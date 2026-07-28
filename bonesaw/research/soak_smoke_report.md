# Soak-rapport (smoke-12min ren (CCR-container)) — 🔴 RØD

Genereret 2026-07-28T01:22:41Z af `research/soak_report.py`. Datavindue: 2026-07-28T01:10:18Z → 2026-07-28T01:22:28Z (0.20 timer).

| Kriterium | Status | Målt |
|---|---|---|
| f1 gap-tid < 0.1% | 🔴 | 1.2271% (3 gaps, maks 10.0s) |
| f2 gap-tid < 0.1% | 🔴 | 0.8638% (2 gaps, maks 10.0s) |
| f3 gap-tid < 0.1% | 🟢 | 0.0% (0 gaps, maks 10.0s) |
| S_open fanget for registrerede vinduer | 🟢 | 12/12 (UNPRICEABLE: 0) |
| RSS-vækst ≤ 15.0% | 🟢 | 74 → 74 MB (+0.0%) |
| ingen droppede events (kø-overløb) | 🟢 | 0 |

## Feeds

| Feed | Beskeder | Gap-tid | Gap-% | Antal gaps | Maks gap | WS-genforbindelser |
|---|---|---|---|---|---|---|
| f1 | 4126 | 9.0s | 1.2271% | 3 | 10.0s | 0 |
| f2 | 1265538 | 6.3s | 0.8638% | 2 | 10.0s | 2 |
| f3 | 894586 | 0.0s | 0.0% | 0 | 10.0s | 4 |

## S_open per asset

| Asset | S_open | UNPRICEABLE |
|---|---|---|
| bnb | 2 | 0 |
| btc | 2 | 0 |
| doge | 2 | 0 |
| eth | 2 | 0 |
| sol | 2 | 0 |
| xrp | 2 | 0 |

## Disk-vagt

Ingen disk-hændelser under kørslen.

Disk-vagtens stop/genoptag-logik er derudover dækket af `tests/test_disk_guard.py` (simuleret lav diskplads).
