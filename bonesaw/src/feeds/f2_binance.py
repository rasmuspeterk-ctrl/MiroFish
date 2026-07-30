"""F2 (SPEC §2): Binance USDⓈ-M futures — det hurtige øje.

bookTicker + aggTrade + forceOrder (likvidationer) + depth@100ms (til OFI)
for alle recorder-assets via ét combined stream. OBS (M1-fund): fstream er
geo-blokeret fra US-egress (HTTP 451) — feedet genforbinder tålmodigt og
gap-tiden bliver synlig i soak-rapporten i stedet for at vælte processen.
"""
from __future__ import annotations

from .. import clock
from ..recorder import EventWriter, GapTracker
from .base import WsFeed, loads_or_none


class F2Binance(WsFeed):
    name = "f2"

    def __init__(self, writer: EventWriter, gaps: GapTracker, *, base_url: str,
                 streams: list[str], assets: list[str],
                 reconnect_base_s: float, reconnect_max_s: float) -> None:
        parts = [f"{clock.BINANCE_SYMBOL[a]}@{s}" for a in assets for s in streams]
        url = f"{base_url}?streams={'/'.join(parts)}"
        super().__init__(writer, gaps, url=url,
                         reconnect_base_s=reconnect_base_s, reconnect_max_s=reconnect_max_s)

    async def on_message(self, raw: str, ts_wall: float, ts_mono: float) -> None:
        msg = loads_or_none(raw)
        if not isinstance(msg, dict) or "stream" not in msg:
            return
        self.gaps.beat("f2", ts_wall)
        self.writer.put("f2", {"stream": msg.get("stream"), "d": msg.get("data")},
                        ts_wall=ts_wall, ts_mono=ts_mono)
