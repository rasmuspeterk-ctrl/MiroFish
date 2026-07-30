Kilde: https://docs.polymarket.com/llms-full.txt
Hentet: 2026-07-27T22:35:37Z
sha256: 1a5816c0d496d739b02435e9ba1c34198575c9bea7ac030f89b0bd15b66601bd

### fee_formula
fee = C × feeRate × p × (1 - p)

### fee_category_table
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

### maker_rebate_table
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

### taker_wv_formula
wV = Trade Size × (1 − Entry Price) × Category Weight × Bonuses

### taker_weights_table
| Category                           | Weight                         |
| ---------------------------------- | ------------------------------ |
| Sports                             | 1.0                            |
| Politics, Finance, Mentions, Tech  | 1.3                            |
| Economics, Culture, Weather, Other | 1.7                            |
| Crypto                             | 2.3                            |
| Geopolitics                        | 0 (free to trade, earns no wV) |

### taker_tier_table
| Tier | Name     | 30-day wV Needed    | Rebate | Level-Up Bonus |
| :--: | -------- | ------------------- | :----: | :------------: |
|   0  | None     | Under $2,000       |   0%   |      None      |
|   1  | Bronze   | $2,000             |   3%   |      $10      |
|   2  | Silver   | $20,000            |   8%   |      $50      |
|   3  | Gold     | $200,000           |   18%  |      $250     |
|   4  | Platinum | $1,000,000         |   32%  |     $1,500    |
|   5  | Diamond  | $4,000,000         |   44%  |     $7,500    |
|   6  | Obsidian | $10,000,000 and up |   50%  |    $25,000    |

### rate_limits_block
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

### trl_warning
Beginning July 24, 2026, the limiter will run in warning mode for two weeks.
  Requests will continue to be processed during this period. A request that
  would be rejected after live enforcement begins will include
  `Poly-RateLimit-Warning: true`. Monitor this header and adjust your request
  patterns before live enforcement begins. Polymarket will announce when live
  enforcement begins.

### trl_bucket_table
| Bucket | Request                        | Token Cost                                    |
| ------ | ------------------------------ | --------------------------------------------- |
| Order  | `POST /order`                  | 1                                             |
| Order  | `POST /orders`                 | Number of orders in a non-empty batch         |
| Cancel | `DELETE /order`                | 1                                             |
| Cancel | `DELETE /orders`               | Number of submitted order IDs                 |
| Cancel | `DELETE /cancel-all`           | 1 plus the number of orders canceled          |
| Cancel | `DELETE /cancel-market-orders` | 1 plus the number of matching orders canceled |

### trl_tier_table
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

### rtds_symbols_table
| Source    | Supported symbols                          |
| --------- | ------------------------------------------ |
| Binance   | `btcusdt`, `ethusdt`, `solusdt`, `xrpusdt` |
| Chainlink | `btc/usd`, `eth/usd`, `sol/usd`, `xrp/usd` |

### rtds_subscribe_example
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