# Backtest-rapport (M2) — BONESAW

Genereret 2026-07-30T13:51:48Z af `research/backtest.py report`. Bootstrap-rapport til M2 (SPEC §4) — INGEN pass/fail-gate; det kommer først med Gate A (M3).

## Datadækning

| Symbol | Zip-filer | Rækker | Dækning | Manglende dage | Vinduer (gyldige) | Vinduer droppet |
|---|---|---|---|---|---|---|
| BTC | 151 | 13046400 | 2026-03-01 .. 2026-07-29 | ingen | 43486 | 2 |
| ETH | 151 | 13046400 | 2026-03-01 .. 2026-07-29 | ingen | 43486 | 2 |

## Oracle-lag-kalibrering

lag_s = **2** sekunder, korrelation = 0.692976, n = 685 overlappende sekunder. Kilde: `archive_smoke.sqlite`, beregnet 2026-07-30T13:48:53Z.

Kendt begrænsning: lag-kalibreringen bruger F2 (Binance USDⓈ-M FUTURES bookTicker-mid) som Binance-side, mens fit-trinnet bruger SPOT-klines. Lille spot/futures-basis accepteres i v1 (se 'Kendte begrænsninger' nedenfor).

## Brier-score per symbol × tau-bånd (TEST-split)

Model = binned empirisk + Laplace + isotonic (BRAIN v0). To baselines: konstant 0,5 og train-base-rate for båndet. Lavere Brier er bedre.

### BTC

| Tau-bånd | n | Brier (model) | Brier (konstant 0,5) | Brier (train-base-rate) | Base-rate |
|---|---|---|---|---|---|
| 240-120 | 34788 | 0.19586 | 0.25000 | 0.25000 | 0.49941 |
| 120-30 | 34788 | 0.12974 | 0.25000 | 0.25000 | 0.49941 |
| 30-0 | 26091 | 0.08002 | 0.25000 | 0.25000 | 0.49941 |

*(Base-rate ligger typisk meget tæt på 0,5 for 5m BTC/ETH-vinduer — derfor ligner de to baselines hinanden; det er i sig selv et empirisk fund, ikke en fejl i tabellen.)*

### ETH

| Tau-bånd | n | Brier (model) | Brier (konstant 0,5) | Brier (train-base-rate) | Base-rate |
|---|---|---|---|---|---|
| 240-120 | 34788 | 0.18965 | 0.25000 | 0.25001 | 0.49932 |
| 120-30 | 34788 | 0.11170 | 0.25000 | 0.25001 | 0.49932 |
| 30-0 | 26091 | 0.05293 | 0.25000 | 0.25001 | 0.49932 |

*(Base-rate ligger typisk meget tæt på 0,5 for 5m BTC/ETH-vinduer — derfor ligner de to baselines hinanden; det er i sig selv et empirisk fund, ikke en fejl i tabellen.)*

## Kalibreringstabeller (10 deciler af p̂, TEST-split)

### BTC

**Bånd 240-120**

| Decil | Middel p̂ | Empirisk up-rate | n |
|---|---|---|---|
| 0 | 0.1495 | 0.1374 | 4511 |
| 1 | 0.2677 | 0.2553 | 2828 |
| 2 | 0.3382 | 0.3080 | 4403 |
| 3 | 0.4135 | 0.4195 | 3280 |
| 4 | 0.4953 | 0.5032 | 4205 |
| 5 | 0.5500 | 0.5784 | 2787 |
| 6 | 0.6101 | 0.6109 | 2925 |
| 7 | 0.6720 | 0.6880 | 2926 |
| 8 | 0.7416 | 0.7741 | 3604 |
| 9 | 0.8727 | 0.8858 | 3319 |

**Bånd 120-30**

| Decil | Middel p̂ | Empirisk up-rate | n |
|---|---|---|---|
| 0 | 0.0323 | 0.0200 | 4097 |
| 1 | 0.1182 | 0.0978 | 3672 |
| 2 | 0.2091 | 0.1914 | 4201 |
| 3 | 0.3274 | 0.3058 | 3188 |
| 4 | 0.4640 | 0.4648 | 3746 |
| 5 | 0.6387 | 0.6337 | 2973 |
| 6 | 0.7244 | 0.7694 | 2663 |
| 7 | 0.8164 | 0.8442 | 3807 |
| 8 | 0.9154 | 0.9477 | 3558 |
| 9 | 0.9792 | 0.9892 | 2883 |

**Bånd 30-0**

| Decil | Middel p̂ | Empirisk up-rate | n |
|---|---|---|---|
| 0 | 0.0036 | 0.0015 | 2744 |
| 1 | 0.0299 | 0.0232 | 2498 |
| 2 | 0.0855 | 0.0695 | 2691 |
| 3 | 0.1786 | 0.1633 | 3503 |
| 4 | 0.4133 | 0.4188 | 2340 |
| 5 | 0.7002 | 0.7174 | 2484 |
| 6 | 0.8641 | 0.8936 | 2547 |
| 7 | 0.9311 | 0.9544 | 2941 |
| 8 | 0.9836 | 0.9954 | 2189 |
| 9 | 0.9981 | 1.0000 | 2154 |

### ETH

**Bånd 240-120**

| Decil | Middel p̂ | Empirisk up-rate | n |
|---|---|---|---|
| 0 | 0.1323 | 0.1254 | 4697 |
| 1 | 0.2438 | 0.2446 | 2911 |
| 2 | 0.3038 | 0.3133 | 3112 |
| 3 | 0.3891 | 0.3962 | 3824 |
| 4 | 0.4705 | 0.5053 | 3295 |
| 5 | 0.5470 | 0.5695 | 3926 |
| 6 | 0.6235 | 0.6203 | 2602 |
| 7 | 0.6879 | 0.6896 | 4008 |
| 8 | 0.7941 | 0.8106 | 3479 |
| 9 | 0.8956 | 0.9264 | 2934 |

**Bånd 120-30**

| Decil | Middel p̂ | Empirisk up-rate | n |
|---|---|---|---|
| 0 | 0.0164 | 0.0074 | 3502 |
| 1 | 0.0662 | 0.0663 | 4226 |
| 2 | 0.1417 | 0.1297 | 3006 |
| 3 | 0.2536 | 0.2487 | 4114 |
| 4 | 0.4294 | 0.4665 | 2759 |
| 5 | 0.6097 | 0.6123 | 3732 |
| 6 | 0.7851 | 0.8126 | 3932 |
| 7 | 0.8936 | 0.9073 | 2729 |
| 8 | 0.9523 | 0.9664 | 3775 |
| 9 | 0.9877 | 0.9950 | 3013 |

**Bånd 30-0**

| Decil | Middel p̂ | Empirisk up-rate | n |
|---|---|---|---|
| 0 | 0.0012 | 0.0004 | 2716 |
| 1 | 0.0091 | 0.0045 | 3306 |
| 2 | 0.0286 | 0.0237 | 2071 |
| 3 | 0.0950 | 0.0932 | 3121 |
| 4 | 0.3631 | 0.3953 | 1930 |
| 5 | 0.7340 | 0.7592 | 2853 |
| 6 | 0.9305 | 0.9446 | 2583 |
| 7 | 0.9811 | 0.9914 | 2315 |
| 8 | 0.9974 | 0.9966 | 2981 |
| 9 | 0.9990 | 1.0000 | 2215 |


## Tynd-bin-andel (celler med n_train < min_bin_n)

`brain.min_bin_n` = 200 (config.yaml). Tynd-bin-andel er andelen af TEST-rækker hvis celle så færre end dette antal TRAIN-observationer (0 tæller også med — 'ukendt celle').

| Symbol | Bånd | Tynd-andel | n (test) |
|---|---|---|---|
| BTC | 240-120 | 0.001 | 34788 |
| BTC | 120-30 | 0.001 | 34788 |
| BTC | 30-0 | 0.003 | 26091 |
| BTC | **samlet** | **0.002** | 95667 |
| ETH | 240-120 | 0.001 | 34788 |
| ETH | 120-30 | 0.001 | 34788 |
| ETH | 30-0 | 0.001 | 26091 |
| ETH | **samlet** | **0.001** | 95667 |

## Kendte begrænsninger

- **Syntetisk oracle**: `oracle(t) = close(t - lag_s)` på et konstant-lag, 1s-heartbeat-grid. Det er en forenkling af Chainlinks faktiske deviation-tærskel-drevne opdateringslogik (RTDS-observationen viser ~1 Hz kadence, men reelle opdateringer kan klumpe sig og springe over stille perioder).
- **Spot-vs-futures-basis i lag-kalibreringen**: F2 (`btcusdt@bookTicker`) er Binance USDⓈ-M FUTURES-mid, mens klines i `fit` er SPOT. Lille basis accepteres i v1 (SPEC M2).
- **Ingen OFI/tape/likvidationsfeatures** i dette bootstrap — kun `delta_bn_bp`, `delta_or_bp`, `tau` og `rv60`. SPEC §2 BRAIN nævner også `ofi_10s`, `tape_imb_30s`, `liq_cascade_flag`, som først kommer med forward recorder-data (M3).
- **Markeds-mid-sammenligning hører til M3/Gate A**: denne rapport sammenligner KUN modellen mod to interne baselines (konstant 0,5 og train-base-rate) — IKKE mod Polymarkets faktiske bog-mid, som kræver recorded F3-data og er selve Gate A-kravet (SPEC §4/M3: slå mid i ≥2/3 bånd, OOS).
- **Lag-kalibreringen er v1**: ét statisk `archive_smoke.sqlite`-vindue (~11-12 minutter) bruges til at vælge ét enkelt globalt `lag_s`, anvendt uændret på både BTC og ETH i `fit`. Et rigere, længere recorder-arkiv vil give en mere robust kalibrering (og evt. per-asset lag).
- **Brier-forspringet her er delvist mekanisk, ikke en bevist edge**: fordi det syntetiske orakel er DEFINERET som `close(t - lag_s)` — dvs. en eksakt, støjfri funktion af Binance selv — er `delta_bn_bp` næsten tautologisk informativ om `S_close` i DENNE opsætning. Et rigtigt Chainlink-orakel har egen deviation-tærskel-drevet støj/opdaterings-logik, som ikke er til stede her. De flotte Brier-tal validerer altså primært at PIPELINE'N (binning + Laplace + isotonic + evaluering) virker korrekt end-to-end — IKKE at modellen slår markedet på rigtige data. Det reelle edge-bevis kommer først med Gate A (M3): BRAIN re-fittet på forward recorder-data og målt mod Chainlink OG Polymarkets bog-mid, out-of-sample.

