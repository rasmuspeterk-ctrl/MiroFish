"""BONESAW entrypoint. Mode-flaget styrer alt (SPEC §1.1):

  RECORD → recorder-pipelinen (M1)
  SHADOW → ikke implementeret før M4
  LIVE   → hård gate-kontrol (SPEC §7) FØR alt andet; derefter M5 (ikke bygget)

Kørsel:  python3 -m src.main --config config.yaml [--duration-s N]
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import signal
import sys
import time
from pathlib import Path

import structlog

from . import clock
from .feeds.f1_rtds import F1Rtds, SOpenTracker
from .feeds.f2_binance import F2Binance
from .feeds.f3_clob import F3Manager
from .modes import Config, Mode, check_gates, load_config
from .recorder import EventWriter, GapTracker, rss_mb
from .uploader import run_uploader

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REGISTER_LEAD_S = 5.0   # registrér boundary lidt før, så første oracle-tick ≥ boundary fanges

log = structlog.get_logger("main")


def _setup_logging() -> None:
    structlog.configure(processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.KeyValueRenderer(key_order=["timestamp", "level", "event"]),
    ])


async def _boundary_task(tracker: SOpenTracker, periods: list[int]) -> None:
    while True:
        waits = [(clock.next_boundary(p) - REGISTER_LEAD_S - time.time(), p) for p in periods]
        wait_s, period = min(waits)
        if wait_s > 0:
            await asyncio.sleep(wait_s)
        boundary = clock.next_boundary(period)
        tracker.register_boundary(period, boundary)
        await asyncio.sleep(max(0.0, boundary - time.time()) + 0.5)


async def _watchdog_task(tracker: SOpenTracker) -> None:
    while True:
        tracker.watchdog()
        await asyncio.sleep(0.5)


async def _metrics_task(writer: EventWriter, gaps: GapTracker, data_dir: Path,
                        interval_s: int) -> None:
    while True:
        await asyncio.sleep(interval_s)
        now = time.time()
        beats = {k: round(now - v, 1) for k, v in gaps.snapshot().items()}
        writer.put("sys", {
            "type": "metrics", "rss_mb": rss_mb(), "qsize": writer.q.qsize(),
            "dropped": writer.dropped, "dropped_disk": writer.dropped_disk,
            "disk_halted": writer.disk_halted,
            "free_gb": round(shutil.disk_usage(data_dir).free / 1e9, 2),
            "beat_age_s": beats})


async def run_record(cfg: Config, duration_s: float | None) -> None:
    data_dir = (PROJECT_ROOT / cfg.recorder.data_dir).resolve()
    parquet_dir = data_dir / "parquet"
    data_dir.mkdir(parents=True, exist_ok=True)
    writer = EventWriter(
        PROJECT_ROOT / cfg.recorder.sqlite_file, parquet_dir,
        flush_ms=cfg.recorder.flush_ms, batch_max=cfg.recorder.batch_max,
        queue_max=cfg.recorder.queue_max, rotate_check_s=cfg.recorder.rotate_check_s,
        rotate_chunk_rows=cfg.recorder.rotate_chunk_rows,
        disk_min_free_gb=cfg.recorder.disk_min_free_gb,
        disk_resume_free_gb=cfg.recorder.disk_resume_free_gb,
        disk_check_s=cfg.recorder.disk_check_s)
    writer.start()
    writer.put("sys", {"type": "recorder_start", "mode": cfg.mode.value,
                       "assets": cfg.assets_record, "enable_15m": cfg.enable_15m})
    gaps = GapTracker()
    pers = clock.periods(cfg.enable_15m)
    tracker = SOpenTracker(writer, cfg.assets_record, pers,
                           gap_s=cfg.feeds.s_open_gap_s, grace_ms=cfg.feeds.s_open_grace_ms)
    f1 = F1Rtds(writer, gaps, tracker, url=cfg.feeds.f1_url, topic=cfg.feeds.f1_topic,
                symbols=[clock.CHAINLINK_SYMBOL[a] for a in cfg.assets_record],
                ping_s=cfg.feeds.f1_ping_s, reconnect_base_s=cfg.feeds.reconnect_base_s,
                reconnect_max_s=cfg.feeds.reconnect_max_s)
    f2 = F2Binance(writer, gaps, base_url=cfg.feeds.f2_url, streams=cfg.feeds.f2_streams,
                   assets=cfg.assets_record, reconnect_base_s=cfg.feeds.reconnect_base_s,
                   reconnect_max_s=cfg.feeds.reconnect_max_s)
    f3 = F3Manager(writer, gaps, cfg.feeds, cfg.assets_record, pers)

    tasks = [
        asyncio.create_task(f1.run(), name="f1"),
        asyncio.create_task(f2.run(), name="f2"),
        asyncio.create_task(f3.run(), name="f3-manager"),
        asyncio.create_task(_boundary_task(tracker, pers), name="boundaries"),
        asyncio.create_task(_watchdog_task(tracker), name="watchdog"),
        asyncio.create_task(_metrics_task(writer, gaps, data_dir,
                                          cfg.recorder.metrics_interval_s), name="metrics"),
    ]
    if cfg.recorder.supabase_upload:
        tasks.append(asyncio.create_task(
            run_uploader(PROJECT_ROOT / cfg.recorder.sqlite_file,
                         cfg.recorder.supabase_table), name="uploader"))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    log.info("recorder_koerer", assets=cfg.assets_record, periods=pers,
             duration_s=duration_s or "until-signal")
    try:
        if duration_s:
            await asyncio.wait_for(stop.wait(), timeout=duration_s)
        else:
            await stop.wait()
    except asyncio.TimeoutError:
        pass
    log.info("stopper")
    # stop-tidspunktet markeres FØR teardown: soak-rapporten capper gap-målingen
    # her, så nedluknings-halen ikke tæller som feed-stilhed
    writer.put("sys", {"type": "recorder_stopping"})
    f1.stop()
    f2.stop()
    f3.stop()
    for t in tasks:
        t.cancel()
    # bounded teardown: et token-opslag fanget i to_thread kan ikke afbrydes;
    # vent kort og lad daemon-tråde dø med processen i stedet for at hænge
    await asyncio.wait(tasks, timeout=5)
    writer.put("sys", {"type": "recorder_stop", "dropped": writer.dropped,
                       "dropped_disk": writer.dropped_disk})
    writer.stop()


def main() -> int:
    ap = argparse.ArgumentParser(description="BONESAW v1")
    ap.add_argument("--config", default=str(PROJECT_ROOT / "config.yaml"))
    ap.add_argument("--duration-s", type=float, default=None,
                    help="stop automatisk efter N sekunder (smoke/soak-styring)")
    args = ap.parse_args()
    _setup_logging()
    cfg = load_config(args.config)

    if cfg.mode is Mode.LIVE:
        check_gates(PROJECT_ROOT)  # SPEC §7 — exit(1) hvis gates mangler/fejler
        print("Gates OK, men LIVE-eksekvering er ikke bygget endnu (M5).", file=sys.stderr)
        return 2
    if cfg.mode is Mode.SHADOW:
        print("SHADOW er ikke bygget endnu (M4).", file=sys.stderr)
        return 2

    asyncio.run(run_record(cfg, args.duration_s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
