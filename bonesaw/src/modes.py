"""Mode-flag og config (SPEC §1.1, §1.5, §5) + gate-mekanik (SPEC §7).

Gate-kontrollen i check_gates() er en hård kodesti: den læser KUN fra
gates/-mappen og kan ikke slås fra eller omdirigeres via config.
"""
from __future__ import annotations

import enum
import json
import os
import sys
import time
from pathlib import Path

import yaml
from pydantic import BaseModel as _PydanticBase
from pydantic import ConfigDict, Field


class BaseModel(_PydanticBase):
    """AUDIT-fix: extra='forbid' — en fejlstavet/malplaceret config-nøgle skal
    FEJLE ved load, ikke stille falde tilbage på pydantic-defaulten
    (fejl-sikker default, SPEC §1.7; alle tærskler i config, §1.5)."""
    model_config = ConfigDict(extra="forbid")


class Mode(str, enum.Enum):
    RECORD = "RECORD"
    SHADOW = "SHADOW"
    LIVE = "LIVE"


class BrainCfg(BaseModel):
    min_bin_n: int = 200
    rv_halflife_s: int = 60


class HandsCfg(BaseModel):
    edge_min: float = 0.02
    pairing_edge_min: float = 0.005
    pairing_max_s: int = 60
    oneside_cap_usdc: float = 25
    ratio_min: float = 1.5
    ratio_max: float = 8.0
    paired_cost_max: float = 1.03
    probe_max_usdc: float = 10
    endgame_cutoff_s: int = 20
    endgame_edge_min: float = 0.08


class FusesCfg(BaseModel):
    daily_loss_pct: float = 5
    oracle_div_bp: float = 10
    oracle_div_s: float = 5
    market_cap_usdc: float = 50
    blackout_min: int = 10
    staleness_ms: int = 1500


class RedeemerCfg(BaseModel):
    redeem_poll_s: int = 60
    redeem_backlog_max_usdc: float = 200


class ExecutionCfg(BaseModel):
    latency_penalty_ms: list[int] = Field(default_factory=lambda: [250, 500])


class RecorderCfg(BaseModel):
    data_dir: str = "data"
    sqlite_file: str = "data/recorder.sqlite"
    flush_ms: int = 200
    batch_max: int = 2000
    queue_max: int = 200_000         # fejlsikker: hot path blokerer aldrig; overloeb droppes + taelles
    rotate_check_s: int = 20
    rotate_chunk_rows: int = 100_000
    disk_min_free_gb: float = 5.0    # SPEC §2 RECORDER: stop ved <5 GB fri
    disk_resume_free_gb: float = 6.0  # hysterese for genoptagelse
    disk_check_s: int = 30
    metrics_interval_s: int = 60
    supabase_upload: bool = False    # valgfri (SPEC M1); kraever SUPABASE_URL/SUPABASE_KEY i .env
    supabase_table: str = "bonesaw_hourly_stats"


class StaleGapCfg(BaseModel):
    f1: float = 5.0
    f2: float = 5.0
    f3: float = 15.0


class FeedsCfg(BaseModel):
    f1_url: str = "wss://ws-live-data.polymarket.com"
    f1_topic: str = "crypto_prices_chainlink"
    f1_ping_s: float = 5.0            # RTDS app-heartbeat (VERIFIED.md §4.3)
    s_open_gap_s: float = 2.0         # SPEC §2 F1 gap-regel: [boundary, boundary+2s] (oracle-ts)
    s_open_grace_ms: int = 3000       # vaeg-urs-slack for LEVERING foer UNPRICEABLE afgoeres;
                                      # reglen selv er uaendret — kun afgoerelsen venter paa
                                      # relay-lag (maalt p99 ~2,6s fra containeren)
    f2_url: str = "wss://fstream.binance.com/stream"
    f2_streams: list[str] = Field(
        default_factory=lambda: ["bookTicker", "aggTrade", "forceOrder", "depth@100ms"])
    f3_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    f3_ping_s: float = 10.0           # CLOB-WS app-heartbeat (docs)
    f3_connect_lead_s: int = 30       # forbind foer vinduet aabner
    f3_linger_s: int = 30             # hold aabent efter vinduet lukker
    gamma_url: str = "https://gamma-api.polymarket.com"
    reconnect_base_s: float = 0.5
    reconnect_max_s: float = 30.0
    stale_gap_s: StaleGapCfg = Field(default_factory=StaleGapCfg)


class SoakCfg(BaseModel):
    gap_pct_max: float = 0.1          # SPEC M1 DoD: <0,1% gap-tid per feed
    mem_growth_max_pct: float = 15.0  # RSS-vaekst sidste time vs foerste time


class Config(BaseModel):
    mode: Mode = Mode.RECORD
    assets_record: list[str] = Field(
        default_factory=lambda: ["btc", "eth", "sol", "xrp", "doge", "bnb"])
    asset_live: str | None = None
    enable_15m: bool = False
    brain: BrainCfg = Field(default_factory=BrainCfg)
    hands: HandsCfg = Field(default_factory=HandsCfg)
    fuses: FusesCfg = Field(default_factory=FusesCfg)
    redeemer: RedeemerCfg = Field(default_factory=RedeemerCfg)
    execution: ExecutionCfg = Field(default_factory=ExecutionCfg)
    recorder: RecorderCfg = Field(default_factory=RecorderCfg)
    feeds: FeedsCfg = Field(default_factory=FeedsCfg)
    soak: SoakCfg = Field(default_factory=SoakCfg)


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return Config.model_validate(raw)


def load_dotenv(path: str | Path) -> int:
    """Minimal .env-læser (KEY=VAL per linje; #-kommentarer; eksisterende
    miljøvariabler vinder). Bevidst uden ekstra dependency — hemmeligheder
    bor KUN i .env, gitignored (SPEC §6). Returnerer antal satte nøgler."""
    p = Path(path)
    if not p.exists():
        return 0
    n = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
            n += 1
    return n


ARM_MAX_AGE_S = 7 * 24 * 3600  # SPEC §7: ARM aeldre end 7 dage → exit(1)


def check_gates(project_root: Path) -> None:
    """SPEC §7: LIVE naegter at starte uden gyldige gate-filer. exit(1) ellers.

    Kaldes KUN fra LIVE-kodestien. Ingen config-parametre — bevidst.
    """
    gates = project_root / "gates"
    problems: list[str] = []
    for name in ("gate_a.json", "gate_b.json"):
        p = gates / name
        if not p.exists():
            problems.append(f"mangler {p}")
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            problems.append(f"{p} ulæselig: {e}")
            continue
        if doc.get("pass") is not True:
            problems.append(f"{p}: pass er ikke true")
    arm = gates / "ARM"
    if not arm.exists():
        problems.append(f"mangler {arm} (skrives KUN af mennesket, SPEC §6)")
    else:
        age = time.time() - arm.stat().st_mtime
        if age > ARM_MAX_AGE_S:
            problems.append(f"{arm} er {age / 86400:.1f} dage gammel (> 7 dage)")
    if problems:
        print("LIVE AFVIST — gate-kontrol fejlede (SPEC §7):", file=sys.stderr)
        for pr in problems:
            print(f"  - {pr}", file=sys.stderr)
        sys.exit(1)
