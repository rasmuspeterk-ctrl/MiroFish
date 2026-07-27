# BONESAW v1 — Build-spec til Claude Code

Mission i én sætning: Byg en fair value-drevet both-sides accumulator på Polymarkets
5-minutters crypto up/down binaries, der slår referencemodstanderen "bonereaper"
(wallet `0xeebde7a0e019a63e6b476eb425505b7b3e6eba30`, +$1,21M siden marts 2026)
på kalibrering og disciplin, med tre hårde gates der kan dræbe projektet gratis
før én dollar er i markedet.

Dette dokument er selvstændigt. Byg KUN det der står her. Én milepæl ad gangen.
Stop ved hver Definition of Done og afvent godkendelse.

---

## 0. Modstander-efterretning (grundlag for alle designvalg)

Målt på 1,34M af hans fills (25/3 til 16/4 2026, PR&R-analyse) plus live leaderboard:

| Parameter | Målt |
|---|---|
| Arketype | Fair value-drevet both-sides accumulator, IKKE quoter |
| Køb vs. salg | 99,8% køb. Ingen exits, alt holdes til resolution |
| Both-sides rate | 96,7%, anden side parret median 10 sek efter første |
| Ratio tung/let side | Median 2,81x. Ratioen er conviction-skiven |
| Ratio→win-rate | 1,0-1,5x: 59% → 5x+: 97,7% (monotont). Kalibreret model bekræftet |
| Kendt læk | Ratio 1,0-1,5x TABER (−$5,23/marked). Eneste røde bucket |
| Paired cost | Median $1,006. 47% under $1,00 (opnået ved legging over tid) |
| Ticket | Median $5,56, power-law, top 5% bærer 57% af kapital |
| Assets | BTC 86%, ETH 14%, resten ~0. Flanker (SOL/XRP/DOGE/BNB) ubesatte |
| Tempo | ~61.000 fills/døgn, ~644 markeder/døgn, peak 13-17 UTC |
| Resultat april | +$443.754 / 22 dage (~$20K/dag). Juli-rate: ~$5,9K/dag (kompression ~70%) |
| Værste marked | −$15.861 (begrunder per-market cap) |
| Værste dag | −$2.507 (begrunder daily loss-fuse) |

Vores tre forbedringer over ham:
1. Skip ratio < 1,5x (hans dokumenterede læk).
2. Stak filtrene: ratio ≥ 1,5x OG sub-$1 paired cost (utestet kombination i PR&R).
3. Flanke-option: live-asset vælges ved Gate B ud fra frisk konkurrent-data.

---

## 1. Ufravigelige principper

1. **Mode-flag styrer alt:** `RECORD` → `SHADOW` → `LIVE`. LIVE er en hård kodesti
   der nægter at starte uden gyldige gate-filer (§7). Ingen undtagelser.
2. **Ingen parametriske fordelinger i modellen.** Empirisk betinget frekvens med
   glatning. Begrundelse: normal vs. Student-t giver ±2 cents uenighed med
   regimeafhængigt fortegn, og hele edgen er 1-2 cent.
3. **Ingen exits på bogen.** Køb, hold, redeem. Modstander-valideret. Fjerner
   exit-adverse-selection og cancel-storme.
4. **Hot path skriver lokalt** (SQLite WAL + parquet). Netværks-DB kun som async
   batch-upload af aggregater. Aldrig netværkskald i beslutningssløjfen.
5. **Alle tærskler i `config.yaml`.** Ingen magiske tal i kode.
6. **Hver beslutning logges med fuldt input-snapshot** (model-P, bogtilstand,
   delta, tau, vol, fuses). Reproducerbarhed er forudsætningen for Gate A/B.
7. **Fejl-sikker default:** enhver uventet tilstand → ingen nye entries.

---

## 2. Systemmoduler og kontrakter

### CLOCK
- `window_ts = now - (now % 300)`. Slug: `{asset}-updown-5m-{window_ts}`.
- 15m-vinduer (`% 900`) bag feature-flag `enable_15m` (default false).
- Kender næste vindue før det åbner. Ingen markedssøgning nogensinde.

### FEED (tre kilder, hver med staleness-ur)
- **F1 Chainlink via Polymarkets WS** (`crypto_prices_chainlink`, btc/usd m.fl.).
  Settlement-sandheden. `S_open` = første tick med ts ≥ window boundary.
  Gap-regel: intet tick i [boundary, boundary+2s] → vinduet markeres
  `UNPRICEABLE`, ingen entries, logges.
- **F2 Binance USDⓈ-M futures WS:** `bookTicker` + `aggTrade` + `forceOrder`
  (likvidationer) + `depth@100ms` (til OFI). Det hurtige øje.
- **F3 Polymarket CLOB WS:** market channel for aktive vinduer (bog + fills).
- Recorder optager ALLE seks assets (BTC, ETH, SOL, XRP, DOGE, BNB). Billigt.

### RECORDER (voldgraven)
- Append-only event-log: `(ts_mono, ts_wall, source, payload_json)`.
- SQLite WAL til seneste time, parquet-rotation per time, disk-vagt (stop ved
  <5 GB fri). Mål: <0,1% gap-tid over 48 timers soak.

### BRAIN
- Features per (marked, tick): `delta_chainlink`, `delta_binance`, `tau`,
  `rv_ewma_60s` (1s log-returns), `ofi_10s`, `tape_imb_30s`, `liq_cascade_flag`.
- Model: binned empirisk P(Up) over (delta-bucket × tau-bucket × vol-regime)
  med Laplace-glatning, efterfulgt af isotonic regression-kalibrering.
- Output: `p_hat` + usikkerhed (n i bin). Lav n → ingen entry.
- Kalibreres på BTC først (mest data). Andre assets klones efter Gate B.

### HANDS
- `edge_net(side) = p_hat(side) − ask(side) − fees`.
- Entry KUN hvis: `edge_net > edge_min` OG projiceret paired cost ≤
  `paired_cost_max` OG planlagt ratio ≥ `ratio_min` OG alle fuses grønne
  OG tau > `endgame_cutoff_s` (undtagelse: `edge_net > endgame_edge_min`).
- Probes: små child-ordrer (respektér min 5 shares). Størrelse skaleres med
  edge og bogdybde, cap per ordre `probe_max_usdc`.
- **Pairing ved legging:** anden side købes når DENS `edge_net` klarer en
  reduceret tærskel (`pairing_edge_min < edge_min`), senest `pairing_max_s`
  efter første fill, ELLERS accepteres ensidet lager op til `oneside_cap_usdc`.
  Paired cost er et mål over vinduets levetid, ikke et øjebliks-arb-krav.
- Ratio-skiven: allokering tung/let side = monoton funktion af |p_hat − mid|,
  clamped til [ratio_min, ratio_max].

### REDEEMER (kapitalcyklussen)
- Poll resolved positions hvert `redeem_poll_s`, indløs vundne shares, frigør
  USDC. Alarm hvis uindløst værdi > `redeem_backlog_max_usdc`.

### FUSES (rækkefølge = prioritet; enhver rød fuse = ingen nye entries)
- F-1 Dagligt tab ≥ `daily_loss_pct` af bankroll → HALT resten af døgnet (UTC).
- F-2 |Chainlink − Binance| > `oracle_div_bp` i > `oracle_div_s` → FLAT + HALT.
- F-3 Per-market eksponering ≥ `market_cap_usdc` → ingen flere entries dér.
- F-4 Catalyst-blackout: ingen entries fra `blackout_min` før events i
  `calendar.yaml` (CPI, FOMC, NFP, PPI; vedligeholdes ugentligt manuelt).
- F-5 Feed-staleness > `staleness_ms` på F1 eller F2 → ingen entries.
- F-6 Runaway-vagt: fills/min > 3× rullende P95 → HALT + alarm.

### SCOREBOARD
- Dagligt pull af modstander-wallet + leaderboard top-10 via
  `data-api.polymarket.com/activity` (timestamp-cursor pagination, aldrig offset).
- Beregner: hans dagsrate, asset-mix, ratio-fordeling, om lækken er patchet.
- Diff mod baseline i §0. Alarm ved regimeskift. Ugentlig rapport: hans edge
  mod vores i delte vinduer.

---

## 3. Teknisk stack og repo-layout

Python 3.11+, asyncio, `websockets`, `py-clob-client` (officiel), `sqlite3`
(WAL), `pyarrow`, `pandas`, `numpy`, `scikit-learn` (kun isotonic), `pydantic`
(config), `structlog`. Ingen andre dependencies uden eksplicit begrundelse.

```
bonesaw/
  config.yaml            # alle tærskler (skabelon i §5)
  calendar.yaml          # macro-events, UTC-timestamps
  src/
    clock.py  feeds/  recorder.py  brain/  hands.py
    redeemer.py  fuses.py  scoreboard.py  main.py  modes.py
  research/
    backtest.py  gate_a_report.py  gate_b_report.py
  gates/                 # gate_a.json, gate_b.json, ARM (skrives af rapporter/menneske)
  data/                  # parquet + sqlite (gitignored)
  tests/
```

---

## 4. Milepæle. Byg i rækkefølge. Stop ved hver DoD.

### M0 — Verifikation (ingen afhængigheder, dag 1-2)
Script `research/verify_rules.py` der henter og skriver `VERIFIED.md`:
- [ ] Fee-skema og maker-rebate for 5m/15m crypto-markeder (CLOB API + docs)
- [ ] Tick size, min order (forventet 5 shares), prisgranularitet
- [ ] CLOB rate limits (ordrer/sek, WS-forbindelser)
- [ ] Resolution-regel verificeret: close ≥ open → Up (tie-reglen)
- [ ] Ping-måling mod clob.polymarket.com fra 3 VPS-regioner (manuel, dokumentér)
Manuelt (Rasmus): Polymarket-konto, API-nøgler, USDC-wallet med KUN arbejdskapital.
**DoD:** `VERIFIED.md` komplet. Økonomi-regnearket i Gate B bruger DISSE tal.

### M1 — RECORDER (uge 1)
- [ ] F1+F2+F3 live for alle 6 assets, lokal skrivning, parquet-rotation
- [ ] S_open-fangst med gap-regel testet (simulér manglende tick)
- [ ] 48 timers soak: <0,1% gap-tid, ingen memory-vækst, disk-vagt testet
- [ ] Async batch-upload af time-aggregater til Supabase (fejltolerant, valgfri)
**DoD:** soak-rapport auto-genereret og grøn.

### M2 — Backtest-bootstrap (uge 2)
- [ ] Download Binance 1s-klines BTC/ETH, marts → nu
- [ ] Oracle-lag-model: simulér Chainlink-adfærd (deviation + heartbeat) ovenpå
      Binance; kalibrér mod recorded F1-data så snart 7+ dage findes
- [ ] Første BRAIN-fit + Brier-baseline per tau-bånd
**DoD:** `research/backtest.py` kører end-to-end, rapport skrevet.

### M3 — GATE A: modellen (uge 3-4)
- [ ] BRAIN re-fittet på forward recorder-data (≥14 dage), out-of-sample split
- [ ] `gate_a_report.py`: Brier per tau-bånd (240-120s / 120-30s / 30-0s) mod
      markedets MID på samme timestamp
- [ ] **PASS-krav: slå mid i ≥2 af 3 bånd, OOS.** Skriver `gates/gate_a.json`
**DoD:** rapport genereret. FAIL = projektet stopper her. Ingen diskussion.

### M4 — SHADOW + GATE B: økonomien (uge 5-6)
- [ ] Fuld pipeline i SHADOW: eksekvering stubbet mod recorded bog med
      latency-straf 250-500 ms (config), fees fra `VERIFIED.md` indregnet
- [ ] Fill-model: der handles mod bogens faktiske dybde på t+straf
- [ ] `gate_b_report.py`: netto-edge per marked, per asset, per session
- [ ] **PASS-krav: netto-edge ≥ 0,3% af deployeret kapital per marked over
      ≥10 shadow-dage.** Skriver `gates/gate_b.json`
- [ ] Asset-beslutning: scoreboard + puller-data afgør live-asset (BTC-hovedvej
      eller ubesat flanke). Dokumenteres i `gates/asset_decision.md`
**DoD:** rapport grøn ELLER projektet stopper.

### M5 — LIVE (uge 7+)
- [ ] Kræver `gate_a.json` + `gate_b.json` (pass=true) + manuel `gates/ARM`-fil
- [ ] Bankroll: $500. Ét asset. Alle fuses aktive fra første ordre
- [ ] **GATE C efter 30 dage:** P/L inden for 2× shadow-konfidensinterval OG
      netto ≥ $100. Ellers permanent shutdown, ingen genstart uden ny M3-M4
- [ ] Driftsloft: 8 timers menneskelig opsyn per uge, hårdt
**DoD:** Gate C-rapport efter 30 dage.

---

## 5. config.yaml-skabelon (defaults, alle kan overstyres)

```yaml
mode: RECORD                # RECORD | SHADOW | LIVE
assets_record: [btc, eth, sol, xrp, doge, bnb]
asset_live: null            # sættes af asset_decision.md ved M4
enable_15m: false

brain:
  min_bin_n: 200            # under dette: ingen entry
  rv_halflife_s: 60

hands:
  edge_min: 0.02            # 2 cent netto foer entry
  pairing_edge_min: 0.005
  pairing_max_s: 60
  oneside_cap_usdc: 25
  ratio_min: 1.5            # hans laek: aldrig 1.0-1.5x
  ratio_max: 8.0
  paired_cost_max: 1.03     # ideal < 1.00
  probe_max_usdc: 10
  endgame_cutoff_s: 20
  endgame_edge_min: 0.08

fuses:
  daily_loss_pct: 5
  oracle_div_bp: 10
  oracle_div_s: 5
  market_cap_usdc: 50
  blackout_min: 10
  staleness_ms: 1500

redeemer:
  redeem_poll_s: 60
  redeem_backlog_max_usdc: 200

execution:
  latency_penalty_ms: [250, 500]   # shadow-straf, interval
```

---

## 6. Hvad Claude Code IKKE må (non-goals, absolutte)

- Aldrig aktivere LIVE eller skrive ARM-filen. Kun mennesket gør det.
- Aldrig tilføje exits/salgslogik, parametriske fordelinger, ML-modeller udover
  binning+isotonic, eller nye assets uden en ny beslutning fra Rasmus.
- Aldrig røre private nøgler i kode eller logs. `.env`, gitignored.
- Ingen copy-trading af modstanderen. Scoreboard er efterretning, ikke signal.
- Ingen Coinglass eller betalte datafeeds i v1. Stacken i §2 er komplet.
- Ingen scope-udvidelser "mens vi er i gang". Parkér idéer i `PARKING.md`.

---

## 7. Gate-mekanik (hård kodesti)

`main.py` i LIVE-mode: læs `gates/gate_a.json`, `gates/gate_b.json`,
`gates/ARM`. Mangler én, pass=false, eller ARM ældre end 7 dage → exit(1)
med forklaring. Denne kontrol må aldrig kunne slås fra via config.

---

## 8. Åbne spørgsmål (tagget med ejer)

| Spørgsmål | Ejer | Blokerer |
|---|---|---|
| Frisk juli-data fra `bonereaper_pull.py` (dagsrate, læk-status, flanker) | Rasmus | M4 asset-beslutning |
| Fee/rebate-tal | M0-script | M4 økonomi |
| VPS-region efter ping-test | Rasmus | M5 |
| Rewards-program: kvalificerer taker-flow overhovedet? | M0-script | Kun opside, blokerer intet |

---

## Startkommando til Claude Code

"Læs SPEC.md i sin helhed. Byg M0. Stop ved Definition of Done og vis mig
VERIFIED.md før du fortsætter til M1."
