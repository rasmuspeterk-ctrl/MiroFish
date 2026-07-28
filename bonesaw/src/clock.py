"""CLOCK (SPEC §2): vinduesmatematik og slugs. Kender næste vindue før det
åbner — ingen markedssøgning nogensinde."""
from __future__ import annotations

import time
from dataclasses import dataclass

PERIOD_5M = 300
PERIOD_15M = 900

# symbol-afledninger per asset (verificeret i M0/VERIFIED.md)
CHAINLINK_SYMBOL = {a: f"{a}/usd" for a in ("btc", "eth", "sol", "xrp", "doge", "bnb")}
BINANCE_SYMBOL = {a: f"{a}usdt" for a in ("btc", "eth", "sol", "xrp", "doge", "bnb")}
ASSET_BY_CHAINLINK = {v: k for k, v in CHAINLINK_SYMBOL.items()}


def horizon(period: int) -> str:
    return {PERIOD_5M: "5m", PERIOD_15M: "15m"}[period]


def window_ts(period: int, now: float | None = None) -> int:
    """window_ts = now - (now % period) (SPEC §2)."""
    t = int(now if now is not None else time.time())
    return t - (t % period)


def slug(asset: str, period: int, ts: int) -> str:
    return f"{asset}-updown-{horizon(period)}-{ts}"


def next_boundary(period: int, now: float | None = None) -> int:
    return window_ts(period, now) + period


@dataclass(frozen=True)
class Window:
    asset: str
    period: int
    ts: int          # aabning (unix, UTC)

    @property
    def end_ts(self) -> int:
        return self.ts + self.period

    @property
    def slug(self) -> str:
        return slug(self.asset, self.period, self.ts)


def periods(enable_15m: bool) -> list[int]:
    return [PERIOD_5M, PERIOD_15M] if enable_15m else [PERIOD_5M]
