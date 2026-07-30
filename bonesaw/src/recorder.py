"""RECORDER (SPEC §2): append-only event-log, voldgraven.

Hot path skriver KUN lokalt (SPEC §1.4): feeds lægger events på en trådsikker
kø uden at blokere; en dedikeret writer-tråd ejer SQLite-forbindelsen (WAL),
batcher inserts, roterer hele timer til parquet og håndhæver disk-vagten.
Fejlsikker default (SPEC §1.7): overløb droppes og TÆLLES frem for at blokere.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger("recorder")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id      INTEGER PRIMARY KEY,
  ts_mono REAL NOT NULL,
  ts_wall REAL NOT NULL,
  source  TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts_wall ON events (ts_wall);
CREATE TABLE IF NOT EXISTS hourly_stats (
  hour_ts  INTEGER NOT NULL,
  source   TEXT NOT NULL,
  n        INTEGER NOT NULL,
  bytes    INTEGER NOT NULL,
  uploaded INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (hour_ts, source)
);
"""


@dataclass
class Event:
    ts_mono: float
    ts_wall: float
    source: str
    payload: dict


class EventWriter:
    """Trådsikker skriver. put() er ikke-blokerende og kaldes fra event-loopet."""

    def __init__(self, sqlite_file: str | Path, parquet_dir: str | Path, *,
                 flush_ms: int = 200, batch_max: int = 2000, queue_max: int = 200_000,
                 rotate_check_s: int = 20, rotate_chunk_rows: int = 100_000,
                 disk_min_free_gb: float = 5.0,
                 disk_resume_free_gb: float = 6.0, disk_check_s: int = 30,
                 on_rotate=None) -> None:
        self.sqlite_file = Path(sqlite_file)
        self.parquet_dir = Path(parquet_dir)
        self.flush_ms = flush_ms
        self.batch_max = batch_max
        self.rotate_check_s = rotate_check_s
        self.rotate_chunk_rows = rotate_chunk_rows
        self.disk_min_free_gb = disk_min_free_gb
        self.disk_resume_free_gb = disk_resume_free_gb
        self.disk_check_s = disk_check_s
        self.on_rotate = on_rotate            # callback(hour_ts, stats_rows) efter rotation
        self.q: queue.Queue[Event] = queue.Queue(maxsize=queue_max)
        self.dropped = 0                       # overloeb (fejlsikker: aldrig blokere hot path)
        self.db_errors = 0                     # db-flush-fejl overlevet (synlig i metrics)
        self._db_error_streak = 0
        self.disk_halted = False
        self.dropped_disk = 0
        self._stop = threading.Event()
        # rotation koerer i EGEN traad med EGEN forbindelse: en times-rotation
        # (millioner raekker ved f2-tempo) maa aldrig stalle hot path-skriveren.
        self._thread = threading.Thread(target=self._run, name="event-writer", daemon=True)
        self._rot_thread = threading.Thread(target=self._rotation_loop,
                                            name="event-rotator", daemon=True)
        self._last_disk_check = 0.0

    # ---------------------------------------------------------------- API

    def put(self, source: str, payload: dict, *, ts_wall: float | None = None,
            ts_mono: float | None = None) -> None:
        if self.disk_halted:
            self.dropped_disk += 1
            return
        ev = Event(ts_mono if ts_mono is not None else time.monotonic(),
                   ts_wall if ts_wall is not None else time.time(), source, payload)
        try:
            self.q.put_nowait(ev)
        except queue.Full:
            self.dropped += 1

    def start(self) -> None:
        self._thread.start()
        self._rot_thread.start()

    def stop(self, timeout: float = 60.0) -> None:
        self._stop.set()
        self._thread.join(timeout=timeout)
        self._rot_thread.join(timeout=timeout)
        # AUDIT-fix: opgivet join må ikke være STILLE — daemon-tråde fryses ved
        # exit, og et efterladt backlog skal være synligt i loggen
        if self._thread.is_alive():
            log.error("writer_traad_stadig_i_live_ved_stop", qsize=self.q.qsize())
        if self._rot_thread.is_alive():
            log.error("rotations_traad_stadig_i_live_ved_stop")

    # ---------------------------------------------------------------- intern

    def _connect(self) -> sqlite3.Connection:
        self.sqlite_file.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.sqlite_file)
        con.executescript(SCHEMA)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA busy_timeout=5000")  # writer/rotator deler WAL-fil
        return con

    def _run(self) -> None:
        con = self._connect()
        buf: list[Event] = []
        last_flush = time.monotonic()
        db_retry_at = 0.0
        while not (self._stop.is_set() and self.q.empty() and not buf):
            now = time.monotonic()
            if len(buf) < self.batch_max and now >= db_retry_at:
                timeout = max(0.01, self.flush_ms / 1000 - (now - last_flush))
                try:
                    buf.append(self.q.get(timeout=timeout))
                    while len(buf) < self.batch_max:
                        buf.append(self.q.get_nowait())
                except queue.Empty:
                    pass
            else:
                # buf fuld eller db i backoff: traek ikke mere fra koeen — den
                # absorberer (200k) og overloeb TAELLES i stedet for at buf
                # vokser ubegraenset mod en syg database
                time.sleep(min(0.2, max(0.01, db_retry_at - now)))
            now = time.monotonic()
            if buf and now >= db_retry_at and (len(buf) >= self.batch_max
                        or now - last_flush >= self.flush_ms / 1000 or self._stop.is_set()):
                try:
                    con.executemany(
                        "INSERT INTO events (ts_mono, ts_wall, source, payload) VALUES (?,?,?,?)",
                        [(e.ts_mono, e.ts_wall, e.source,
                          json.dumps(e.payload, separators=(",", ":"), ensure_ascii=False))
                         for e in buf])
                    con.commit()
                    buf.clear()
                    self._db_error_streak = 0
                except sqlite3.Error as e:
                    # AUDIT-fix (critical): én OperationalError (fx 'database is
                    # locked' under rotationens WAL-checkpoint) må ALDRIG dræbe
                    # writer-tråden. Behold buf (re-flushes), rollback, backoff.
                    try:
                        con.rollback()
                    except sqlite3.Error:
                        pass
                    self.db_errors += 1
                    self._db_error_streak += 1
                    db_retry_at = now + min(5.0, 0.5 * 2 ** min(self._db_error_streak, 4))
                    log.error("db_flush_fejl", error=str(e)[:200], buffered=len(buf),
                              streak=self._db_error_streak)
                    if self._stop.is_set() and self._db_error_streak >= 3:
                        self.dropped += len(buf)  # sidste udvej ved nedlukning: tab TALT
                        buf.clear()
                last_flush = now
            if now - self._last_disk_check >= self.disk_check_s:
                self._last_disk_check = now
                try:
                    self._disk_guard(con)
                except sqlite3.Error as e:
                    log.error("disk_guard_db_fejl", error=str(e)[:200])
        try:
            con.commit()
        except sqlite3.Error:
            pass
        con.close()

    def _rotation_loop(self) -> None:
        con = self._connect()
        while not self._stop.wait(timeout=self.rotate_check_s):
            try:
                self.rotate(con)
            except Exception as e:  # rotation maa aldrig vaelte recorderen
                log.error("rotation_fejl", error=str(e))
        con.close()

    def _disk_guard(self, con: sqlite3.Connection) -> None:
        """SPEC §2 RECORDER: stop ved < disk_min_free_gb fri (med hysterese)."""
        free_gb = shutil.disk_usage(self.parquet_dir if self.parquet_dir.exists()
                                    else self.sqlite_file.parent).free / 1e9
        if not self.disk_halted and free_gb < self.disk_min_free_gb:
            self.disk_halted = True
            con.execute("INSERT INTO events (ts_mono, ts_wall, source, payload) VALUES (?,?,?,?)",
                        (time.monotonic(), time.time(), "sys",
                         json.dumps({"type": "disk_halt", "free_gb": round(free_gb, 2)})))
            con.commit()
            log.error("disk_halt", free_gb=round(free_gb, 2))
        elif self.disk_halted and free_gb >= self.disk_resume_free_gb:
            self.disk_halted = False
            con.execute("INSERT INTO events (ts_mono, ts_wall, source, payload) VALUES (?,?,?,?)",
                        (time.monotonic(), time.time(), "sys",
                         json.dumps({"type": "disk_resume", "free_gb": round(free_gb, 2),
                                     "dropped_while_halted": self.dropped_disk})))
            con.commit()
            log.info("disk_resume", free_gb=round(free_gb, 2))

    def rotate(self, con: sqlite3.Connection, *, now: float | None = None) -> list[tuple]:
        """Flyt alle HELE timer ældre end den igangværende til parquet.

        SQLite beholder kun seneste time (SPEC §2). Returnerer stats-rækker
        [(hour_ts, source, n, bytes)] for de roterede timer.
        """
        cur_hour = int((now if now is not None else time.time()) // 3600) * 3600
        rows = con.execute(
            "SELECT DISTINCT CAST(ts_wall/3600 AS INTEGER)*3600 FROM events WHERE ts_wall < ?",
            (cur_hour,)).fetchall()
        all_stats: list[tuple] = []
        for (hour_ts,) in sorted(rows):
            stats = self._rotate_hour(con, int(hour_ts))
            all_stats.extend(stats)
            if self.on_rotate:
                try:
                    self.on_rotate(int(hour_ts), stats)
                except Exception as e:
                    log.error("on_rotate_fejl", error=str(e))
        if rows:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return all_stats

    def _rotate_hour(self, con: sqlite3.Connection, hour_ts: int) -> list[tuple]:
        """Chunked streaming med crash-sikker rækkefølge (AUDIT-fix, critical):

          1. SELECT-chunks via id-CURSOR (id > last_id) → parquet-row-groups
             i en .tmp-fil — INGEN sletning undervejs.
          2. Når hele timen er skrevet: close() (footer), fsync, atomisk
             os.replace(.tmp → endelig fil), fsync af mappen.
          3. FØRST DEREFTER chunkede DELETEs (korte WAL-låse).

        Dør processen på et vilkårligt tidspunkt før (3), står alle rækker
        stadig i SQLite og næste rotation genskriver .tmp-filen idempotent —
        den endelige fil åbnes ALDRIG i write-mode, så en tidligere komplet
        rotation kan ikke trunkeres af en genkørsel.
        """
        import pyarrow as pa
        import pyarrow.parquet as pq

        day = time.strftime("%Y-%m-%d", time.gmtime(hour_ts))
        hh = time.strftime("%H", time.gmtime(hour_ts))
        out_dir = self.parquet_dir / day
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"events_{day}_{hh}.parquet"
        tmp = out_dir / f"events_{day}_{hh}.parquet.tmp"
        writer: pq.ParquetWriter | None = None
        stats: dict[str, list[int]] = {}
        total = 0
        last_id = 0
        try:
            while True:
                sel = con.execute(
                    "SELECT id, ts_mono, ts_wall, source, payload FROM events "
                    "WHERE id > ? AND ts_wall >= ? AND ts_wall < ? ORDER BY id LIMIT ?",
                    (last_id, hour_ts, hour_ts + 3600, self.rotate_chunk_rows)).fetchall()
                if not sel:
                    break
                table = pa.table({
                    "ts_mono": pa.array([r[1] for r in sel], pa.float64()),
                    "ts_wall": pa.array([r[2] for r in sel], pa.float64()),
                    "source": pa.array([r[3] for r in sel], pa.string()),
                    "payload": pa.array([r[4] for r in sel], pa.string()),
                })
                if writer is None:
                    writer = pq.ParquetWriter(tmp, table.schema, compression="zstd")
                writer.write_table(table)
                for _, _, _, source, payload in sel:
                    s = stats.setdefault(source, [0, 0])
                    s[0] += 1
                    s[1] += len(payload)
                total += len(sel)
                last_id = sel[-1][0]
        finally:
            if writer is not None:
                writer.close()
        if not total:
            tmp.unlink(missing_ok=True)
            return []
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, out)
        dfd = os.open(out_dir, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
        while True:  # sletning EFTER holdbar fil; chunket for korte laase
            cur = con.execute(
                "DELETE FROM events WHERE id IN (SELECT id FROM events WHERE id <= ? "
                "AND ts_wall >= ? AND ts_wall < ? LIMIT ?)",
                (last_id, hour_ts, hour_ts + 3600, self.rotate_chunk_rows))
            con.commit()
            if cur.rowcount < self.rotate_chunk_rows:
                break
        stat_rows = [(hour_ts, src, n, b) for src, (n, b) in sorted(stats.items())]
        con.executemany(
            "INSERT OR REPLACE INTO hourly_stats (hour_ts, source, n, bytes) VALUES (?,?,?,?)",
            stat_rows)
        con.commit()
        log.info("roteret", hour=f"{day}T{hh}Z", rows=total, file=str(out))
        return stat_rows


class GapTracker:
    """Sidste-livstegn per kilde — grundlag for soak-rapportens gap-tid."""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def beat(self, source: str, ts: float | None = None) -> None:
        with self._lock:
            self._last[source] = ts if ts is not None else time.time()

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            return dict(self._last)


def rss_mb() -> float | None:
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        return None
    return None
