#!/usr/bin/env python3
"""Backtest-bootstrap (SPEC M2): konsoliderer Binance-klines, kalibrerer
oracle-lag mod recorded F1-data, fitter BRAIN v0 (binned empirisk + Laplace
+ isotonic — SPEC §2) og skriver en dansk rapport.

Subkommandoer (køres i rækkefølge af `all`):
  consolidate  Binance 1s-kline-zips -> data/klines/{btcusdt,ethusdt}.parquet
  lag          Kalibrerer oracle-lag mod data/archive_smoke.sqlite (statisk)
  fit          Syntetisk oracle + BRAIN v0-fit + Brier-evaluering pr. symbol
  report       Skriver research/backtest_report.md
  all          consolidate -> lag -> fit -> report

Kørsel:  python3 research/backtest.py all
         python3 research/backtest.py consolidate --delete-zips

OBS (kendte begrænsninger, uddybes i rapporten):
  - Syntetisk oracle = close(t - lag_s) på et 1s-heartbeat-grid. Det er en
    forenkling af Chainlinks faktiske deviation-drevne opdateringslogik.
  - Lag-kalibreringen bruger F2 (Binance USDⓈ-M FUTURES bookTicker-mid) som
    "Binance"-side, mens fit-trinnet bruger SPOT-klines. Lille basis
    accepteres i v1 (SPEC M2).
  - Ingen OFI/tape/likvidationsfeatures her — kun delta_bn, delta_or, tau, rv60.
  - Sammenligning mod Polymarkets faktiske bog-mid hører til M3/Gate A, ikke
    denne rapport (som kun sammenligner mod interne baselines).
"""
from __future__ import annotations

import argparse
import io
import json
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from src.modes import load_config  # noqa: E402

# --------------------------------------------------------------------------
# Konstanter (kontrakt, se SPEC §4/M2)
# --------------------------------------------------------------------------

KLINE_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
ASSET_OF_SYMBOL = {"BTCUSDT": "btc", "ETHUSDT": "eth"}
KLINE_COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
                  "quote_volume", "count", "taker_buy_base", "taker_buy_quote", "ignore"]

US_THRESHOLD = 1_000_000_000_000_000  # 1e15: open_time > dette => mikrosekunder

TAU_LIST = [240, 210, 180, 150, 120, 90, 60, 45, 30, 20, 10]  # sek. til udløb, decision-punkter
BAND_NAMES = ["240-120", "120-30", "30-0"]                    # M3-tau-bånd (SPEC §4/M3)
N_DELTA_BINS = 15     # kvantil-bins for delta_bn_bp, PER tau-bånd, fra TRAIN
N_VOL_REGIMES = 3     # terciler af rv60, globalt fra TRAIN
WINDOW_PERIOD = 300   # 5m-vindue (SPEC §2 CLOCK)
WINDOW_QC_SPAN = 300  # [ts0-300, ts0+300] skal være hul-frit, ellers droppes vinduet

DEFAULT_LAG_S = 2     # fit-fallback hvis oracle_lag.json mangler (SPEC M2)

DEFAULT_ZIPS_DIR = ROOT / "data" / "klines" / "zips"
DEFAULT_KLINES_DIR = ROOT / "data" / "klines"
DEFAULT_ARCHIVE = ROOT / "data" / "archive_smoke.sqlite"
DEFAULT_LAG_FILE = HERE / "oracle_lag.json"
DEFAULT_BACKTEST_DIR = ROOT / "data" / "backtest"
DEFAULT_REPORT = HERE / "backtest_report.md"
DEFAULT_CONFIG = ROOT / "config.yaml"


# ==========================================================================
# RENE FUNKTIONER (unittestet fra tests/test_backtest.py)
# ==========================================================================

def has_header_row(first_line: str) -> bool:
    """True hvis første linje i en kline-CSV er en header-række.

    Detektion: første felt er IKKE-numerisk (fx "open_time"). Nyere
    Binance Vision-filer har header, ældre filer har det ikke — begge
    formater forekommer i data/klines/zips/ (SPEC M2 consolidate)."""
    first_field = first_line.split(",", 1)[0].strip()
    try:
        float(first_field)
        return False
    except ValueError:
        return True


def normalize_open_time(raw_open_time) -> np.ndarray:
    """Normaliserer Binance open_time til unix-sekunder (int64, elementvis).

    open_time kan være millisekunder (ældre filer, ~13 cifre) ELLER
    mikrosekunder (nyere filer, ~16 cifre) — blandet på tværs af filer,
    ikke inden i samme fil. ts > 1e15 => mikrosekunder (SPEC M2 consolidate)."""
    raw = np.asarray(raw_open_time, dtype=np.int64)
    is_us = raw > US_THRESHOLD
    sec = np.where(is_us, raw // 1_000_000, raw // 1000)
    return sec.astype(np.int64)


def parse_kline_csv(text: str) -> pd.DataFrame:
    """Parser en enkelt Binance kline-CSV (én zips indhold) til ts+close.

    Kombinerer header-detektion og ms/µs-normalisering. Returnerer en
    DataFrame med kolonnerne ts (int64, unix-sekund) og close (float64)."""
    first_line = text.split("\n", 1)[0]
    header = 0 if has_header_row(first_line) else None
    df = pd.read_csv(io.StringIO(text), header=header, names=KLINE_COLUMNS,
                      usecols=["open_time", "close"],
                      dtype={"open_time": "int64", "close": "float64"})
    ts = normalize_open_time(df["open_time"].to_numpy())
    close = df["close"].to_numpy(dtype=np.float64)
    return pd.DataFrame({"ts": ts, "close": close})


def label_up(s_open, s_close):
    """Resolution-regel (VERIFIED.md §2): close >= open => Up. Tie => Up.

    Virker elementvis (scalar eller numpy-array/Serie)."""
    return np.asarray(s_close) >= np.asarray(s_open)


def tau_band(tau: float) -> str:
    """M3-tau-bånd (SPEC §4/M3): [240-120) / [120-30) / [30-0]."""
    if 120 < tau <= 240:
        return "240-120"
    if 30 < tau <= 120:
        return "120-30"
    if 0 <= tau <= 30:
        return "30-0"
    raise ValueError(f"tau={tau} uden for definerede bånd [0,240]")


def quantile_bin_edges(x: np.ndarray, n_bins: int) -> np.ndarray:
    """Kvantil-bin-kanter til (op til) n_bins bins fra data x.

    Dedupe'r ens kvantiler (kan ske ved lav varians/mange ens værdier) så
    kanterne er strengt monotont stigende — ellers ville np.digitize give
    tomme eller ugyldige bins. Første/sidste kant sættes til -inf/+inf så
    binning altid dækker fremtidig (test-)data, også uden for TRAINs range."""
    x = np.asarray(x, dtype=np.float64)
    x = x[np.isfinite(x)]
    if x.size == 0:
        return np.array([-np.inf, np.inf])
    qs = np.linspace(0.0, 1.0, n_bins + 1)
    edges = np.unique(np.quantile(x, qs))
    if edges.size < 2:
        return np.array([-np.inf, np.inf])
    edges = edges.copy()
    edges[0] = -np.inf
    edges[-1] = np.inf
    return edges


def laplace_cell_prob(n_up, n):
    """Celle-P med Laplace-glatning (SPEC §2 BRAIN v0): (n_up+2)/(n+4).

    Virker elementvis (scalar eller numpy-array/Serie)."""
    return (n_up + 2) / (n + 4)


def brier_score(y_true, p_hat) -> float:
    """Brier-score: middel((p_hat - y)^2). Lavere er bedre."""
    y = np.asarray(y_true, dtype=np.float64)
    p = np.asarray(p_hat, dtype=np.float64)
    return float(np.mean((p - y) ** 2))


# ==========================================================================
# CONSOLIDATE
# ==========================================================================

def find_zips(zips_dir: Path, sym_upper: str) -> list[Path]:
    return sorted(zips_dir.glob(f"{sym_upper}-1s-*.zip"))


def zip_date(path: Path) -> str:
    """BTCUSDT-1s-2026-03-01.zip -> '2026-03-01'."""
    return path.stem.split("-", 2)[-1]


def read_kline_zip(path: Path) -> pd.DataFrame:
    """Læser den (ene) CSV i en Binance Vision kline-zip."""
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise ValueError("ingen CSV-fil i zip")
        with zf.open(names[0]) as f:
            text = f.read().decode("utf-8")
    return parse_kline_csv(text)


def coverage_stats(ts: np.ndarray) -> dict:
    """Datadækning fra en ts-kolonne (unix-sekunder): antal rækker,
    første/sidste kalenderdag, og manglende kalenderdage i det spand.

    Ydelse: dedupér til hele UTC-DAGE (heltalsdivision + np.unique) FØR der
    formateres datostrenge — for et helt symbols 1s-datasæt (~13M rækker)
    er der kun ~150-200 unikke dage, men strftime på alle 13M rækker
    (fx via pd.to_datetime(...).strftime(...)) tager >1 minut; på de
    dedupede dage tager det <1s."""
    if ts.size == 0:
        return {"n_rows": 0, "date_min": None, "date_max": None, "missing_days": []}
    dmin = time.strftime("%Y-%m-%d", time.gmtime(int(ts.min())))
    dmax = time.strftime("%Y-%m-%d", time.gmtime(int(ts.max())))
    day_epoch = np.unique(ts // 86400)
    days_present = {time.strftime("%Y-%m-%d", time.gmtime(int(d) * 86400)) for d in day_epoch}
    all_days = pd.date_range(dmin, dmax, freq="D").strftime("%Y-%m-%d").tolist()
    missing = sorted(set(all_days) - days_present)
    return {"n_rows": int(ts.size), "date_min": dmin, "date_max": dmax, "missing_days": missing}


def consolidate_symbol(sym_upper: str, zips_dir: Path, out_dir: Path, *,
                        delete_zips: bool) -> dict:
    asset = ASSET_OF_SYMBOL[sym_upper]
    files = find_zips(zips_dir, sym_upper)
    result = {"symbol": asset, "n_files": len(files), "n_rows": 0,
              "date_min": None, "date_max": None, "missing_days": []}
    if not files:
        return result

    frames = []
    parsed_ok: list[Path] = []
    for fp in files:
        try:
            frames.append(read_kline_zip(fp))
            parsed_ok.append(fp)
        except Exception as e:  # fejlsikker: en korrupt zip vælter ikke hele konsolideringen
            print(f"ADVARSEL [{asset}]: kunne ikke læse {fp.name}: {e}", file=sys.stderr)
    if not frames:
        return result

    all_df = pd.concat(frames, ignore_index=True)
    all_df = all_df.sort_values("ts").drop_duplicates(subset="ts", keep="last")
    all_df = all_df.reset_index(drop=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{sym_upper.lower()}.parquet"
    all_df.to_parquet(out_path, index=False)

    cov = coverage_stats(all_df["ts"].to_numpy())
    result.update(cov)
    result["out_path"] = str(out_path)

    if delete_zips:
        # AUDIT-fix: slet KUN zips der blev parset OK — en korrupt fil skal
        # bevares så den kan re-downloades/undersøges, ikke destrueres
        for fp in parsed_ok:
            fp.unlink()
        n_kept = len(files) - len(parsed_ok)
        if n_kept:
            print(f"ADVARSEL [{asset}]: {n_kept} fejlede zips BEHOLDT trods --delete-zips",
                  file=sys.stderr)

    return result


def cmd_consolidate(args) -> int:
    zips_dir = Path(args.zips_dir)
    out_dir = Path(args.out_dir)
    for sym_upper in KLINE_SYMBOLS:
        r = consolidate_symbol(sym_upper, zips_dir, out_dir, delete_zips=args.delete_zips)
        if r["n_files"] == 0:
            print(f"[{r['symbol']}] ingen zips fundet i {zips_dir} — springer over")
            continue
        n_missing = len(r["missing_days"])
        print(f"[{r['symbol']}] {r['n_files']} zips -> {r['n_rows']} rækker "
              f"({r['date_min']} .. {r['date_max']}), {n_missing} manglende dage"
              + (f" ({', '.join(r['missing_days'][:10])}{'…' if n_missing > 10 else ''})"
                 if n_missing else ""))
    return 0


# ==========================================================================
# LAG (oracle-lag-kalibrering v1, SPEC M2)
# ==========================================================================

def load_f1_f2_btc_from_archive(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Læser F1 (asset=btc) og F2 (btcusdt@bookTicker) fra en STATISK
    arkiv-sqlite, read-only. Bruger de indlejrede kilde-tidsstempler
    (oracle_ts_ms hhv. E) — IKKE lokal recv_wall/ts_wall — så lag-målingen
    ikke forurenes af recorderens modtagelses-jitter.

    OBS: F2 er FUTURES-mid (Binance USDⓈ-M bookTicker), mens kline-fittet
    bruger SPOT-klines — lille basis accepteres i v1 (kendt begrænsning)."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        f1_rows = con.execute("SELECT payload FROM events WHERE source='f1'").fetchall()
        f2_rows = con.execute("SELECT payload FROM events WHERE source='f2'").fetchall()
    finally:
        con.close()

    o_ts, o_val = [], []
    for (payload,) in f1_rows:
        d = json.loads(payload)
        if d.get("asset") != "btc":
            continue
        try:
            o_ts.append(int(d["oracle_ts_ms"]) // 1000)
            o_val.append(float(d["value"]))
        except (KeyError, TypeError, ValueError):
            continue

    b_ts, b_val = [], []
    for (payload,) in f2_rows:
        d = json.loads(payload)
        if d.get("stream") != "btcusdt@bookTicker":
            continue
        dd = d.get("d") or {}
        try:
            bid, ask, ev_ms = float(dd["b"]), float(dd["a"]), int(dd["E"])
        except (KeyError, TypeError, ValueError):
            continue
        b_ts.append(ev_ms // 1000)
        b_val.append((bid + ask) / 2.0)

    oracle = pd.DataFrame({"ts": o_ts, "value": o_val})
    binance = pd.DataFrame({"ts": b_ts, "value": b_val})
    return oracle, binance


def resample_1hz_last(df: pd.DataFrame, t0: int, t1: int) -> np.ndarray:
    """Resampler (ts,value) til et sammenhængende 1Hz-grid [t0,t1] med
    'sidste værdi per sekund'. Korte huller udfyldes fremad (ffill) —
    heartbeat-antagelsen matcher RTDS/bookTicker-kadencen (~1 Hz, se
    VERIFIED.md §5 / §4.3)."""
    s = df.groupby("ts")["value"].last()
    idx = np.arange(t0, t1 + 1, dtype=np.int64)
    s = s.reindex(idx).ffill()
    return s.to_numpy(dtype=np.float64)


def log_returns(x: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.diff(np.log(x))


def pearson_corr(a: np.ndarray, b: np.ndarray) -> float:
    if a.size < 2:
        return float("nan")
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    if denom == 0:
        return float("nan")
    return float((a * b).sum() / denom)


def scan_lags(ret_oracle: np.ndarray, ret_binance: np.ndarray, max_lag: int) -> list[dict]:
    """Korrelation af 1s-log-returns ved lags 0..max_lag (oracle bagud ift.
    Binance): korrelerer ret_oracle[t] med ret_binance[t-lag] — dvs. hvis
    oracle(t) ≈ binance(t-lag_s) er det netop dette lag der maksimerer
    korrelationen."""
    n = min(ret_oracle.size, ret_binance.size)
    ro, rb = ret_oracle[:n], ret_binance[:n]
    out = []
    for lag in range(max_lag + 1):
        a, b = (ro, rb) if lag == 0 else (ro[lag:], rb[:-lag])
        out.append({"lag_s": lag, "corr": pearson_corr(a, b), "n_seconds": int(a.size)})
    return out


def cmd_lag(args) -> int:
    archive = Path(args.archive)
    if not archive.exists():
        print(f"ADVARSEL: {archive} ikke fundet — kan ikke kalibrere oracle-lag "
              f"(fit falder tilbage til lag_s={DEFAULT_LAG_S})", file=sys.stderr)
        return 1

    oracle, binance = load_f1_f2_btc_from_archive(archive)
    if oracle.empty or binance.empty:
        print("ADVARSEL: intet f1(asset=btc)/f2(btcusdt@bookTicker) data i arkivet",
              file=sys.stderr)
        return 1

    t0 = int(max(oracle["ts"].min(), binance["ts"].min()))
    t1 = int(min(oracle["ts"].max(), binance["ts"].max()))
    if t1 - t0 < args.max_lag + 2:
        print(f"ADVARSEL: kun {t1 - t0}s overlap mellem f1/f2 — for lidt til "
              f"lag-kalibrering (behøver > {args.max_lag + 2}s)", file=sys.stderr)
        return 1

    ov = resample_1hz_last(oracle, t0, t1)
    bv = resample_1hz_last(binance, t0, t1)
    ro, rb = log_returns(ov), log_returns(bv)
    scan = scan_lags(ro, rb, args.max_lag)
    valid = [r for r in scan if not np.isnan(r["corr"])]
    if not valid:
        print("ADVARSEL: ingen gyldig korrelation fundet (konstant serie?)", file=sys.stderr)
        return 1
    best = max(valid, key=lambda r: r["corr"])

    out = {"lag_s": best["lag_s"], "corr": round(best["corr"], 6),
           "n_seconds": best["n_seconds"], "source": "archive_smoke.sqlite",
           "computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n",
                              encoding="utf-8")
    print(f"lag* = {out['lag_s']}s (corr={out['corr']}, n={out['n_seconds']}s) -> {args.out}")
    for r in scan:
        marker = " <-- valgt" if r["lag_s"] == out["lag_s"] else ""
        print(f"  lag={r['lag_s']:>2}s  corr={r['corr']:.4f}  n={r['n_seconds']}{marker}")
    return 0


def load_lag_info(path: Path) -> dict:
    """fit/report bruger denne: findes oracle_lag.json ikke, brug
    default lag_s=2 med advarsel (SPEC M2)."""
    if path.exists():
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
            info.setdefault("lag_s", DEFAULT_LAG_S)
            return info
        except Exception as e:
            print(f"ADVARSEL: kunne ikke læse {path}: {e} — bruger default "
                  f"lag_s={DEFAULT_LAG_S}", file=sys.stderr)
    else:
        print(f"ADVARSEL: {path} ikke fundet — bruger default lag_s={DEFAULT_LAG_S} "
              f"(kalibrér med 'backtest.py lag' når data/archive_smoke.sqlite findes)",
              file=sys.stderr)
    return {"lag_s": DEFAULT_LAG_S, "corr": None, "n_seconds": 0, "source": "default",
            "computed_at": None}


# ==========================================================================
# FIT (BRAIN v0: binned empirisk + Laplace + isotonic, SPEC §2)
# ==========================================================================

def compute_rv60_dense(dense_close: np.ndarray, halflife_s: float) -> np.ndarray:
    """rv60 (SPEC M2 fit): ewma af (1s log-return)^2, halvliv fra config
    (brain.rv_halflife_s), udtrykt som bp/√s (sqrt(ewma)*1e4 — basis-
    samplingen ER 1s, så tallet er allerede på "per sqrt(sekund)"-skala).

    Beregnes PR. SAMMENHÆNGENDE datasegment ("ø" af rigtige, fortløbende
    sekunder i dense_close) i stedet for globalt på tværs af huller — ellers
    ville et hul (manglende dag) indsætte ét kæmpe spooky "1s-afkast" over
    hele hullets længde og forurene rv lige efter genoptagelsen. Hvert
    segment varmer selv op fra sin egen start (naturlig ewm-adfærd)."""
    span = dense_close.size
    rv = np.full(span, np.nan, dtype=np.float64)
    present = ~np.isnan(dense_close)
    idx = np.flatnonzero(present)
    if idx.size < 2:
        return rv
    breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate(([idx[0]], idx[breaks + 1]))
    ends = np.concatenate((idx[breaks], [idx[-1]]))  # inklusiv
    for s0, e0 in zip(starts, ends):
        if e0 - s0 < 1:
            continue
        seg = dense_close[s0:e0 + 1]
        with np.errstate(divide="ignore", invalid="ignore"):
            logret = np.diff(np.log(seg))
        var = pd.Series(logret).pow(2).ewm(halflife=halflife_s).mean().to_numpy()
        rv[s0 + 1:e0 + 1] = np.sqrt(var) * 1e4
    return rv


def enumerate_windows(ts: np.ndarray) -> tuple[list[int], int]:
    """Alle kandidat-ts0 (ts0 % 300 == 0) i datadækningen [ts.min(),
    ts.max()]; en kandidat er GYLDIG hvis [ts0-300, ts0+300] er hul-frit
    (alle heltalssekunder til stede). Returnerer (gyldige ts0'er, antal
    droppede — inkl. dem for tæt på datadækningens rand)."""
    if ts.size == 0:
        return [], 0
    tmin, tmax = int(ts.min()), int(ts.max())
    span = tmax - tmin + 1
    present = np.zeros(span, dtype=bool)
    present[ts - tmin] = True
    cum = np.concatenate(([0], np.cumsum(present)))

    def contiguous(lo_, hi_):
        if lo_ < tmin or hi_ > tmax:
            return False
        i0, i1 = lo_ - tmin, hi_ - tmin
        return (cum[i1 + 1] - cum[i0]) == (i1 - i0 + 1)

    first_multiple = ((tmin + WINDOW_PERIOD - 1) // WINDOW_PERIOD) * WINDOW_PERIOD
    valid, n_dropped = [], 0
    for ts0 in range(first_multiple, tmax + 1, WINDOW_PERIOD):
        if contiguous(ts0 - WINDOW_QC_SPAN, ts0 + WINDOW_QC_SPAN):
            valid.append(ts0)
        else:
            n_dropped += 1
    return valid, n_dropped


def fit_symbol(klines_df: pd.DataFrame, cfg, lag_s: int) -> dict:
    """Fitter BRAIN v0 for ét symbol. Returnerer status + windows_df (én
    række per vindue×tau — features, label, split, p_hat) klar til at blive
    gemt som data/backtest/{sym}_windows.parquet."""
    ts = klines_df["ts"].to_numpy(dtype=np.int64)
    close = klines_df["close"].to_numpy(dtype=np.float64)
    order = np.argsort(ts)
    ts, close = ts[order], close[order]
    keep = np.concatenate(([True], np.diff(ts) != 0))  # defensivt (consolidate deduper allerede)
    ts, close = ts[keep], close[keep]

    if ts.size == 0:
        return {"status": "no_data", "n_windows": 0, "n_dropped": 0, "windows_df": pd.DataFrame()}

    tmin, tmax = int(ts[0]), int(ts[-1])
    span = tmax - tmin + 1
    dense_close = np.full(span, np.nan, dtype=np.float64)
    dense_close[ts - tmin] = close

    oracle_dense = np.full(span, np.nan, dtype=np.float64)
    if lag_s < span:
        oracle_dense[lag_s:] = dense_close[:span - lag_s]

    rv_dense = compute_rv60_dense(dense_close, cfg.brain.rv_halflife_s)

    # samme kandidat-/gyldigheds-logik som `report` bruger til datadæknings-
    # tabellen (enumerate_windows) — én kilde til sandhed for hvilke vinduer
    # der er gyldige.
    valid_ts0, n_dropped = enumerate_windows(ts)

    window_ts0: list[int] = []
    rows = []
    for ts0 in valid_ts0:
        # S_open/S_close er ORACLE-værdier (kontrakt: settlement følger det
        # lagged oracle, ikke rå Binance) — delta_bn måles mod oracle-S_open,
        # så Binances forspring på lag_s er selve signalet der testes.
        s_open = oracle_dense[ts0 - tmin]
        s_close = oracle_dense[ts0 + WINDOW_PERIOD - tmin]
        if not (np.isfinite(s_open) and np.isfinite(s_close)):
            n_dropped += 1
            continue
        label = int(label_up(s_open, s_close))
        window_ts0.append(ts0)
        for tau in TAU_LIST:
            t_dec = ts0 + WINDOW_PERIOD - tau
            i = t_dec - tmin
            bn, orc, rv = dense_close[i], oracle_dense[i], rv_dense[i]
            if not (np.isfinite(bn) and np.isfinite(orc) and np.isfinite(rv)):
                continue  # fejlsikkert: bør ikke ske efter QC-tjekket i enumerate_windows
            rows.append((ts0, tau, (bn - s_open) / s_open * 1e4,
                        (orc - s_open) / s_open * 1e4, rv, label))

    n_windows = len(window_ts0)
    if n_windows == 0:
        return {"status": "no_valid_windows", "n_windows": 0, "n_dropped": n_dropped,
                "windows_df": pd.DataFrame()}

    df = pd.DataFrame(rows, columns=["ts0", "tau", "delta_bn_bp", "delta_or_bp", "rv60", "label"])
    df["band"] = df["tau"].map(tau_band)
    df["lag_s"] = lag_s  # proveniens (AUDIT-fix): rapporten viser det lag der FITTEDES med

    n_train_windows = max(1, int(round(0.8 * n_windows)))
    train_ts0 = set(window_ts0[:n_train_windows])
    df["split"] = np.where(df["ts0"].isin(train_ts0), "train", "test")

    # 15 kvantil-delta-bins PER tau-bånd, fra TRAIN
    band_delta_edges = {}
    for band in BAND_NAMES:
        tr = df[(df["split"] == "train") & (df["band"] == band)]
        band_delta_edges[band] = quantile_bin_edges(tr["delta_bn_bp"].to_numpy(), N_DELTA_BINS)
    df["delta_bin"] = -1
    for band in BAND_NAMES:
        m = df["band"] == band
        df.loc[m, "delta_bin"] = np.digitize(df.loc[m, "delta_bn_bp"], band_delta_edges[band][1:-1])

    # vol-regime: terciler af rv60, globalt fra TRAIN (på tværs af bånd/tau)
    vol_edges = quantile_bin_edges(df.loc[df["split"] == "train", "rv60"].to_numpy(),
                                    N_VOL_REGIMES)
    df["vol_regime"] = np.digitize(df["rv60"], vol_edges[1:-1])

    train_df = df[df["split"] == "train"]
    cell_stats = (train_df.groupby(["band", "delta_bin", "vol_regime"])["label"]
                  .agg(n_up="sum", n_train_cell="count").reset_index())
    df = df.merge(cell_stats, on=["band", "delta_bin", "vol_regime"], how="left")
    df["n_up"] = df["n_up"].fillna(0).astype(int)
    df["n_train_cell"] = df["n_train_cell"].fillna(0).astype(int)

    band_base_rate = {}
    for band in BAND_NAMES:
        lab = train_df.loc[train_df["band"] == band, "label"]
        band_base_rate[band] = laplace_cell_prob(int(lab.sum()), int(lab.shape[0]))

    # celle-P: Laplace hvis cellen er set i TRAIN, ellers bånd-base-rate (ukendt celle)
    df["cell_p"] = np.where(df["n_train_cell"] > 0,
                            laplace_cell_prob(df["n_up"], df["n_train_cell"]),
                            df["band"].map(band_base_rate))

    isotonics = {}
    for band in BAND_NAMES:
        tr = df[(df["split"] == "train") & (df["band"] == band)]
        iso = IsotonicRegression(y_min=0.001, y_max=0.999, out_of_bounds="clip")
        iso.fit(tr["cell_p"].to_numpy(dtype=float), tr["label"].to_numpy(dtype=float))
        isotonics[band] = iso

    df["p_hat"] = np.nan
    for band in BAND_NAMES:
        m = df["band"] == band
        df.loc[m, "p_hat"] = isotonics[band].predict(df.loc[m, "cell_p"].to_numpy(dtype=float))

    return {"status": "ok", "n_windows": n_windows, "n_dropped": n_dropped,
            "n_train_windows": len(train_ts0), "n_test_windows": n_windows - len(train_ts0),
            "lag_s": lag_s, "band_base_rate": band_base_rate, "windows_df": df}


def cmd_fit(args) -> int:
    cfg = load_config(args.config)
    lag_info = load_lag_info(Path(args.lag_file))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    any_ok = False
    for sym_upper in KLINE_SYMBOLS:
        asset = ASSET_OF_SYMBOL[sym_upper]
        parquet_path = Path(args.klines_dir) / f"{sym_upper.lower()}.parquet"
        if not parquet_path.exists():
            print(f"[{asset}] ADVARSEL: {parquet_path} ikke fundet — springer fit over "
                  f"(kør 'consolidate' først)", file=sys.stderr)
            continue
        klines_df = pd.read_parquet(parquet_path)
        if klines_df.empty:
            print(f"[{asset}] ADVARSEL: {parquet_path} er tom — springer fit over",
                  file=sys.stderr)
            continue

        res = fit_symbol(klines_df, cfg, lag_info["lag_s"])
        if res["status"] != "ok":
            print(f"[{asset}] {res['status']} — ingen gyldige vinduer "
                  f"({res['n_dropped']} droppet)", file=sys.stderr)
            continue

        out_path = out_dir / f"{asset}_windows.parquet"
        res["windows_df"].to_parquet(out_path, index=False)
        any_ok = True
        ev = evaluate_windows(res["windows_df"], cfg.brain.min_bin_n)
        print(f"[{asset}] {res['n_windows']} vinduer ({res['n_dropped']} droppet), "
              f"train={res['n_train_windows']} test={res['n_test_windows']} "
              f"-> {out_path}")
        for row in ev["brier"]:
            print(f"    bånd {row['band']:>7}: n={row['n']:>6}  "
                  f"Brier model={row['brier_model']:.4f}  "
                  f"konst.0.5={row['brier_const']:.4f}  "
                  f"base-rate={row['brier_base_rate']:.4f}")
        print(f"    tynd-bin-andel (n_train<{cfg.brain.min_bin_n}): "
              f"{ev['thin_overall']:.3f}")
    return 0 if any_ok else 1


# ==========================================================================
# EVALUERING + REPORT
# ==========================================================================

def calibration_table(p_hat: np.ndarray, y: np.ndarray, n_deciles: int = 10) -> pd.DataFrame:
    """10 deciler af p_hat -> (middel p_hat, empirisk up-rate, n)."""
    d = pd.DataFrame({"p_hat": np.asarray(p_hat, dtype=float),
                      "y": np.asarray(y, dtype=float)})
    if d.empty:
        return pd.DataFrame(columns=["decile", "mean_p_hat", "emp_up_rate", "n"])
    try:
        d["decile"] = pd.qcut(d["p_hat"], n_deciles, labels=False, duplicates="drop")
    except ValueError:
        d["decile"] = 0
    g = (d.groupby("decile")
          .agg(mean_p_hat=("p_hat", "mean"), emp_up_rate=("y", "mean"), n=("y", "size"))
          .reset_index())
    return g


def evaluate_windows(df: pd.DataFrame, min_bin_n: int) -> dict:
    """Evaluerer TEST-delen af et windows_df: Brier per tau-bånd (model vs.
    to baselines), kalibreringstabel og tynd-bin-andel. Bruges af BÅDE
    `fit` (konsol-opsummering) og `report` (genindlæst fra parquet — ingen
    afhængighed af fit's in-memory tilstand, så `report` kan køres alene)."""
    train_df = df[df["split"] == "train"]
    test_df = df[df["split"] == "test"]

    band_base_rate = {}
    for band in BAND_NAMES:
        lab = train_df.loc[train_df["band"] == band, "label"]
        band_base_rate[band] = laplace_cell_prob(int(lab.sum()), int(lab.shape[0])) \
            if lab.shape[0] else float("nan")

    brier_rows, calib_frames, thin_rows = [], [], []
    for band in BAND_NAMES:
        sub = test_df[test_df["band"] == band]
        if sub.empty:
            continue
        y = sub["label"].to_numpy(dtype=float)
        p_model = sub["p_hat"].to_numpy(dtype=float)
        base = band_base_rate[band]
        brier_rows.append({
            "band": band, "n": int(sub.shape[0]),
            "brier_model": brier_score(y, p_model),
            "brier_const": brier_score(y, np.full_like(y, 0.5)),
            "brier_base_rate": brier_score(y, np.full_like(y, base)),
            "base_rate": base,
        })
        calib = calibration_table(p_model, y)
        calib["band"] = band
        calib_frames.append(calib)
        thin_rows.append({"band": band, "thin_frac": float((sub["n_train_cell"] < min_bin_n).mean()),
                          "n": int(sub.shape[0])})

    overall_thin = (float((test_df["n_train_cell"] < min_bin_n).mean())
                    if not test_df.empty else float("nan"))
    calib_df = pd.concat(calib_frames, ignore_index=True) if calib_frames else pd.DataFrame()
    return {"brier": brier_rows, "calibration": calib_df, "thin": thin_rows,
            "thin_overall": overall_thin, "band_base_rate": band_base_rate,
            "n_test": int(test_df.shape[0]), "n_train": int(train_df.shape[0])}


def _fmt_missing(missing: list[str]) -> str:
    if not missing:
        return "ingen"
    if len(missing) <= 15:
        return f"{len(missing)} ({', '.join(missing)})"
    return f"{len(missing)} (fx {', '.join(missing[:8])}, …)"


def cmd_report(args) -> int:
    cfg = load_config(args.config)
    min_bin_n = cfg.brain.min_bin_n
    lines: list[str] = []
    gen = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lines += [
        "# Backtest-rapport (M2) — BONESAW", "",
        f"Genereret {gen} af `research/backtest.py report`. Bootstrap-rapport til "
        "M2 (SPEC §4) — INGEN pass/fail-gate; det kommer først med Gate A (M3).", "",
    ]

    # ---- datadækning ----------------------------------------------------
    lines += ["## Datadækning", "",
              "| Symbol | Zip-filer | Rækker | Dækning | Manglende dage | "
              "Vinduer (gyldige) | Vinduer droppet |",
              "|---|---|---|---|---|---|---|"]
    coverage_by_asset = {}
    for sym_upper in KLINE_SYMBOLS:
        asset = ASSET_OF_SYMBOL[sym_upper]
        parquet_path = Path(args.klines_dir) / f"{sym_upper.lower()}.parquet"
        n_files = len(find_zips(DEFAULT_ZIPS_DIR, sym_upper))
        if not parquet_path.exists():
            lines.append(f"| {asset.upper()} | {n_files} | 0 | — | — | 0 | 0 |")
            coverage_by_asset[asset] = None
            continue
        kdf = pd.read_parquet(parquet_path)
        ts = kdf["ts"].to_numpy(dtype=np.int64) if not kdf.empty else np.array([], dtype=np.int64)
        cov = coverage_stats(ts)
        valid_ts0, n_dropped = enumerate_windows(ts)
        coverage_by_asset[asset] = cov
        lines.append(
            f"| {asset.upper()} | {n_files} | {cov['n_rows']} | "
            f"{cov['date_min']} .. {cov['date_max']} | "
            f"{_fmt_missing(cov['missing_days'])} | {len(valid_ts0)} | {n_dropped} |")
    lines.append("")

    # ---- oracle-lag-kalibrering ------------------------------------------
    lag_info = load_lag_info(Path(args.lag_file))
    lines += ["## Oracle-lag-kalibrering", ""]
    if lag_info["source"] == "default":
        lines.append(f"**ADVARSEL:** `{args.lag_file}` blev ikke fundet — "
                      f"bruger default `lag_s={lag_info['lag_s']}` (ikke kalibreret). "
                      "Kør `backtest.py lag` når `data/archive_smoke.sqlite` findes.")
    else:
        lines.append(
            f"lag_s = **{lag_info['lag_s']}** sekunder, korrelation = "
            f"{lag_info['corr']}, n = {lag_info['n_seconds']} overlappende sekunder. "
            f"Kilde: `{lag_info['source']}`, beregnet {lag_info['computed_at']}.")
    lines += ["",
              "Kendt begrænsning: lag-kalibreringen bruger F2 (Binance USDⓈ-M "
              "FUTURES bookTicker-mid) som Binance-side, mens fit-trinnet bruger "
              "SPOT-klines. Lille spot/futures-basis accepteres i v1 (se "
              "'Kendte begrænsninger' nedenfor).", ""]

    # ---- brier + kalibrering + tynd-bin pr. symbol -----------------------
    lines += ["## Brier-score per symbol × tau-bånd (TEST-split)", "",
              "Model = binned empirisk + Laplace + isotonic (BRAIN v0). To baselines: "
              "konstant 0,5 og train-base-rate for båndet. Lavere Brier er bedre.", ""]
    calib_sections: list[str] = []
    thin_lines: list[str] = ["## Tynd-bin-andel (celler med n_train < min_bin_n)", "",
                              f"`brain.min_bin_n` = {min_bin_n} (config.yaml). Tynd-bin-andel er "
                              "andelen af TEST-rækker hvis celle så færre end dette antal "
                              "TRAIN-observationer (0 tæller også med — 'ukendt celle').", "",
                              "| Symbol | Bånd | Tynd-andel | n (test) |", "|---|---|---|---|"]

    any_windows = False
    for sym_upper in KLINE_SYMBOLS:
        asset = ASSET_OF_SYMBOL[sym_upper]
        windows_path = Path(args.backtest_dir) / f"{asset}_windows.parquet"
        lines.append(f"### {asset.upper()}")
        lines.append("")
        if not windows_path.exists():
            lines.append("Ingen fit-resultater fundet (kør `backtest.py fit` først, eller "
                          "der var intet gyldigt vindue for dette symbol).")
            lines.append("")
            continue
        wdf = pd.read_parquet(windows_path)
        if wdf.empty:
            lines.append("Tomt windows-datasæt.")
            lines.append("")
            continue
        any_windows = True
        # proveniens (AUDIT-fix): tallene tilhører det lag fittet brugte —
        # ikke nødvendigvis det aktuelle oracle_lag.json
        if "lag_s" in wdf.columns:
            fitted_lags = sorted(set(int(x) for x in wdf["lag_s"].unique()))
            lines.append(f"Fittet med `lag_s = {fitted_lags}`."
                         + (f" **ADVARSEL:** aktuelt kalibreret lag er {lag_info['lag_s']} — "
                            "genkør `fit` for at opdatere tallene."
                            if fitted_lags != [int(lag_info['lag_s'])] else ""))
            lines.append("")
        ev = evaluate_windows(wdf, min_bin_n)
        lines += ["| Tau-bånd | n | Brier (model) | Brier (konstant 0,5) | "
                  "Brier (train-base-rate) | Base-rate |",
                  "|---|---|---|---|---|---|"]
        for row in ev["brier"]:
            lines.append(
                f"| {row['band']} | {row['n']} | {row['brier_model']:.5f} | "
                f"{row['brier_const']:.5f} | {row['brier_base_rate']:.5f} | "
                f"{row['base_rate']:.5f} |")
        lines.append("")
        lines.append(
            "*(Base-rate ligger typisk meget tæt på 0,5 for 5m BTC/ETH-vinduer — "
            "derfor ligner de to baselines hinanden; det er i sig selv et "
            "empirisk fund, ikke en fejl i tabellen.)*")
        lines.append("")

        for t in ev["thin"]:
            thin_lines.append(f"| {asset.upper()} | {t['band']} | {t['thin_frac']:.3f} | {t['n']} |")
        thin_lines.append(f"| {asset.upper()} | **samlet** | **{ev['thin_overall']:.3f}** | "
                          f"{ev['n_test']} |")

        calib_sections.append(f"### {asset.upper()}")
        calib_sections.append("")
        if ev["calibration"].empty:
            calib_sections.append("Intet test-data.")
            calib_sections.append("")
        else:
            for band in BAND_NAMES:
                bsub = ev["calibration"][ev["calibration"]["band"] == band]
                if bsub.empty:
                    continue
                calib_sections.append(f"**Bånd {band}**")
                calib_sections.append("")
                calib_sections += ["| Decil | Middel p̂ | Empirisk up-rate | n |", "|---|---|---|---|"]
                for _, r in bsub.iterrows():
                    calib_sections.append(
                        f"| {int(r['decile'])} | {r['mean_p_hat']:.4f} | "
                        f"{r['emp_up_rate']:.4f} | {int(r['n'])} |")
                calib_sections.append("")

    lines += ["## Kalibreringstabeller (10 deciler af p̂, TEST-split)", ""]
    lines += calib_sections if calib_sections else ["Ingen fit-resultater at kalibrere."]
    lines.append("")
    lines += thin_lines
    lines.append("")

    # ---- kendte begrænsninger ---------------------------------------------
    lines += [
        "## Kendte begrænsninger", "",
        "- **Syntetisk oracle**: `oracle(t) = close(t - lag_s)` på et konstant-lag, "
        "1s-heartbeat-grid. Det er en forenkling af Chainlinks faktiske "
        "deviation-tærskel-drevne opdateringslogik (RTDS-observationen viser ~1 Hz "
        "kadence, men reelle opdateringer kan klumpe sig og springe over stille perioder).",
        "- **Spot-vs-futures-basis i lag-kalibreringen**: F2 (`btcusdt@bookTicker`) er "
        "Binance USDⓈ-M FUTURES-mid, mens klines i `fit` er SPOT. Lille basis "
        "accepteres i v1 (SPEC M2).",
        "- **Ingen OFI/tape/likvidationsfeatures** i dette bootstrap — kun "
        "`delta_bn_bp`, `delta_or_bp`, `tau` og `rv60`. SPEC §2 BRAIN nævner også "
        "`ofi_10s`, `tape_imb_30s`, `liq_cascade_flag`, som først kommer med "
        "forward recorder-data (M3).",
        "- **Markeds-mid-sammenligning hører til M3/Gate A**: denne rapport "
        "sammenligner KUN modellen mod to interne baselines (konstant 0,5 og "
        "train-base-rate) — IKKE mod Polymarkets faktiske bog-mid, som kræver "
        "recorded F3-data og er selve Gate A-kravet (SPEC §4/M3: slå mid i ≥2/3 bånd, OOS).",
        "- **Lag-kalibreringen er v1**: ét statisk `archive_smoke.sqlite`-vindue "
        "(~11-12 minutter) bruges til at vælge ét enkelt globalt `lag_s`, anvendt "
        "uændret på både BTC og ETH i `fit`. Et rigere, længere recorder-arkiv vil "
        "give en mere robust kalibrering (og evt. per-asset lag).",
        "- **Brier-forspringet her er delvist mekanisk, ikke en bevist edge**: "
        "fordi det syntetiske orakel er DEFINERET som `close(t - lag_s)` — dvs. "
        "en eksakt, støjfri funktion af Binance selv — er `delta_bn_bp` næsten "
        "tautologisk informativ om `S_close` i DENNE opsætning. Et rigtigt "
        "Chainlink-orakel har egen deviation-tærskel-drevet støj/opdaterings-"
        "logik, som ikke er til stede her. De flotte Brier-tal validerer altså "
        "primært at PIPELINE'N (binning + Laplace + isotonic + evaluering) "
        "virker korrekt end-to-end — IKKE at modellen slår markedet på rigtige "
        "data. Det reelle edge-bevis kommer først med Gate A (M3): BRAIN "
        "re-fittet på forward recorder-data og målt mod Chainlink OG "
        "Polymarkets bog-mid, out-of-sample.",
        "",
    ]

    out = Path(args.out)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"skrev {out}" + ("" if any_windows else " (uden fit-resultater — kør 'fit' først)"))
    return 0


# ==========================================================================
# CLI
# ==========================================================================

def cmd_all(args) -> int:
    ns = argparse.Namespace(zips_dir=str(DEFAULT_ZIPS_DIR), out_dir=str(DEFAULT_KLINES_DIR),
                            delete_zips=args.delete_zips)
    cmd_consolidate(ns)

    ns = argparse.Namespace(archive=str(DEFAULT_ARCHIVE), out=str(DEFAULT_LAG_FILE),
                            max_lag=args.max_lag)
    cmd_lag(ns)  # ok at fejle (fx manglende arkiv) — fit falder tilbage til default lag_s

    ns = argparse.Namespace(config=args.config, klines_dir=str(DEFAULT_KLINES_DIR),
                            lag_file=str(DEFAULT_LAG_FILE), out_dir=str(DEFAULT_BACKTEST_DIR))
    cmd_fit(ns)

    ns = argparse.Namespace(config=args.config, klines_dir=str(DEFAULT_KLINES_DIR),
                            lag_file=str(DEFAULT_LAG_FILE), backtest_dir=str(DEFAULT_BACKTEST_DIR),
                            out=str(DEFAULT_REPORT))
    return cmd_report(ns)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="BONESAW M2 — backtest-bootstrap")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("consolidate", help="Binance kline-zips -> parquet")
    p.add_argument("--zips-dir", default=str(DEFAULT_ZIPS_DIR))
    p.add_argument("--out-dir", default=str(DEFAULT_KLINES_DIR))
    p.add_argument("--delete-zips", action="store_true",
                    help="Slet zips efter konsolidering (default: behold)")

    p = sub.add_parser("lag", help="Kalibrér oracle-lag mod archive_smoke.sqlite")
    p.add_argument("--archive", default=str(DEFAULT_ARCHIVE))
    p.add_argument("--out", default=str(DEFAULT_LAG_FILE))
    p.add_argument("--max-lag", type=int, default=10)

    p = sub.add_parser("fit", help="Fit BRAIN v0 pr. symbol")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--klines-dir", default=str(DEFAULT_KLINES_DIR))
    p.add_argument("--lag-file", default=str(DEFAULT_LAG_FILE))
    p.add_argument("--out-dir", default=str(DEFAULT_BACKTEST_DIR))

    p = sub.add_parser("report", help="Skriv backtest_report.md")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--klines-dir", default=str(DEFAULT_KLINES_DIR))
    p.add_argument("--lag-file", default=str(DEFAULT_LAG_FILE))
    p.add_argument("--backtest-dir", default=str(DEFAULT_BACKTEST_DIR))
    p.add_argument("--out", default=str(DEFAULT_REPORT))

    p = sub.add_parser("all", help="consolidate -> lag -> fit -> report")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--delete-zips", action="store_true")
    p.add_argument("--max-lag", type=int, default=10)

    return ap


def main() -> int:
    args = build_parser().parse_args()
    dispatch = {"consolidate": cmd_consolidate, "lag": cmd_lag, "fit": cmd_fit,
                "report": cmd_report, "all": cmd_all}
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
