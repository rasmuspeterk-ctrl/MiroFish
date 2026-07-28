"""RECORDER (SPEC §2): append-only event-log, voldgraven.

Hot path skriver KUN lokalt (SPEC §1.4): feeds lægger events på en trådsikker
kø uden at blokere; en dedikeret writer-tråd ejer SQLite-forbindelsen (WAL),
batcher inserts, roterer hele timer til parquet og håndhæver disk-vagten.
Fejlsikker default (SPEC §1.7): overløb droppes og TÆLLES frem for at blokere.
"""
from __future__ import annotations

import json
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
                 flush_ms: int = 250, batch_max: int = 500, queue_max: int = 50_000,
                 rotate_check_s: int = 20, disk_min_free_gb: float = 5.0,
                 disk_resume_free_gb: float = 6.0, disk_check_s: int = 30,
                 on_rotate=None) -> None:
        self.sqlite_file = Path(sqlite_file)
        self.parquet_dir = Path(parquet_dir)
        self.flush_ms = flush_ms
        self.batch_max = batch_max
        self.rotate_check_s = rotate_check_s
        self.disk_min_free_gb = disk_min_free_gb
        self.disk_resume_free_gb = disk_resume_free_gb
        self.disk_check_s = disk_check_s
        self.on_rotate = on_rotate            # callback(hour_ts, stats_rows) efter rotation
        self.q: queue.Queue[Event] = queue.Queue(maxsize=queue_max)
        self.dropped = 0                       # overloeb (fejlsikker: aldrig blokere hot path)
        self.disk_halted = False
        self.dropped_disk = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="event-writer", daemon=True)
        self._last_disk_check = 0.0
        self._last_rotate_check = 0.0

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

    def stop(self, timeout: float = 15.0) -> None:
        self._stop.set()
        self._thread.join(timeout=timeout)

    # ---------------------------------------------------------------- intern

    def _connect(self) -> sqlite3.Connection:
        self.sqlite_file.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.sqlite_file)
        con.executescript(SCHEMA)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    def _run(self) -> None:
        con = self._connect()
        buf: list[Event] = []
        last_flush = time.monotonic()
        while not (self._stop.is_set() and self.q.empty() and not buf):
            timeout = max(0.01, self.flush_ms / 1000 - (time.monotonic() - last_flush))
            try:
                buf.append(self.q.get(timeout=timeout))
                while len(buf) < self.batch_max:
                    buf.append(self.q.get_nowait())
            except queue.Empty:
                pass
            now = time.monotonic()
            if buf and (len(buf) >= self.batch_max
                        or now - last_flush >= self.flush_ms / 1000 or self._stop.is_set()):
                con.executemany(
                    "INSERT INTO events (ts_mono, ts_wall, source, payload) VALUES (?,?,?,?)",
                    [(e.ts_mono, e.ts_wall, e.source,
                      json.dumps(e.payload, separators=(",", ":"), ensure_ascii=False))
                     for e in buf])
                con.commit()
                buf.clear()
                last_flush = now
            if now - self._last_disk_check >= self.disk_check_s:
                self._last_disk_check = now
                self._disk_guard(con)
            if now - self._last_rotate_check >= self.rotate_check_s:
                self._last_rotate_check = now
                try:
                    self.rotate(con)
                except Exception as e:  # rotation maa aldrig vaelte skriveren
                    log.error("rotation_fejl", error=str(e))
        con.commit()
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
        import pyarrow as pa
        import pyarrow.parquet as pq

        sel = con.execute(
            "SELECT ts_mono, ts_wall, source, payload FROM events "
            "WHERE ts_wall >= ? AND ts_wall < ? ORDER BY id",
            (hour_ts, hour_ts + 3600)).fetchall()
        if not sel:
            return []
        day = time.strftime("%Y-%m-%d", time.gmtime(hour_ts))
        hh = time.strftime("%H", time.gmtime(hour_ts))
        out_dir = self.parquet_dir / day
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"events_{day}_{hh}.parquet"
        table = pa.table({
            "ts_mono": pa.array([r[0] for r in sel], pa.float64()),
            "ts_wall": pa.array([r[1] for r in sel], pa.float64()),
            "source": pa.array([r[2] for r in sel], pa.string()),
            "payload": pa.array([r[3] for r in sel], pa.string()),
        })
        pq.write_table(table, out, compression="zstd")
        stats: dict[str, list[int]] = {}
        for _, _, source, payload in sel:
            s = stats.setdefault(source, [0, 0])
            s[0] += 1
            s[1] += len(payload)
        stat_rows = [(hour_ts, src, n, b) for src, (n, b) in sorted(stats.items())]
        con.executemany(
            "INSERT OR REPLACE INTO hourly_stats (hour_ts, source, n, bytes) VALUES (?,?,?,?)",
            stat_rows)
        con.execute("DELETE FROM events WHERE ts_wall >= ? AND ts_wall < ?",
                    (hour_ts, hour_ts + 3600))
        con.commit()
        log.info("roteret", hour=f"{day}T{hh}Z", rows=len(sel), file=str(out))
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
