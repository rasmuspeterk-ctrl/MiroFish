"""F1 (SPEC §2): Chainlink via Polymarkets RTDS — settlement-sandheden.

Indeholder SOpenTracker: S_open = første tick med oracle-ts ≥ window boundary.
Gap-regel: intet tick i [boundary, boundary+gap_s] → vinduet UNPRICEABLE,
ingen entries, logges. Afgørelsen bruger ORACLE-timestamps; et vægur-watchdog
(grace) fanger tavs feed. Sen in-window-tick efter afgørelsen logges som
s_open_late, men vinduet FORBLIVER unpriceable (fejlsikker default, SPEC §1.7).
"""
from __future__ import annotations

import json
import time

import structlog

from .. import clock
from ..recorder import EventWriter, GapTracker
from .base import WsFeed, loads_or_none

log = structlog.get_logger("f1")


class SOpenTracker:
    def __init__(self, writer: EventWriter, assets: list[str], periods: list[int],
                 *, gap_s: float = 2.0, grace_ms: int = 500) -> None:
        self.writer = writer
        self.assets = assets
        self.periods = periods
        self.gap_s = gap_s
        self.grace_ms = grace_ms
        # (asset, period, boundary) -> "pending" | "open" | "unpriceable"
        self.state: dict[tuple[str, int, int], str] = {}

    def register_boundary(self, period: int, boundary: int) -> None:
        for a in self.assets:
            self.state.setdefault((a, period, boundary), "pending")

    def on_tick(self, asset: str, oracle_ts_ms: int, value: float) -> None:
        for (a, period, boundary), st in list(self.state.items()):
            if a != asset or st != "pending":
                continue
            b_ms = boundary * 1000
            if oracle_ts_ms < b_ms:
                continue  # tick foer boundary — ikke kvalificerende
            if oracle_ts_ms <= b_ms + self.gap_s * 1000:
                self.state[(a, period, boundary)] = "open"
                self.writer.put("s_open", {
                    "asset": a, "period": period, "window_ts": boundary,
                    "slug": clock.slug(a, period, boundary),
                    "s_open": value, "oracle_ts_ms": oracle_ts_ms,
                    "lag_ms": oracle_ts_ms - b_ms})
            else:
                # foerste kvalificerende tick ligger EFTER gap-vinduet
                self._unpriceable(a, period, boundary, reason="first_tick_after_gap",
                                  oracle_ts_ms=oracle_ts_ms)

    def watchdog(self, now_wall: float | None = None) -> None:
        """Kaldes periodisk: tavst feed → UNPRICEABLE efter gap+grace (vægur)."""
        now = now_wall if now_wall is not None else time.time()
        for (a, period, boundary), st in list(self.state.items()):
            if st == "pending" and now > boundary + self.gap_s + self.grace_ms / 1000:
                self._unpriceable(a, period, boundary, reason="no_tick_by_deadline")
            elif st != "pending" and now > boundary + period + 60:
                del self.state[(a, period, boundary)]  # oprydning

    def _unpriceable(self, asset: str, period: int, boundary: int, **kw) -> None:
        self.state[(asset, period, boundary)] = "unpriceable"
        self.writer.put("unpriceable", {
            "asset": asset, "period": period, "window_ts": boundary,
            "slug": clock.slug(asset, period, boundary), **kw})
        log.warning("unpriceable", asset=asset, window_ts=boundary, **kw)

    def note_late(self, asset: str, oracle_ts_ms: int, value: float) -> None:
        for (a, period, boundary), st in self.state.items():
            b_ms = boundary * 1000
            if (a == asset and st == "unpriceable"
                    and b_ms <= oracle_ts_ms <= b_ms + self.gap_s * 1000):
                self.writer.put("s_open_late", {
                    "asset": a, "period": period, "window_ts": boundary,
                    "s_open_late": value, "oracle_ts_ms": oracle_ts_ms})


class F1Rtds(WsFeed):
    name = "f1"

    def __init__(self, writer: EventWriter, gaps: GapTracker, tracker: SOpenTracker,
                 *, url: str, topic: str, symbols: list[str], ping_s: float,
                 reconnect_base_s: float, reconnect_max_s: float) -> None:
        super().__init__(writer, gaps, url=url, ping_text="PING", ping_interval_s=ping_s,
                         reconnect_base_s=reconnect_base_s, reconnect_max_s=reconnect_max_s)
        self.topic = topic
        self.symbols = set(symbols)
        self.tracker = tracker

    async def on_open(self, ws) -> None:
        # Ufiltreret subscribe (verificeret i M0-proben); per-symbol-filtre viste
        # sig IKKE at levere data i praksis. Vi filtrerer klient-side i stedet.
        await ws.send(json.dumps({"action": "subscribe",
                                  "subscriptions": [{"topic": self.topic, "type": "*"}]}))

    async def on_message(self, raw: str, ts_wall: float, ts_mono: float) -> None:
        msg = loads_or_none(raw)
        if not isinstance(msg, dict) or msg.get("topic") not in (
                self.topic, "prices.crypto.chainlink"):
            return
        p = msg.get("payload") or {}
        sym = p.get("symbol")
        if sym not in self.symbols:
            return
        asset = clock.ASSET_BY_CHAINLINK.get(sym)
        try:
            oracle_ts_ms = int(p["timestamp"])
            value = float(p["value"])
        except (KeyError, TypeError, ValueError):
            return
        self.gaps.beat("f1", ts_wall)
        self.writer.put("f1", {"asset": asset, "symbol": sym, "oracle_ts_ms": oracle_ts_ms,
                               "value": value, "recv_wall": ts_wall},
                        ts_wall=ts_wall, ts_mono=ts_mono)
        if asset:
            self.tracker.on_tick(asset, oracle_ts_ms, value)
            self.tracker.note_late(asset, oracle_ts_ms, value)
