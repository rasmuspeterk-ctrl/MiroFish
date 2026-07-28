"""F3 (SPEC §2): Polymarket CLOB-WS, market channel for aktive vinduer.

Subscription er en fast assets_ids-liste per forbindelse, så recorderen kører
én kortlivet forbindelse PER VINDUE: forbind connect_lead_s før åbning
(token-id'er kendes deterministisk via CLOCK-slug → Gamma-opslag, ingen
søgning), luk linger_s efter vinduets udløb. Maks ~2 samtidige forbindelser.
App-heartbeat: tekstframen PING hvert 10. sekund (VERIFIED.md §4.3/docs).
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.request

import structlog

from .. import clock
from ..modes import FeedsCfg
from ..recorder import EventWriter, GapTracker
from .base import WsFeed, loads_or_none

log = structlog.get_logger("f3")


def _lookup_tokens_sync(gamma_url: str, slug: str, timeout: float = 8.0) -> list[str]:
    req = urllib.request.Request(
        f"{gamma_url}/markets?slug={slug}",
        headers={"User-Agent": "bonesaw-recorder/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data:
        return []
    return json.loads(data[0].get("clobTokenIds") or "[]")


class F3WindowFeed(WsFeed):
    name = "f3"

    def __init__(self, writer: EventWriter, gaps: GapTracker, cfg: FeedsCfg,
                 period: int, window_ts: int, token_map: dict[str, list[str]]) -> None:
        super().__init__(writer, gaps, url=cfg.f3_url, ping_text="PING",
                         ping_interval_s=cfg.f3_ping_s,
                         reconnect_base_s=cfg.reconnect_base_s,
                         reconnect_max_s=cfg.reconnect_max_s)
        self.period = period
        self.window_ts = window_ts
        self.token_map = token_map              # asset -> [token_up, token_down]
        self.asset_by_token = {t: a for a, ts in token_map.items() for t in ts}
        self.deadline = window_ts + period + cfg.f3_linger_s

    def should_stop(self) -> bool:
        return self._stopping or time.time() > self.deadline

    async def on_open(self, ws) -> None:
        ids = [t for ts in self.token_map.values() for t in ts]
        await ws.send(json.dumps({"assets_ids": ids, "type": "market"}))

    async def on_message(self, raw: str, ts_wall: float, ts_mono: float) -> None:
        msg = loads_or_none(raw)
        if msg is None:
            return
        events = msg if isinstance(msg, list) else [msg]
        for ev in events:
            if not isinstance(ev, dict) or "event_type" not in ev:
                continue
            asset = self.asset_by_token.get(ev.get("asset_id", ""))
            self.gaps.beat("f3", ts_wall)
            self.writer.put("f3", {"asset": asset, "period": self.period,
                                   "window_ts": self.window_ts, "ev": ev},
                            ts_wall=ts_wall, ts_mono=ts_mono)


class F3Manager:
    """Planlægger vindues-forbindelser: nuværende vindue straks, derefter
    hver kommende boundary connect_lead_s i forvejen (SPEC §2 CLOCK: kender
    næste vindue før det åbner)."""

    def __init__(self, writer: EventWriter, gaps: GapTracker, cfg: FeedsCfg,
                 assets: list[str], periods: list[int]) -> None:
        self.writer = writer
        self.gaps = gaps
        self.cfg = cfg
        self.assets = assets
        self.periods = periods
        self._tasks: set[asyncio.Task] = set()
        self._feeds: list[F3WindowFeed] = []

    async def _tokens_for_window(self, period: int, ts: int) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for a in self.assets:
            slug = clock.slug(a, period, ts)
            for attempt in (1, 2):
                try:
                    toks = await asyncio.to_thread(
                        _lookup_tokens_sync, self.cfg.gamma_url, slug)
                    if toks:
                        out[a] = toks
                    break
                except Exception as e:
                    if attempt == 2:
                        self.writer.put("sys", {"type": "token_lookup_fail",
                                                "slug": slug, "error": str(e)[:200]})
                    else:
                        await asyncio.sleep(1.0)
        return out

    async def _spawn_window(self, period: int, ts: int) -> None:
        token_map = await self._tokens_for_window(period, ts)
        if not token_map:
            self.writer.put("sys", {"type": "f3_window_skipped", "period": period,
                                    "window_ts": ts, "reason": "ingen tokens fundet"})
            return
        feed = F3WindowFeed(self.writer, self.gaps, self.cfg, period, ts, token_map)
        self._feeds.append(feed)
        t = asyncio.create_task(feed.run(), name=f"f3-{period}-{ts}")
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def run(self) -> None:
        # nuvaerende vindue(r) straks
        for period in self.periods:
            await self._spawn_window(period, clock.window_ts(period))
        while True:
            waits = [(clock.next_boundary(p) - self.cfg.f3_connect_lead_s - time.time(), p)
                     for p in self.periods]
            wait_s, period = min(waits)
            if wait_s > 0:
                await asyncio.sleep(wait_s)
            boundary = clock.next_boundary(period)
            await self._spawn_window(period, boundary)
            # sov forbi boundary saa naeste iteration sigter paa det NAESTE vindue
            await asyncio.sleep(max(0.0, boundary - time.time()) + 1.0)
            self._feeds = [f for f in self._feeds if not f.should_stop()]

    def stop(self) -> None:
        for f in self._feeds:
            f.stop()
        for t in self._tasks:
            t.cancel()
