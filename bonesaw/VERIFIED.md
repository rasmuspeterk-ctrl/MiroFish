# VERIFIED.md — M0-verifikationsrapport (BONESAW v1)

Genereret: 2026-07-27T22:35:35Z af `research/verify_rules.py`. Alle tal er hentet live fra kilderne angivet per sektion; rå svar ligger i `research/raw/`. Gate B-økonomien SKAL bruge tallene herfra (SPEC §4/M0).

## DoD-status

| M0-punkt (SPEC §4) | Status | Kort resultat |
|---|---|---|
| Fee-skema + maker-rebate (5m/15m crypto) | ✅ VERIFICERET | taker-only `fee = C·0.07·p·(1−p)`; maker 0; maker-rebate-pulje 20% af taker-fees; taker-rebate-tiers 0–50% |
| Tick size, min order, prisgranularitet | ✅ VERIFICERET | tick 0.01 (1¢), minOrder 5 (enhed: se åben detalje A1), negRisk false |
| CLOB rate limits (ordrer/sek, WS) | ✅ VERIFICERET | Cloudflare-IP-limits + NYE per-signer token-buckets (warning-mode fra 24/7-2026) |
| Resolution-regel (close ≥ open → Up) | ✅ VERIFICERET | »greater than or equal to« bekræftet i 6/6 markedsbeskrivelser; kilde: Chainlink data streams |
| Ping mod clob.polymarket.com fra 3 VPS-regioner | ⬜ OPEN (manuel) | container-baseline målt (se §6); 3×VPS er manuel (Rasmus) — skabelon klar |
| Manuelt (Rasmus): konto, API-nøgler, USDC-wallet | ⬜ OPEN (manuel) | tjekliste i §8 |

---

## 1. Markeder og CLOCK-verifikation

Kilde: `GET https://gamma-api.polymarket.com/markets?slug=…` (2026-07-27T22:35:35Z). Slug-formlen fra SPEC §2 CLOCK ramte direkte — ingen markedssøgning nødvendig.

| Asset (5m) | Slug fundet | Tick | MinOrder | negRisk | feesEnabled | feeType | Tie-regel ok |
|---|---|---|---|---|---|---|---|
| btc | ✅ `btc-updown-5m-1785191700` | 0.01 | 5 | False | True | crypto_fees_v2 | ✅ |
| eth | ✅ `eth-updown-5m-1785191700` | 0.01 | 5 | False | True | crypto_fees_v2 | ✅ |
| sol | ✅ `sol-updown-5m-1785191700` | 0.01 | 5 | False | True | crypto_fees_v2 | ✅ |
| xrp | ✅ `xrp-updown-5m-1785191700` | 0.01 | 5 | False | True | crypto_fees_v2 | ✅ |
| doge | ✅ `doge-updown-5m-1785191700` | 0.01 | 5 | False | True | crypto_fees_v2 | ✅ |
| bnb | ✅ `bnb-updown-5m-1785191700` | 0.01 | 5 | False | True | crypto_fees_v2 | ✅ |
| btc (15m) | ✅ `btc-updown-15m-1785191400` | 0.01 | 5 | False | True | crypto_fees_v2 | ✅ |

- Næste vindue (`btc-updown-5m-1785192000`) fandtes allerede ved opslag: ✅ — markeder oprettes i forvejen (btc-vinduet her fik `acceptingOrdersTimestamp` ca. et døgn før åbning: `2026-07-26T22:51:03Z` for vindue `1785191700`).
- `restricted: True` på alle updown-markeder (geo-flag; se §8).
- Observeret spread i btc-vinduet ved opslag: 0.01 (bogens likviditet `liquidityNum` ≈ 27322.3648).

## 2. Resolution-regel (tie-reglen) — VERIFICERET

Verbatim fra btc-5m-markedsbeskrivelsen (identisk mønster i alle 6 assets):

> This market will resolve to "Up" if the Bitcoin price at the end of the time range specified in the title is greater than or equal to the price at the beginning of that range. Otherwise, it will resolve to "Down".
> The resolution source for this market is information from Chainlink, specifically the BTC/USD data stream available at https://data.chain.link/streams/btc-usd.
> Please note that this market is about the price according to Chainlink data stream BTC/USD, not according to other sources or spot markets.

**Konklusion:** close **≥** open → **Up** (»greater than or equal to«). Tie går til Up, præcis som SPEC §4/M0 antog. Settlement-kilden er Chainlink data streams per asset (fx `https://data.chain.link/streams/btc-usd`) — bekræfter F1 (SPEC §2) som settlement-sandheden. Ved delta ≈ 0 er Up dermed strukturelt favoriseret; det skal BRAIN's empiriske P(Up) fange naturligt.

## 3. Fees og rebates — DE tal Gate B skal bruge

Kilder: markeds-objekterne (Gamma/CLOB, 2026-07-27T22:35:35Z) + `docs.polymarket.com/trading/fees`, `/programs/maker-rebates`, `/programs/taker-rebates` (llms-full.txt, sha256 `1a5816c0d496d739…`, 2026-07-27T22:35:37Z).

### 3.1 Markedets fee-konfiguration (live-felter, alle 6 assets + 15m identiske)

```json
{
  "feesEnabled": true,
  "feeType": "crypto_fees_v2",
  "feeSchedule": {
    "exponent": 1,
    "rate": 0.07,
    "takerOnly": true,
    "rebateRate": 0.2
  }
}
```

### 3.2 Fee-formlen (docs, verbatim)

```text
fee = C × feeRate × p × (1 - p)
```

> **Makers are never charged fees.** Only takers pay fees.

Kategori-tabel (docs, verbatim — crypto-rækken er vores):

| Category        | Taker Fee Rate | Maker Fee Rate | Maker Rebate |
| --------------- | -------------- | -------------- | ------------ |
| Crypto          | 0.07           | 0              | 20%          |
| Sports          | 0.05           | 0              | 15%          |
| Finance         | 0.04           | 0              | 25%          |
| Politics        | 0.04           | 0              | 25%          |
| Economics       | 0.05           | 0              | 25%          |
| Culture         | 0.05           | 0              | 25%          |
| Weather         | 0.05           | 0              | 25%          |
| Other / General | 0.05           | 0              | 25%          |
| Mentions        | 0.04           | 0              | 25%          |
| Tech            | 0.04           | 0              | 25%          |
| Geopolitics     | 0              | 0              | —            |

Præcision: Fees are rounded to 5 decimal places. The smallest fee charged is 0.00001 pUSD. Anything smaller rounds to zero, so very small trades near the extremes may incur no fee at all.

### 3.3 Afledte Gate B-tal (beregnet af scriptet fra rate = 0.07)

Taker-fee per share ved fill-pris p — dette er `fees`-leddet i `edge_net = p_hat − ask − fees` (SPEC §2 HANDS), når entry er taker:

| p | 0.30 | 0.35 | 0.40 | 0.45 | 0.50 | 0.55 | 0.60 | 0.65 | 0.70 |
|---|---|---|---|---|---|---|---|---|---|
| fee/share (USDC) | 0.01470 | 0.01593 | 0.01680 | 0.01733 | 0.01750 | 0.01733 | 0.01680 | 0.01593 | 0.01470 |

Both-sides-par hvor BEGGE legs fyldes som taker (leg-priser ~komplementære):

| Leg-priser | Fee per par (USDC) | Breakeven paired cost (payout 1.00) |
|---|---|---|
| 0.50/0.50 | 0.03500 | < 0.96500 |
| 0.60/0.40 | 0.03360 | < 0.96640 |
| 0.70/0.30 | 0.02940 | < 0.97060 |
| 0.80/0.20 | 0.02240 | < 0.97760 |
| 0.90/0.10 | 0.01260 | < 0.98740 |

**Konsekvens (aritmetik, ikke politik):** modstander-baselinens paired cost ~$1.006 (SPEC §0) er UNDER vand med taker/taker-fees (breakeven ~0.965 ved 50/50) men fee-fri hvis begge legs er maker-fills (`takerOnly: true`). `paired_cost_max: 1.03` i config giver derfor kun mening som ex-fee-mål med maker-legging; M4-shadow skal modellere fee per leg efter faktisk maker/taker-status. Bemærk: SPEC §0 nævner ~70% komprimering af hans dagsrate siden april — fees på crypto-kategorien er en oplagt delforklaring.

### 3.4 Maker-rebates (docs, verbatim uddrag)

| Category        | Maker Rebate | Distribution Method |
| --------------- | ------------ | ------------------- |
| Crypto          | 20%          | Fee-curve weighted  |
| Sports          | 15%          | Fee-curve weighted  |
| Finance         | 25%          | Fee-curve weighted  |
| Politics        | 25%          | Fee-curve weighted  |
| Economics       | 25%          | Fee-curve weighted  |
| Culture         | 25%          | Fee-curve weighted  |
| Weather         | 25%          | Fee-curve weighted  |
| Other / General | 25%          | Fee-curve weighted  |
| Mentions        | 25%          | Fee-curve weighted  |
| Tech            | 25%          | Fee-curve weighted  |
| Geopolitics     | —            | Fee-free            |

> Rebates are paid daily in pUSD, directly to your wallet. A minimum accrued rebate of **$1 pUSD** is required for a payout.

Fordeling per marked efter fee-ækvivalent formel (`fee_equivalent = C × feeRate × p × (1 − p)`), dagligt i pUSD, minimum $1 før udbetaling. Markeds-objektet bekræfter `makerRebatesFeeShareBps: 10000` og `feeSchedule.rebateRate: 0.2` (20% af taker-fees går til maker-puljen i crypto).

### 3.5 Taker-rebates (SPEC §8-spørgsmålet: »kvalificerer taker-flow?«)

**Svar: JA, delvist** — siden 28. maj 2026 (The Taker Rebate Program goes live on **Thursday, May 28, 2026**.). Taker-flow optjener Weighted Volume og får 0–50% af taker-fees tilbage efter tier. Maker-REWARDS (liquidity rewards) kræver derimod hvilende ordrer (min størrelse 50 shares, maks spread 4.5¢ på disse markeder) — rent taker-flow kvalificerer ikke dér.

```text
wV = Trade Size × (1 − Entry Price) × Category Weight × Bonuses
```

| Category                           | Weight                         |
| ---------------------------------- | ------------------------------ |
| Sports                             | 1.0                            |
| Politics, Finance, Mentions, Tech  | 1.3                            |
| Economics, Culture, Weather, Other | 1.7                            |
| Crypto                             | 2.3                            |
| Geopolitics                        | 0 (free to trade, earns no wV) |

Crypto-vægten er 2,3 — højeste af alle kategorier. Tier-tabel (verbatim):

| Tier | Name     | 30-day wV Needed    | Rebate | Level-Up Bonus |
| :--: | -------- | ------------------- | :----: | :------------: |
|   0  | None     | Under $2,000       |   0%   |      None      |
|   1  | Bronze   | $2,000             |   3%   |      $10      |
|   2  | Silver   | $20,000            |   8%   |      $50      |
|   3  | Gold     | $200,000           |   18%  |      $250     |
|   4  | Platinum | $1,000,000         |   32%  |     $1,500    |
|   5  | Diamond  | $4,000,000         |   44%  |     $7,500    |
|   6  | Obsidian | $10,000,000 and up |   50%  |    $25,000    |

### 3.6 Legacy-felter (dokumenteret uoverensstemmelse)

Både Gamma og CLOB viser `makerBaseFee/maker_base_fee = 1000` og `takerBaseFee/taker_base_fee = 1000` (bps). Disse matcher IKKE den aktive `crypto_fees_v2`-formel (0.07·p·(1−p), taker-only) og behandles som legacy-metadata. Docs' fee-tabeller matcher formlen (100 shares @ $0.50 → $1.75). Empirisk bekræftelse af faktisk trukket fee sker ved første fills i M4/M5 (åben detalje A2).

## 4. Rate limits

Kilde: `docs.polymarket.com/api-reference/rate-limits` + `/api-reference/trading-rate-limits` (2026-07-27T22:35:37Z, verbatim).

### 4.1 Cloudflare-IP-limits (throttling, glidende vinduer)

## General

| Endpoint              | Limit            |
| --------------------- | ---------------- |
| General rate limiting | 15,000 req / 10s |
| Health check (`/ok`)  | 100 req / 10s    |

***

## Gamma API

Base URL: `https://gamma-api.polymarket.com`

| Endpoint                       | Limit           |
| ------------------------------ | --------------- |
| General                        | 4,000 req / 10s |
| `/events`                      | 500 req / 10s   |
| `/markets`                     | 300 req / 10s   |
| `/markets` + `/events` listing | 900 req / 10s   |
| `/comments`                    | 200 req / 10s   |
| `/tags`                        | 200 req / 10s   |
| `/public-search`               | 350 req / 10s   |

***

## Data API

Base URL: `https://data-api.polymarket.com`

| Endpoint             | Limit           |
| -------------------- | --------------- |
| General              | 1,000 req / 10s |
| `/trades`            | 200 req / 10s   |
| `/positions`         | 150 req / 10s   |
| `/closed-positions`  | 150 req / 10s   |
| Health check (`/ok`) | 100 req / 10s   |

***

## CLOB API

Base URL: `https://clob.polymarket.com`

### General

| Endpoint                   | Limit           |
| -------------------------- | --------------- |
| General                    | 9,000 req / 10s |
| `GET` balance allowance    | 200 req / 10s   |
| `UPDATE` balance allowance | 50 req / 10s    |

### Market Data

| Endpoint          | Limit           |
| ----------------- | --------------- |
| `/book`           | 1,500 req / 10s |
| `/books`          | 500 req / 10s   |
| `/price`          | 1,500 req / 10s |
| `/prices`         | 500 req / 10s   |
| `/midpoint`       | 1,500 req / 10s |
| `/midpoints`      | 500 req / 10s   |
| `/prices-history` | 1,000 req / 10s |
| Market tick size  | 200 req / 10s   |

### Ledger

| Endpoint                                         | Limit         |
| ------------------------------------------------ | ------------- |
| `/trades`, `/orders`, `/notifications`, `/order` | 900 req / 10s |
| `/data/orders`                                   | 500 req / 10s |
| `/data/trades`                                   | 500 req / 10s |
| `/notifications`                                 | 125 req / 10s |

### Authentication

| Endpoint          | Limit         |
| ----------------- | ------------- |
| API key endpoints | 100 req / 10s |

### Trading

Cloudflare applies both **burst** limits (short spikes allowed) and **sustained** limits (longer-term average) to trading endpoints.

| Endpoint                       | Burst Limit     | Sustained Limit      |
| ------------------------------ | --------------- | -------------------- |
| `POST /order`                  | 5,000 req / 10s | 120,000 req / 10 min |
| `DELETE /order`                | 5,000 req / 10s | 120,000 req / 10 min |
| `POST /orders`                 | 2,000 req / 10s | 21,000 req / 10 min  |
| `DELETE /orders`               | 2,000 req / 10s | 15,000 req / 10 min  |
| `DELETE /cancel-all`           | 250 req / 10s   | 6,000 req / 10 min   |
| `DELETE /cancel-market-orders` | 1,500 req / 10s | 21,000 req / 10 min  |

***

### 4.2 Per-signer token-buckets (NYT — warning-mode netop nu)

> ⚠️ Beginning July 24, 2026, the limiter will run in warning mode for two weeks.
  Requests will continue to be processed during this period. A request that
  would be rejected after live enforcement begins will include
  `Poly-RateLimit-Warning: true`. Monitor this header and adjust your request
  patterns before live enforcement begins. Polymarket will announce when live
  enforcement begins.

Token-kost per request:

| Bucket | Request                        | Token Cost                                    |
| ------ | ------------------------------ | --------------------------------------------- |
| Order  | `POST /order`                  | 1                                             |
| Order  | `POST /orders`                 | Number of orders in a non-empty batch         |
| Cancel | `DELETE /order`                | 1                                             |
| Cancel | `DELETE /orders`               | Number of submitted order IDs                 |
| Cancel | `DELETE /cancel-all`           | 1 plus the number of orders canceled          |
| Cancel | `DELETE /cancel-market-orders` | 1 plus the number of matching orders canceled |

Volume-tiers (refill-rate/burst; tier følger 30-dages volumen på maker-wallet, opdateres hver 3. time):

| Tier     | 30-Day Volume | Order Rate (tokens/s) | Order Burst (tokens) | Cancel Rate (tokens/s) | Cancel Burst (tokens) | Negative Cancel Balance |
| -------- | ------------: | --------------------: | -------------------: | ---------------------: | --------------------: | :---------------------: |
| Standard |             — |                    40 |                   60 |                     80 |                   120 |           Yes           |
| Copper   |     $30,000+ |                    60 |                   90 |                    120 |                   180 |           Yes           |
| Bronze   |     $50,000+ |                    80 |                  120 |                    160 |                   240 |           Yes           |
| Silver   |    $100,000+ |                   200 |                  300 |                    400 |                   600 |           Yes           |
| Gold     |    $500,000+ |                   400 |                  600 |                    800 |                 1,200 |           Yes           |
| Platinum |       $2.5M+ |                   450 |                  675 |                    900 |                 1,350 |            No           |
| Diamond  |         $5M+ |                   525 |                  787 |                  1,050 |                 1,575 |            No           |
| Elite    |        $10M+ |                   600 |                  900 |                  1,200 |                 1,800 |            No           |

**Operativ konsekvens:** Standard-tier = 40 ordrer/s (burst 60) og 80 cancels/s (burst 120) per signer. Rigeligt til HANDS' probe-tempo i M5 ($500 bankroll), men live-enforcement starter ~7/8-2026 — RECORDER/HANDS skal logge `Poly-RateLimit-*`-headers fra dag ét.

### 4.3 WebSocket-limits

- CLOB market/user-WS (`wss://ws-subscriptions-clob.polymarket.com/ws/…`): **ingen dokumenterede connection-caps** fundet i docs — kun de generelle Cloudflare-limits. (Perps-produktet har egne WS-caps — 50 conns/IP, 100 subs/conn — de gælder IKKE CLOB, men er noteret som reference i `research/raw/docs_excerpts.md`.)
- RTDS (`wss://ws-live-data.polymarket.com`): applikations-heartbeat — send tekstframen `PING` hvert 5. sekund (bekræftet i docs).

## 5. F1-feed verificeret live (RTDS / Chainlink)

SPEC §2 F1 angiver kanalen `crypto_prices_chainlink` — **bekræftet som det rå topic-navn** på RTDS. Subscribe-frame (docs, verbatim):

```json
{
      "action": "subscribe",
      "subscriptions": [
        {
          "topic": "crypto_prices",
          "type": "update",
          "filters": "btcusdt,ethusdt"
        },
        {
          "topic": "crypto_prices_chainlink",
          "type": "*",
          "filters": "{\"symbol\":\"eth/usd\"}"
        }
      ]
    }
```

Docs' symboltabel lister kun btc/eth/sol/xrp:

| Source    | Supported symbols                          |
| --------- | ------------------------------------------ |
| Binance   | `btcusdt`, `ethusdt`, `solusdt`, `xrpusdt` |
| Chainlink | `btc/usd`, `eth/usd`, `sol/usd`, `xrp/usd` |

**Live-probe** (2026-07-27T22:35:38Z, 30s subscription uden filter): ticks per symbol: `{"bnb/usd": 28, "btc/usd": 28, "doge/usd": 28, "eth/usd": 28, "hype/usd": 28, "sol/usd": 29, "xrp/usd": 27}`. btc/usd inter-tick median 1000 ms, maks 2000 ms.

✅ **Alle seks spec-assets streamer live** — docs-symboltabellen er blot forældet (den mangler doge/bnb; streamen bærer desuden flere symboler end spec'en bruger). Flanke-optionen (SPEC §0) er dermed feed-teknisk åben for alle seks. Kadencen ~1 Hz betyder samtidig, at S_open-gap-reglen (tick i `[boundary, boundary+2s]`, SPEC §2 F1) er realistisk men stram: observeret maks-gap i proben lå ved selve 2s-grænsen. M1-soaken skal måle gap-fordelingen per symbol over 48h, før grænsen evt. justeres i config.

## 6. Ping-baseline og VPS-skabelon

Målt 2026-07-27T22:36:08Z fra **dev-container via agent-proxy (IKKE repræsentativ for VPS)** mod `https://clob.polymarket.com/` (n=15; curl gennem HTTPS-CONNECT-tunnel):

| Metrik (median) | ms |
|---|---|
| DNS | 0.0 |
| TCP connect (til proxy) | 0.3 |
| TLS appconnect (håndtryk m. origin gennem tunnel) | 120.6 |
| TTFB | 270.6 |
| Total | 270.7 |

TLS−TCP ≈ 120.3 ms (groft mål for origin-håndtrykket herfra). Cloudflare-colo der svarede: **IAD**. Tallene er en metode-baseline — IKKE et beslutningsgrundlag for VPS-valg.

**Manuel VPS-måling (Rasmus, blokerer M5 jf. SPEC §8).** Kør på hver kandidat-VPS:

```bash
for i in $(seq 20); do curl -so /dev/null -w '%{time_connect} %{time_appconnect} %{time_starttransfer}\n' https://clob.polymarket.com/; sleep 0.5; done
# eller: python3 research/verify_rules.py --ping --region <label>
```

| Region | Dato | n | TCP med. (ms) | TLS med. (ms) | TTFB med. (ms) | CF-colo |
|---|---|---|---|---|---|---|
| us-east (fx Ashburn) | _udfyldes_ | | | | | |
| us-east-2 (fx Ohio) | _udfyldes_ | | | | | |
| eu-vest (fx Frankfurt/Amsterdam) | _udfyldes_ | | | | | |

Bemærk: `clob.polymarket.com` er bag Cloudflare — TCP/TLS-tid måler nærmeste CF-edge, ikke origin. **TTFB er det operative tal** (edge→origin inkluderet). Polymarkets S3-buckets ligger i `us-east-2`; container-proben svarede fra IAD (us-east).

## 7. CLOB-krydstjek (uafhængig kilde for §1-tallene)

Kilde: `GET https://clob.polymarket.com/markets/{conditionId}` (2026-07-27T22:35:37Z):

```json
{
 "btc_5m": {
  "condition_id": "0x6b94b859d87563a957ef6520e9f39350025e33bdb2c9eac0699c577b78e48b91",
  "minimum_order_size": 5,
  "minimum_tick_size": 0.01,
  "maker_base_fee": 1000,
  "taker_base_fee": 1000,
  "neg_risk": false,
  "accepting_orders": true,
  "rewards": {
   "rates": null,
   "min_size": 50,
   "max_spread": 4.5
  }
 },
 "btc_15m": {
  "condition_id": "0xf7b51c6093759314b93fa3b3622ae37358da60de5a34c2fe5bae518b38614aab",
  "minimum_order_size": 5,
  "minimum_tick_size": 0.01,
  "maker_base_fee": 1000,
  "taker_base_fee": 1000,
  "neg_risk": false,
  "accepting_orders": true,
  "rewards": {
   "rates": null,
   "min_size": 50,
   "max_spread": 4.5
  }
 }
}
```

## 8. Manuel tjekliste (Rasmus) — OPEN

1. **Polymarket-konto + API-nøgler** (L2-credentials via py-clob-client; nøgler i `.env`, aldrig i kode/logs — SPEC §6).
2. **USDC-wallet (Polygon) med KUN arbejdskapital** ($500-rammen fra M5).
3. **3×VPS-ping** — udfyld tabellen i §6; regionvalg blokerer M5 (SPEC §8).
4. **`calendar.yaml`** — FOMC-datoerne er seedet fra Feds offentliggjorte kalender; verificér + tilføj CPI/PPI/NFP fra BLS-skemaet (ugentlig vedligeholdelse, F-4).
5. **Geo/jura:** markederne bærer `restricted: true`, og docs' geo-side (Perps) lister bl.a. USA/Canada som ordre-forbudte. CLOB-markedernes præcise geo-vilkår følger Polymarkets ToS — afklar selv jurisdiktion/VPS-placering ift. konto. Read-only markedsdata er eksplicit undtaget restriktionerne.

## 9. Åbne detaljer fundet under M0 (ikke-blokerende for M1)

| # | Detalje | Afklares |
|---|---|---|
| A1 | `orderMinSize: 5` — docs' Market Details-tabel siger »Minimum order size **in USDC**«, men feltet hed historisk »shares« og SPEC forventer 5 *shares*. Værdien er 5 uanset; enheden afgør mindste probe ved p<1.00. | Empirisk ved første ordre (M4-stub/M5) |
| A2 | Legacy `maker/taker_base_fee = 1000` bps vs. aktiv `feeSchedule` 0.07 — faktisk trukket fee bekræftes mod fills. | Første fills (M4/M5) |
| A3 | RTDS-Chainlink dækker ALLE seks assets (bekræftet i live-probe trods forældet docs-tabel). Rest: kadence-/gap-fordeling per symbol over døgn, ift. S_open-gap-reglens 2s-grænse. | M1 48h-soak |
| A4 | CLOB-WS connection-caps udokumenterede — antag konservativt få forbindelser (recorderen behøver ≤3). | M1-soak observerer evt. disconnects |

---
*Reproducérbarhed: `python3 research/verify_rules.py` genkører alt; rå API-svar i `research/raw/`; docs-dump sha256 `1a5816c0d496d739b02435e9ba1c34198575c9bea7ac030f89b0bd15b66601bd` (1233772 bytes).*
