#!/usr/bin/env python3
"""Soak-rapport (SPEC M1 DoD): auto-genereret og grøn/rød.

Læser hele event-loggen (parquet-rotationer + resterende SQLite), beregner
gap-tid per feed, S_open-dækning, RSS-trend og disk-vagt-hændelser, og fælder
dom mod tærsklerne i config.yaml (soak.*). Exit 0 = GRØN, 1 = RØD.

Kørsel:  python3 research/soak_report.py [--config config.yaml] [--out research/soak_report.md]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from src.modes import load_config  # noqa: E402

FEEDS = ("f1", "f2", "f3")


def load_events(sqlite_file: Path, parquet_dir: Path):
    """-> dict source -> np.array(ts_wall, sorteret), plus alle sys/s_open-rækker."""
    ts_by_source: dict[str, list[np.ndarray]] = {}
    special: dict[str, list[dict]] = {"sys": [], "s_open": [], "unpriceable": [],
                                      "s_open_late": []}
    files = sorted(parquet_dir.rglob("*.parquet")) if parquet_dir.exists() else []
    if files:
        import pyarrow.parquet as pq
        for f in files:
            t = pq.read_table(f, columns=["ts_wall", "source", "payload"])
            src = t.column("source").to_numpy(zero_copy_only=False)
            ts = t.column("ts_wall").to_numpy()
            for s in np.unique(src):
                ts_by_source.setdefault(str(s), []).append(ts[src == s])
            if any(s in special for s in np.unique(src)):
                pl = t.column("payload").to_pylist()
                for s, w, p in zip(src, ts, pl):
                    if str(s) in special:
                        special[str(s)].append({"ts_wall": float(w), **json.loads(p)})
    if sqlite_file.exists():
        con = sqlite3.connect(sqlite_file)
        for s, w, p in con.execute("SELECT source, ts_wall, payload FROM events ORDER BY id"):
            ts_by_source.setdefault(s, []).append(np.array([w]))
            if s in special:
                special[s].append({"ts_wall": float(w), **json.loads(p)})
        con.close()
    merged = {s: np.sort(np.concatenate(chunks)) for s, chunks in ts_by_source.items()}
    return merged, special


def gap_stats(ts: np.ndarray, span: tuple[float, float], stale_s: float) -> dict:
    """Gap = perioder uden livstegn > stale_s, inkl. leading/trailing."""
    t0, t1 = span
    if ts.size == 0:
        return {"n_msgs": 0, "gap_s": t1 - t0, "gap_pct": 100.0, "n_gaps": 1,
                "max_gap_s": t1 - t0}
    points = np.concatenate(([t0], ts, [t1]))
    deltas = np.diff(points)
    gaps = deltas[deltas > stale_s] - stale_s  # tid UDOVER taersklen taeller som gap
    total = float(gaps.sum())
    return {"n_msgs": int(ts.size), "gap_s": round(total, 1),
            "gap_pct": round(100 * total / max(t1 - t0, 1e-9), 4),
            "n_gaps": int(gaps.size), "max_gap_s": round(float(deltas.max()), 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--out", default=str(HERE / "soak_report.md"))
    ap.add_argument("--label", default="soak")
    args = ap.parse_args()
    cfg = load_config(args.config)
    sqlite_file = ROOT / cfg.recorder.sqlite_file
    parquet_dir = ROOT / cfg.recorder.data_dir / "parquet"

    events, special = load_events(sqlite_file, parquet_dir)
    if not events:
        print("Ingen events fundet — har recorderen kørt?", file=sys.stderr)
        return 1
    all_ts = np.concatenate(list(events.values()))
    span = (float(all_ts.min()), float(all_ts.max()))
    span_s = span[1] - span[0]
    stale = cfg.feeds.stale_gap_s.model_dump()

    feed_rows, verdicts = [], []
    for f in FEEDS:
        st = gap_stats(events.get(f, np.array([])), span, stale[f])
        ok = st["gap_pct"] < cfg.soak.gap_pct_max
        verdicts.append((f"{f} gap-tid < {cfg.soak.gap_pct_max}%", ok,
                         f"{st['gap_pct']}% ({st['n_gaps']} gaps, maks {st['max_gap_s']}s)"))
        feed_rows.append((f, st))

    # S_open-daekning
    s_open = special["s_open"]
    unpr = special["unpriceable"]
    by_asset: dict[str, list[int]] = {}
    for ev in s_open:
        by_asset.setdefault(ev["asset"], [0, 0])[0] += 1
    for ev in unpr:
        by_asset.setdefault(ev["asset"], [0, 0])[1] += 1
    n_windows = sum(v[0] + v[1] for v in by_asset.values())
    n_unpr = sum(v[1] for v in by_asset.values())
    if n_windows:
        verdicts.append(("S_open fanget for registrerede vinduer",
                         n_unpr == 0, f"{n_windows - n_unpr}/{n_windows} (UNPRICEABLE: {n_unpr})"))

    # RSS-trend: foerste vs sidste times median
    met = [ev for ev in special["sys"] if ev.get("type") == "metrics" and ev.get("rss_mb")]
    rss_note = "for få metrics-punkter til trend"
    if len(met) >= 4:
        ts_m = np.array([m["ts_wall"] for m in met])
        rss = np.array([m["rss_mb"] for m in met])
        first = rss[ts_m <= span[0] + 3600]
        last = rss[ts_m >= span[1] - 3600]
        if first.size and last.size:
            growth = 100 * (np.median(last) - np.median(first)) / np.median(first)
            ok = growth <= cfg.soak.mem_growth_max_pct
            rss_note = f"{np.median(first):.0f} → {np.median(last):.0f} MB ({growth:+.1f}%)"
            verdicts.append((f"RSS-vækst ≤ {cfg.soak.mem_growth_max_pct}%", ok, rss_note))

    disk_evs = [e for e in special["sys"] if e.get("type") in ("disk_halt", "disk_resume")]
    disconnects = {}
    for e in special["sys"]:
        if e.get("type") == "ws_disconnect":
            disconnects[e.get("feed", "?")] = disconnects.get(e.get("feed", "?"), 0) + 1
    last_stop = [e for e in special["sys"] if e.get("type") == "recorder_stop"]
    dropped = last_stop[-1].get("dropped", 0) if last_stop else 0
    verdicts.append(("ingen droppede events (kø-overløb)", dropped == 0, str(dropped)))

    green = all(ok for _, ok, _ in verdicts)
    gen = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    L = [f"# Soak-rapport ({args.label}) — {'🟢 GRØN' if green else '🔴 RØD'}", "",
         f"Genereret {gen} af `research/soak_report.py`. "
         f"Datavindue: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(span[0]))} → "
         f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(span[1]))} "
         f"({span_s / 3600:.2f} timer).", "",
         "| Kriterium | Status | Målt |", "|---|---|---|"]
    for name, ok, note in verdicts:
        L.append(f"| {name} | {'🟢' if ok else '🔴'} | {note} |")
    L += ["", "## Feeds", "", "| Feed | Beskeder | Gap-tid | Gap-% | Antal gaps | Maks gap | WS-genforbindelser |",
          "|---|---|---|---|---|---|---|"]
    for f, st in feed_rows:
        L.append(f"| {f} | {st['n_msgs']} | {st['gap_s']}s | {st['gap_pct']}% | "
                 f"{st['n_gaps']} | {st['max_gap_s']}s | {disconnects.get(f, 0)} |")
    L += ["", "## S_open per asset", "", "| Asset | S_open | UNPRICEABLE |", "|---|---|---|"]
    for a in sorted(by_asset):
        L.append(f"| {a} | {by_asset[a][0]} | {by_asset[a][1]} |")
    if special["s_open_late"]:
        L.append(f"\nSene in-window-ticks efter UNPRICEABLE-afgørelse: "
                 f"{len(special['s_open_late'])} (vinduerne forblev unpriceable — fejlsikkert).")
    L += ["", "## Disk-vagt", ""]
    L.append("Ingen disk-hændelser under kørslen." if not disk_evs else
             "\n".join(f"- {e['type']} @ {time.strftime('%H:%M:%SZ', time.gmtime(e['ts_wall']))} "
                       f"(fri: {e.get('free_gb')} GB)" for e in disk_evs))
    L.append("\nDisk-vagtens stop/genoptag-logik er derudover dækket af "
             "`tests/test_disk_guard.py` (simuleret lav diskplads).")
    out = Path(args.out)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"{'GRØN' if green else 'RØD'} — skrev {out}")
    return 0 if green else 1


if __name__ == "__main__":
    sys.exit(main())
