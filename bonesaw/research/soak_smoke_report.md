# Soak-rapport (smoke-11min (CCR-container, ren)) — 🔴 RØD

Genereret 2026-07-28T01:35:49Z af `research/soak_report.py`. Datavindue: 2026-07-28T01:24:32Z → 2026-07-28T01:35:32Z (0.18 timer).

| Kriterium | Status | Målt |
|---|---|---|
| f1 gap-tid < 0.1% | 🟢 | 0.0% (0 gaps, maks 2.5s) |
| f2 gap-tid < 0.1% | 🟢 | 0.0% (0 gaps, maks 1.0s) |
| f3 gap-tid < 0.1% | 🟢 | 0.0% (0 gaps, maks 2.3s) |
| S_open fanget for registrerede vinduer | 🔴 | 15/18 (UNPRICEABLE: 3) |
| RSS-vækst ≤ 15.0% | 🟢 | 67 → 67 MB (+0.0%) |
| ingen droppede events (kø-overløb) | 🟢 | 0 |

## Feeds

| Feed | Beskeder | Gap-tid | Gap-% | Antal gaps | Maks gap | WS-genforbindelser |
|---|---|---|---|---|---|---|
| f1 | 3761 | 0.0s | 0.0% | 0 | 2.5s | 0 |
| f2 | 820345 | 0.0s | 0.0% | 0 | 1.0s | 0 |
| f3 | 580946 | 0.0s | 0.0% | 0 | 2.3s | 4 |

## S_open per asset

| Asset | S_open | UNPRICEABLE |
|---|---|---|
| bnb | 2 | 1 |
| btc | 2 | 1 |
| doge | 3 | 0 |
| eth | 3 | 0 |
| sol | 2 | 1 |
| xrp | 3 | 0 |

Sene in-window-ticks efter UNPRICEABLE-afgørelse: 6 (vinduerne forblev unpriceable — fejlsikkert).

## Disk-vagt

Ingen disk-hændelser under kørslen.

Disk-vagtens stop/genoptag-logik er derudover dækket af `tests/test_disk_guard.py` (simuleret lav diskplads).
