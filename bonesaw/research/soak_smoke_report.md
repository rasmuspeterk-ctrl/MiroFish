# Soak-rapport (smoke-11.5min (CCR-container, 3s grace)) — 🔴 RØD

Genereret 2026-07-28T01:48:49Z af `research/soak_report.py`. Datavindue: 2026-07-28T01:37:05Z → 2026-07-28T01:48:35Z (0.19 timer).

| Kriterium | Status | Målt |
|---|---|---|
| f1 gap-tid < 0.1% | 🔴 | 0.2243% (1 gaps, maks 6.5s) |
| f2 gap-tid < 0.1% | 🟢 | 0.0% (0 gaps, maks 1.0s) |
| f3 gap-tid < 0.1% | 🟢 | 0.0% (0 gaps, maks 2.3s) |
| S_open fanget for registrerede vinduer | 🟢 | 12/12 (UNPRICEABLE: 0) |
| RSS-vækst ≤ 15.0% | 🟢 | 67 → 67 MB (+0.0%) |
| ingen droppede events (kø-overløb) | 🟢 | 0 |

## Feeds

| Feed | Beskeder | Gap-tid | Gap-% | Antal gaps | Maks gap | WS-genforbindelser |
|---|---|---|---|---|---|---|
| f1 | 3946 | 1.5s | 0.2243% | 1 | 6.5s | 0 |
| f2 | 861496 | 0.0s | 0.0% | 0 | 1.0s | 0 |
| f3 | 632458 | 0.0s | 0.0% | 0 | 2.3s | 3 |

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
