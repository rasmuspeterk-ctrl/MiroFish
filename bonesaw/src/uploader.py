"""Valgfri async batch-upload af time-aggregater til Supabase (SPEC M1).

Kører HELT uden for hot path (SPEC §1.4): egen SQLite-forbindelse, egen task,
poll af hourly_stats hvor uploaded=0. Fejl logges og forsøges igen næste
cyklus — aldrig exceptions ud af tasken. Kræver SUPABASE_URL + SUPABASE_KEY
i miljøet (.env, gitignored); mangler de, er uploaderen en no-op.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import urllib.request
from pathlib import Path

import structlog

log = structlog.get_logger("uploader")


def _upload_sync(sqlite_file: Path, table: str) -> int:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        return 0
    con = sqlite3.connect(sqlite_file, timeout=10)
    try:
        rows = con.execute(
            "SELECT hour_ts, source, n, bytes FROM hourly_stats WHERE uploaded=0 "
            "ORDER BY hour_ts LIMIT 500").fetchall()
        if not rows:
            return 0
        body = json.dumps([{"hour_ts": h, "source": s, "n": n, "bytes": b}
                           for h, s, n, b in rows]).encode("utf-8")
        req = urllib.request.Request(
            f"{url}/rest/v1/{table}", data=body, method="POST",
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status not in (200, 201, 204):
                raise RuntimeError(f"HTTP {resp.status}")
        con.executemany(
            "UPDATE hourly_stats SET uploaded=1 WHERE hour_ts=? AND source=?",
            [(h, s) for h, s, _, _ in rows])
        con.commit()
        return len(rows)
    finally:
        con.close()


async def run_uploader(sqlite_file: Path, table: str, interval_s: int = 120) -> None:
    while True:
        try:
            n = await asyncio.to_thread(_upload_sync, sqlite_file, table)
            if n:
                log.info("uploadet", rows=n)
        except Exception as e:  # fejltolerant per spec — proev igen naeste cyklus
            log.warning("upload_fejl", error=str(e)[:200])
        await asyncio.sleep(interval_s)
