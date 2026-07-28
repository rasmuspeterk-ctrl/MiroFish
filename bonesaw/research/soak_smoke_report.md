# Soak-rapport (smoke-12min (CCR-container)) — 🔴 RØD

Genereret 2026-07-28T01:07:28Z af `research/soak_report.py`. Datavindue: 2026-07-28T00:53:24Z → 2026-07-28T01:06:49Z (0.22 timer).

| Kriterium | Status | Målt |
|---|---|---|
| f1 gap-tid < 0.1% | 🔴 | 9.6145% (3 gaps, maks 77.3s) |
| f2 gap-tid < 0.1% | 🔴 | 1.0388% (2 gaps, maks 10.0s) |
| f3 gap-tid < 0.1% | 🟢 | 0.0% (0 gaps, maks 10.0s) |
| S_open fanget for registrerede vinduer | 🟢 | 18/18 (UNPRICEABLE: 0) |
| RSS-vækst ≤ 15.0% | 🟢 | 66 → 66 MB (+0.0%) |
| ingen droppede events (kø-overløb) | 🔴 | 8545 |

## Feeds

| Feed | Beskeder | Gap-tid | Gap-% | Antal gaps | Maks gap | WS-genforbindelser |
|---|---|---|---|---|---|---|
| f1 | 4070 | 77.5s | 9.6145% | 3 | 77.3s | 0 |
| f2 | 1409549 | 8.4s | 1.0388% | 2 | 10.0s | 0 |
| f3 | 1017003 | 0.0s | 0.0% | 0 | 10.0s | 5 |

## S_open per asset

| Asset | S_open | UNPRICEABLE |
|---|---|---|
| bnb | 3 | 0 |
| btc | 3 | 0 |
| doge | 3 | 0 |
| eth | 3 | 0 |
| sol | 3 | 0 |
| xrp | 3 | 0 |

## Disk-vagt

Ingen disk-hændelser under kørslen.

Disk-vagtens stop/genoptag-logik er derudover dækket af `tests/test_disk_guard.py` (simuleret lav diskplads).
