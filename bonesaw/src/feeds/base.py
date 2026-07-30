"""Fælles genforbindende WS-feed: eksponentiel backoff, app-heartbeat,
sys-events for connect/disconnect (indgår i soak-rapportens gap-regnskab)."""
from __future__ import annotations

import asyncio
import json
import random
import time

import structlog
import websockets

from ..recorder import EventWriter, GapTracker

log = structlog.get_logger("feed")


class WsFeed:
    """Underklasser sætter name/url og implementerer on_open()/on_message()."""

    name = "feed"

    def __init__(self, writer: EventWriter, gaps: GapTracker, *,
                 url: str, ping_text: str | None = None, ping_interval_s: float = 0,
                 reconnect_base_s: float = 0.5, reconnect_max_s: float = 30.0) -> None:
        self.writer = writer
        self.gaps = gaps
        self.url = url
        self.ping_text = ping_text
        self.ping_interval_s = ping_interval_s
        self.reconnect_base_s = reconnect_base_s
        self.reconnect_max_s = reconnect_max_s
        self._stopping = False

    # underklasse-hooks -------------------------------------------------
    async def on_open(self, ws) -> None:  # subscribe-frames m.m.
        return

    async def on_message(self, raw: str, ts_wall: float, ts_mono: float) -> None:
        raise NotImplementedError

    def should_stop(self) -> bool:  # per-vindue-feeds (F3) overstyrer
        return self._stopping

    # kørsel -------------------------------------------------------------
    def sys_event(self, type_: str, **kw) -> None:
        self.writer.put("sys", {"type": type_, "feed": self.name, **kw})

    HEALTHY_CONN_S = 30.0  # forbindelse skal leve saa laenge foer backoff nulstilles

    async def run(self) -> None:
        backoff = self.reconnect_base_s
        while not self.should_stop():
            connected_at = None
            try:
                async with websockets.connect(self.url, open_timeout=15,
                                              max_size=2**23) as ws:
                    self.sys_event("ws_connect", url=self.url)
                    # AUDIT-fix: backoff nulstilles IKKE ved handshake alene —
                    # en server der accepterer og straks lukker ville ellers
                    # give en 1-2 Hz reconnect-storm. Se except-grenen.
                    connected_at = time.monotonic()
                    await self.on_open(ws)
                    next_ping = time.monotonic() + (self.ping_interval_s or 1e12)
                    while not self.should_stop():
                        now = time.monotonic()
                        if self.ping_text and now >= next_ping:
                            await ws.send(self.ping_text)
                            next_ping = now + self.ping_interval_s
                        try:
                            raw = await asyncio.wait_for(
                                ws.recv(), timeout=max(0.05, min(1.0, next_ping - now)))
                        except asyncio.TimeoutError:
                            continue
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", errors="replace")
                        if raw in ("PONG", "PING", "pong"):
                            self.gaps.beat(self.name)
                            continue
                        await self.on_message(raw, time.time(), time.monotonic())
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self.should_stop():
                    break
                self.sys_event("ws_disconnect", error=str(e)[:300])
                log.warning("ws_disconnect", feed=self.name, error=str(e)[:120])
                if connected_at is not None and \
                        time.monotonic() - connected_at >= self.HEALTHY_CONN_S:
                    backoff = self.reconnect_base_s  # sund forbindelse gik ned: frisk start
                await asyncio.sleep(backoff + random.uniform(0, backoff / 2))
                backoff = min(backoff * 2, self.reconnect_max_s)
        self.sys_event("ws_stopped")

    def stop(self) -> None:
        self._stopping = True


def loads_or_none(raw: str):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
