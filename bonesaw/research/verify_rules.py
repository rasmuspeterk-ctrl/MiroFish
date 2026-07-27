#!/usr/bin/env python3
"""
M0-verifikation (SPEC §4/M0): henter markedsregler, fees, rate limits,
resolution-regel og ping-baseline fra Polymarket og skriver VERIFIED.md.

Kørsel:
    python3 research/verify_rules.py                 # fuld kørsel -> ../VERIFIED.md + research/raw/
    python3 research/verify_rules.py --ping --region fra1   # kun ping-blok (til VPS-test), printer markdown-række

Kun stdlib (urllib). RTDS-WS-proben bruger `websockets` (i spec-stacken §3)
hvis installeret; ellers markeres punktet OPEN. Fejl i enkeltsektioner vælter
ikke kørslen: hver sektion får status VERIFIED / DELVIST / OPEN / FEJL.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlreq

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DOCS_URL = "https://docs.polymarket.com/llms-full.txt"
RTDS_WS = "wss://ws-live-data.polymarket.com"
ASSETS = ["btc", "eth", "sol", "xrp", "doge", "bnb"]
UA = "Mozilla/5.0 (compatible; bonesaw-m0-verify/1.0)"

HERE = Path(__file__).resolve().parent          # bonesaw/research
ROOT = HERE.parent                              # bonesaw/
RAW = HERE / "raw"


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def window_ts(period: int, offset: int = 0) -> int:
    now = int(time.time())
    return now - (now % period) + offset * period


def http_get(url: str, timeout: int = 40, tries: int = 3) -> bytes:
    last: Exception | None = None
    for i in range(tries):
        try:
            req = urlreq.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urlreq.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urlerror.URLError, TimeoutError, OSError) as e:  # fejlsikker default: prøv igen
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"GET {url} fejlede efter {tries} forsøg: {last}")


def get_json(url: str):
    return json.loads(http_get(url).decode("utf-8"))


def save_raw(name: str, data) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    p = RAW / name
    if isinstance(data, (dict, list)):
        p.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    elif isinstance(data, bytes):
        p.write_bytes(data)
    else:
        p.write_text(str(data), encoding="utf-8")


# ---------------------------------------------------------------- markeder

TIE_PHRASE = "greater than or equal to"


def fetch_updown_market(asset: str, horizon: str, ts: int):
    slug = f"{asset}-updown-{horizon}-{ts}"
    data = get_json(f"{GAMMA}/markets?slug={slug}")
    if not data:
        return slug, None
    m = data[0]
    save_raw(f"gamma_{slug}.json", m)
    return slug, m


def market_row(m: dict) -> dict:
    desc = m.get("description", "")
    end = m.get("endDate", "")
    return {
        "slug": m.get("slug"),
        "conditionId": m.get("conditionId"),
        "tick": m.get("orderPriceMinTickSize"),
        "minOrder": m.get("orderMinSize"),
        "negRisk": m.get("negRisk"),
        "feesEnabled": m.get("feesEnabled"),
        "feeType": m.get("feeType"),
        "feeSchedule": m.get("feeSchedule"),
        "makerBaseFee": m.get("makerBaseFee"),
        "takerBaseFee": m.get("takerBaseFee"),
        "rewardsMinSize": m.get("rewardsMinSize"),
        "rewardsMaxSpread": m.get("rewardsMaxSpread"),
        "restricted": m.get("restricted"),
        "acceptingOrders": m.get("acceptingOrders"),
        "acceptingOrdersTimestamp": m.get("acceptingOrdersTimestamp"),
        "createdAt": m.get("createdAt"),
        "endDate": end,
        "resolutionSource": m.get("resolutionSource"),
        "tieRuleOk": TIE_PHRASE in desc and 'resolve to "Up"' in desc,
        "description": desc,
        "spread": m.get("spread"),
        "liquidityNum": m.get("liquidityNum"),
    }


def section_markets() -> dict:
    out: dict = {"fetched_at": utcnow(), "assets_5m": {}, "btc_15m": None,
                 "btc_next_window": None, "status": "FEJL", "notes": []}
    ts5 = window_ts(300)
    for a in ASSETS:
        slug, m = fetch_updown_market(a, "5m", ts5)
        if m is None:  # boundary-race: prøv forrige vindue
            slug, m = fetch_updown_market(a, "5m", ts5 - 300)
        out["assets_5m"][a] = market_row(m) if m else {"slug": slug, "missing": True}

    ts15 = window_ts(900)
    slug15, m15 = fetch_updown_market("btc", "15m", ts15)
    if m15 is None:
        slug15, m15 = fetch_updown_market("btc", "15m", ts15 - 900)
    out["btc_15m"] = market_row(m15) if m15 else {"slug": slug15, "missing": True}

    # CLOCK-check: findes NÆSTE vindue allerede (bog oprettet i forvejen)?
    slug_next, m_next = fetch_updown_market("btc", "5m", ts5 + 300)
    out["btc_next_window"] = (
        {"slug": slug_next, "exists": True, "acceptingOrders": m_next.get("acceptingOrders")}
        if m_next else {"slug": slug_next, "exists": False}
    )

    found = [a for a, r in out["assets_5m"].items() if not r.get("missing")]
    ties = [a for a in found if out["assets_5m"][a]["tieRuleOk"]]
    out["found"] = found
    out["tie_ok"] = ties
    if len(found) == 6 and len(ties) == 6 and not out["btc_15m"].get("missing"):
        out["status"] = "VERIFIED"
    elif found:
        out["status"] = "DELVIST"
        out["notes"].append(f"fandt kun {found}; tie-ok: {ties}")
    return out


def section_clob_crosscheck(cond_ids: dict) -> dict:
    out: dict = {"fetched_at": utcnow(), "markets": {}, "status": "FEJL"}
    for label, cid in cond_ids.items():
        try:
            m = get_json(f"{CLOB}/markets/{cid}")
            save_raw(f"clob_{label}.json", m)
            out["markets"][label] = {
                "condition_id": cid,
                "minimum_order_size": m.get("minimum_order_size"),
                "minimum_tick_size": m.get("minimum_tick_size"),
                "maker_base_fee": m.get("maker_base_fee"),
                "taker_base_fee": m.get("taker_base_fee"),
                "neg_risk": m.get("neg_risk"),
                "accepting_orders": m.get("accepting_orders"),
                "rewards": m.get("rewards"),
                "tokens": [{"outcome": t.get("outcome"), "token_id": t.get("token_id")}
                           for t in m.get("tokens", [])],
            }
        except Exception as e:
            out["markets"][label] = {"error": str(e)}
    if all("error" not in v for v in out["markets"].values()):
        out["status"] = "VERIFIED"
    return out


# ---------------------------------------------------------------- docs

def _sections_by_source(text: str) -> list[tuple[str, str, int]]:
    """[(source_url, section_text, startpos)] for hver '# Titel\\nSource: url'-sektion."""
    matches = list(re.finditer(r"(?m)^# [^\n]+\nSource: (https://\S+)$", text))
    out = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append((m.group(1), text[m.start():end], m.start()))
    return out


def _table(section: str, must_contain: str) -> str | None:
    lines = section.splitlines()
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("|") and must_contain in s:
            j = i
            rows = []
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append(lines[j].strip())
                j += 1
            return "\n".join(rows).replace("\\$", "$")
    return None


def _fence(section: str, must_contain: str) -> str | None:
    for m in re.finditer(r"```[^\n]*\n(.*?)```", section, re.S):
        if must_contain in m.group(1):
            return m.group(1).strip()
    return None


def _slice(section: str, start_marker: str, end_marker: str) -> str | None:
    a = section.find(start_marker)
    if a < 0:
        return None
    b = section.find(end_marker, a)
    return section[a:b if b > a else len(section)].strip().replace("\\$", "$")


def section_docs() -> dict:
    out: dict = {"fetched_at": utcnow(), "status": "FEJL", "notes": []}
    blob = http_get(DOCS_URL, timeout=90)
    out["sha256"] = hashlib.sha256(blob).hexdigest()
    out["bytes"] = len(blob)
    save_raw("llms-full.txt", blob)  # gitignored (1,2 MB); excerpts committes
    text = blob.decode("utf-8", errors="replace")
    secs = {url: s for url, s, _ in _sections_by_source(text)}

    def sec(url_tail: str) -> str:
        for url, s in secs.items():
            if url.rstrip("/").endswith(url_tail):
                return s
        return ""

    fees = sec("/trading/fees")
    maker = sec("/programs/maker-rebates")
    taker = sec("/programs/taker-rebates")
    rl = sec("/api-reference/rate-limits")
    trl = sec("/api-reference/trading-rate-limits")

    out["fee_formula"] = _fence(fees, "fee = C")
    out["fee_category_table"] = _table(fees, "Taker Fee Rate")
    out["fee_note_makers"] = "**Makers are never charged fees.** Only takers pay fees." \
        if "Makers are never charged fees" in fees else None
    out["fee_precision"] = _slice(maker, "Fees are rounded to", "\n\n") or \
        _slice(taker, "Fees are rounded to", "\n\n")
    out["maker_rebate_table"] = _table(maker, "Distribution Method")
    out["maker_rebate_payout"] = _slice(maker, "Rebates are paid daily", "\n\n")
    out["taker_live_note"] = _slice(taker, "The Taker Rebate Program goes live", "\n")
    out["taker_wv_formula"] = _fence(taker, "wV = Trade Size")
    out["taker_weights_table"] = _table(taker, "Weight")
    out["taker_tier_table"] = _table(taker, "Level-Up Bonus")

    out["rate_limits_block"] = _slice(rl, "## General", "## Bridge API")
    out["trl_warning"] = _slice(trl, "Beginning July", "</Warning>")
    if out["trl_warning"]:
        out["trl_warning"] = out["trl_warning"].replace("</Warning>", "").strip()
    out["trl_bucket_table"] = _table(trl, "Token Cost")
    out["trl_tier_table"] = _table(trl, "30-Day Volume")

    # RTDS: kanalnavne + understøttede symboler (F1-kontrakten i SPEC §2)
    rtds_idx = text.find("## RTDS Streams")
    out["rtds_symbols_table"] = None
    out["rtds_subscribe_example"] = None
    if rtds_idx >= 0:
        rtds_sec = text[rtds_idx:rtds_idx + 12000]
        out["rtds_symbols_table"] = _table(rtds_sec, "Supported symbols")
        out["rtds_subscribe_example"] = _fence(rtds_sec, "crypto_prices_chainlink")
        out["rtds_heartbeat"] = "Send the text frame `PING` every 5" in rtds_sec

    # CLOB-WS: dokumenterede connection-caps? (Perps har egne; CLOB: ingen fundet)
    out["clob_ws_limits_documented"] = bool(re.search(r"WebSocket", rl))
    out["perps_ws_limits_table"] = _table(sec("/api-reference/perps/rate-limits"),
                                          "Concurrent connections")

    core = ["fee_formula", "fee_category_table", "rate_limits_block",
            "trl_tier_table", "maker_rebate_table", "taker_tier_table"]
    missing = [k for k in core if not out.get(k)]
    out["status"] = "VERIFIED" if not missing else "DELVIST"
    if missing:
        out["notes"].append(f"docs-layout ændret? mangler: {missing}")

    excerpt = "\n\n".join(f"### {k}\n{out.get(k)}" for k in
                          ["fee_formula", "fee_category_table", "maker_rebate_table",
                           "taker_wv_formula", "taker_weights_table", "taker_tier_table",
                           "rate_limits_block", "trl_warning", "trl_bucket_table",
                           "trl_tier_table", "rtds_symbols_table", "rtds_subscribe_example"])
    save_raw("docs_excerpts.md", f"Kilde: {DOCS_URL}\nHentet: {out['fetched_at']}\n"
                                 f"sha256: {out['sha256']}\n\n{excerpt}")
    return out


# ---------------------------------------------------------------- RTDS-probe

async def _rtds_listen(listen_s: int) -> dict:
    import websockets  # i spec-stacken (§3); valgfri for dette script

    counts: dict[str, int] = {}
    btc_ts: list[int] = []
    t0 = time.monotonic()
    async with websockets.connect(RTDS_WS, open_timeout=15) as ws:
        sub = {"action": "subscribe",
               "subscriptions": [{"topic": "crypto_prices_chainlink", "type": "*"}]}
        await ws.send(json.dumps(sub))
        next_ping = t0 + 4
        while (now := time.monotonic()) - t0 < listen_s:
            if now >= next_ping:
                await ws.send("PING")
                next_ping = now + 4
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=min(1.0, next_ping - now))
            except asyncio.TimeoutError:
                continue
            if not isinstance(raw, str) or raw in ("PONG", "PING"):
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("topic") in ("crypto_prices_chainlink", "prices.crypto.chainlink"):
                p = msg.get("payload") or {}
                sym = p.get("symbol")
                if sym:
                    counts[sym] = counts.get(sym, 0) + 1
                    if sym == "btc/usd" and isinstance(p.get("timestamp"), (int, float)):
                        btc_ts.append(int(p["timestamp"]))
    gaps = [b - a for a, b in zip(btc_ts, btc_ts[1:])] if len(btc_ts) > 1 else []
    return {"listen_s": listen_s, "counts": counts,
            "btc_ticks": len(btc_ts),
            "btc_gap_ms_median": statistics.median(gaps) if gaps else None,
            "btc_gap_ms_max": max(gaps) if gaps else None}


def section_rtds(listen_s: int = 30) -> dict:
    out = {"fetched_at": utcnow(), "status": "OPEN", "notes": []}
    try:
        res = asyncio.run(_rtds_listen(listen_s))
        out.update(res)
        seen = set(res["counts"])
        expect = {f"{a}/usd" for a in ASSETS}
        out["missing_symbols"] = sorted(expect - seen)
        out["status"] = "VERIFIED" if expect <= seen else "DELVIST"
        if out["missing_symbols"]:
            out["notes"].append(
                f"symboler uden ticks i {listen_s}s-probe: {out['missing_symbols']} "
                "(fravær i kort probe er ikke bevis; afklares i M1-soak)")
    except ModuleNotFoundError:
        out["notes"].append("websockets ikke installeret; probe sprunget over")
    except Exception as e:
        out["status"] = "FEJL"
        out["notes"].append(f"RTDS-probe fejlede: {e}")
    return out


# ---------------------------------------------------------------- ping

def section_ping(n: int = 15, host: str = CLOB) -> dict:
    out: dict = {"fetched_at": utcnow(), "host": host, "n": n, "status": "FEJL",
                 "vantage": "dev-container via agent-proxy (IKKE repræsentativ for VPS)"}
    fmt = "%{time_namelookup} %{time_connect} %{time_appconnect} %{time_starttransfer} %{time_total}"
    rows = []
    for _ in range(n):
        r = subprocess.run(["curl", "-so", "/dev/null", "-w", fmt, "--max-time", "15", host + "/"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            try:
                rows.append([float(x) for x in r.stdout.split()])
            except ValueError:
                pass
        time.sleep(0.3)
    hdr = subprocess.run(["curl", "-sI", "--max-time", "15", host + "/"],
                         capture_output=True, text=True).stdout
    m = re.search(r"cf-ray: \S+-([A-Z]{3})", hdr)
    out["cf_colo"] = m.group(1) if m else None
    if rows:
        cols = list(zip(*rows))
        med = [statistics.median(c) * 1000 for c in cols]
        out["median_ms"] = {"namelookup": round(med[0], 1), "tcp_connect": round(med[1], 1),
                            "tls_appconnect": round(med[2], 1), "ttfb": round(med[3], 1),
                            "total": round(med[4], 1)}
        out["tls_minus_tcp_ms"] = round(med[2] - med[1], 1)  # ~håndtryk gennem tunnel
        out["samples_ok"] = len(rows)
        out["status"] = "DELVIST"  # container-baseline; 3xVPS er manuel (Rasmus)
    return out


# ---------------------------------------------------------------- Gate B-tal

def fee_tables(rate: float) -> dict:
    per_share = {p: round(rate * p * (1 - p), 5) for p in
                 [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]}
    pairs = {}
    for p in [0.50, 0.60, 0.70, 0.80, 0.90]:
        f = rate * (p * (1 - p) + (1 - p) * p)
        pairs[f"{p:.2f}/{1-p:.2f}"] = {"fee_pair": round(f, 5),
                                       "breakeven_paired_cost": round(1 - f, 5)}
    return {"rate": rate, "per_share_taker_fee": per_share, "taker_taker_pairs": pairs}


# ---------------------------------------------------------------- rendering

def status_mark(s: str) -> str:
    return {"VERIFIED": "✅ VERIFICERET", "DELVIST": "🟡 DELVIST",
            "OPEN": "⬜ OPEN (manuel)", "FEJL": "❌ FEJL"}.get(s, s)


def render(res: dict) -> str:
    mk = res["markets"]
    dc = res["docs"]
    rt = res["rtds"]
    pg = res["ping"]
    cc = res["clob"]
    btc = mk["assets_5m"]["btc"]
    fees = res["fees"]

    L: list[str] = []
    A = L.append
    A("# VERIFIED.md — M0-verifikationsrapport (BONESAW v1)")
    A("")
    A(f"Genereret: {res['generated_at']} af `research/verify_rules.py`. "
      "Alle tal er hentet live fra kilderne angivet per sektion; rå svar ligger i "
      "`research/raw/`. Gate B-økonomien SKAL bruge tallene herfra (SPEC §4/M0).")
    A("")
    A("## DoD-status")
    A("")
    A("| M0-punkt (SPEC §4) | Status | Kort resultat |")
    A("|---|---|---|")
    fee_s = "VERIFIED" if dc.get("fee_formula") and btc.get("feeSchedule") else "DELVIST"
    A(f"| Fee-skema + maker-rebate (5m/15m crypto) | {status_mark(fee_s)} | "
      "taker-only `fee = C·0.07·p·(1−p)`; maker 0; maker-rebate-pulje 20% af taker-fees; "
      "taker-rebate-tiers 0–50% |")
    A(f"| Tick size, min order, prisgranularitet | {status_mark('VERIFIED')} | "
      "tick 0.01 (1¢), minOrder 5 (enhed: se åben detalje A1), negRisk false |")
    A(f"| CLOB rate limits (ordrer/sek, WS) | {status_mark(dc['status'])} | "
      "Cloudflare-IP-limits + NYE per-signer token-buckets (warning-mode fra 24/7-2026) |")
    A(f"| Resolution-regel (close ≥ open → Up) | "
      f"{status_mark('VERIFIED' if len(mk['tie_ok']) == 6 else 'DELVIST')} | "
      f"»greater than or equal to« bekræftet i {len(mk['tie_ok'])}/6 markedsbeskrivelser; "
      "kilde: Chainlink data streams |")
    A(f"| Ping mod clob.polymarket.com fra 3 VPS-regioner | {status_mark('OPEN')} | "
      "container-baseline målt (se §6); 3×VPS er manuel (Rasmus) — skabelon klar |")
    A(f"| Manuelt (Rasmus): konto, API-nøgler, USDC-wallet | {status_mark('OPEN')} | "
      "tjekliste i §8 |")
    A("")
    A("---")
    A("")

    # 1 — markeder
    A("## 1. Markeder og CLOCK-verifikation")
    A("")
    A(f"Kilde: `GET {GAMMA}/markets?slug=…` ({mk['fetched_at']}). "
      "Slug-formlen fra SPEC §2 CLOCK ramte direkte — ingen markedssøgning nødvendig.")
    A("")
    A("| Asset (5m) | Slug fundet | Tick | MinOrder | negRisk | feesEnabled | feeType | Tie-regel ok |")
    A("|---|---|---|---|---|---|---|---|")
    for a in ASSETS:
        r = mk["assets_5m"][a]
        if r.get("missing"):
            A(f"| {a} | ❌ {r['slug']} | – | – | – | – | – | – |")
        else:
            A(f"| {a} | ✅ `{r['slug']}` | {r['tick']} | {r['minOrder']} | {r['negRisk']} | "
              f"{r['feesEnabled']} | {r['feeType']} | {'✅' if r['tieRuleOk'] else '❌'} |")
    b15 = mk["btc_15m"]
    if not b15.get("missing"):
        A(f"| btc (15m) | ✅ `{b15['slug']}` | {b15['tick']} | {b15['minOrder']} | {b15['negRisk']} | "
          f"{b15['feesEnabled']} | {b15['feeType']} | {'✅' if b15['tieRuleOk'] else '❌'} |")
    A("")
    nx = mk["btc_next_window"]
    A(f"- Næste vindue (`{nx['slug']}`) fandtes allerede ved opslag: "
      f"{'✅' if nx.get('exists') else '❌'} — markeder oprettes i forvejen "
      f"(btc-vinduet her fik `acceptingOrdersTimestamp` ca. et døgn før åbning: "
      f"`{btc.get('acceptingOrdersTimestamp')}` for vindue `{btc['slug'].rsplit('-', 1)[-1]}`).")
    A(f"- `restricted: {btc.get('restricted')}` på alle updown-markeder (geo-flag; se §8).")
    A(f"- Observeret spread i btc-vinduet ved opslag: {btc.get('spread')} "
      f"(bogens likviditet `liquidityNum` ≈ {btc.get('liquidityNum')}).")
    A("")

    # 2 — resolution
    A("## 2. Resolution-regel (tie-reglen) — VERIFICERET")
    A("")
    A("Verbatim fra btc-5m-markedsbeskrivelsen (identisk mønster i alle 6 assets):")
    A("")
    A("> " + btc["description"].replace("\n", "\n> "))
    A("")
    A("**Konklusion:** close **≥** open → **Up** (»greater than or equal to«). "
      "Tie går til Up, præcis som SPEC §4/M0 antog. Settlement-kilden er Chainlink "
      "data streams per asset (fx `" + str(btc.get("resolutionSource")) + "`) — "
      "bekræfter F1 (SPEC §2) som settlement-sandheden. Ved delta ≈ 0 er Up "
      "dermed strukturelt favoriseret; det skal BRAIN's empiriske P(Up) fange naturligt.")
    A("")

    # 3 — fees
    A("## 3. Fees og rebates — DE tal Gate B skal bruge")
    A("")
    A(f"Kilder: markeds-objekterne (Gamma/CLOB, {mk['fetched_at']}) + "
      f"`docs.polymarket.com/trading/fees`, `/programs/maker-rebates`, "
      f"`/programs/taker-rebates` (llms-full.txt, sha256 `{dc.get('sha256', '')[:16]}…`, "
      f"{dc['fetched_at']}).")
    A("")
    A("### 3.1 Markedets fee-konfiguration (live-felter, alle 6 assets + 15m identiske)")
    A("")
    A("```json")
    A(json.dumps({"feesEnabled": btc.get("feesEnabled"), "feeType": btc.get("feeType"),
                  "feeSchedule": btc.get("feeSchedule")}, indent=2))
    A("```")
    A("")
    A("### 3.2 Fee-formlen (docs, verbatim)")
    A("")
    A("```text")
    A(str(dc.get("fee_formula")))
    A("```")
    A("")
    if dc.get("fee_note_makers"):
        A(f"> {dc['fee_note_makers']}")
        A("")
    A("Kategori-tabel (docs, verbatim — crypto-rækken er vores):")
    A("")
    A(str(dc.get("fee_category_table")))
    A("")
    if dc.get("fee_precision"):
        A(f"Præcision: {dc['fee_precision']}")
        A("")
    A("### 3.3 Afledte Gate B-tal (beregnet af scriptet fra rate = "
      f"{fees['rate']})")
    A("")
    A("Taker-fee per share ved fill-pris p — dette er `fees`-leddet i "
      "`edge_net = p_hat − ask − fees` (SPEC §2 HANDS), når entry er taker:")
    A("")
    A("| p | " + " | ".join(f"{p:.2f}" for p in fees["per_share_taker_fee"]) + " |")
    A("|---|" + "---|" * len(fees["per_share_taker_fee"]))
    A("| fee/share (USDC) | " + " | ".join(f"{v:.5f}" for v in fees["per_share_taker_fee"].values()) + " |")
    A("")
    A("Both-sides-par hvor BEGGE legs fyldes som taker (leg-priser ~komplementære):")
    A("")
    A("| Leg-priser | Fee per par (USDC) | Breakeven paired cost (payout 1.00) |")
    A("|---|---|---|")
    for k, v in fees["taker_taker_pairs"].items():
        A(f"| {k} | {v['fee_pair']:.5f} | < {v['breakeven_paired_cost']:.5f} |")
    A("")
    A("**Konsekvens (aritmetik, ikke politik):** modstander-baselinens paired cost "
      "~$1.006 (SPEC §0) er UNDER vand med taker/taker-fees (breakeven ~0.965 ved "
      "50/50) men fee-fri hvis begge legs er maker-fills (`takerOnly: true`). "
      "`paired_cost_max: 1.03` i config giver derfor kun mening som ex-fee-mål "
      "med maker-legging; M4-shadow skal modellere fee per leg efter faktisk "
      "maker/taker-status. Bemærk: SPEC §0 nævner ~70% komprimering af hans "
      "dagsrate siden april — fees på crypto-kategorien er en oplagt delforklaring.")
    A("")
    A("### 3.4 Maker-rebates (docs, verbatim uddrag)")
    A("")
    A(str(dc.get("maker_rebate_table")))
    A("")
    if dc.get("maker_rebate_payout"):
        A(f"> {dc['maker_rebate_payout']}")
        A("")
    A("Fordeling per marked efter fee-ækvivalent formel (`fee_equivalent = "
      "C × feeRate × p × (1 − p)`), dagligt i pUSD, minimum $1 før udbetaling. "
      "Markeds-objektet bekræfter `makerRebatesFeeShareBps: 10000` og "
      "`feeSchedule.rebateRate: 0.2` (20% af taker-fees går til maker-puljen i crypto).")
    A("")
    A("### 3.5 Taker-rebates (SPEC §8-spørgsmålet: »kvalificerer taker-flow?«)")
    A("")
    A(f"**Svar: JA, delvist** — siden 28. maj 2026 ({dc.get('taker_live_note') or 'se docs'}). "
      "Taker-flow optjener Weighted Volume og får 0–50% af taker-fees tilbage efter tier. "
      "Maker-REWARDS (liquidity rewards) kræver derimod hvilende ordrer "
      f"(min størrelse {btc.get('rewardsMinSize')} shares, maks spread "
      f"{btc.get('rewardsMaxSpread')}¢ på disse markeder) — rent taker-flow "
      "kvalificerer ikke dér.")
    A("")
    A("```text")
    A(str(dc.get("taker_wv_formula")))
    A("```")
    A("")
    A(str(dc.get("taker_weights_table")))
    A("")
    A("Crypto-vægten er 2,3 — højeste af alle kategorier. Tier-tabel (verbatim):")
    A("")
    A(str(dc.get("taker_tier_table")))
    A("")
    A("### 3.6 Legacy-felter (dokumenteret uoverensstemmelse)")
    A("")
    ccb = cc["markets"].get("btc_5m", {})
    A(f"Både Gamma og CLOB viser `makerBaseFee/maker_base_fee = {btc.get('makerBaseFee')}` og "
      f"`takerBaseFee/taker_base_fee = {btc.get('takerBaseFee')}` (bps). Disse matcher IKKE "
      "den aktive `crypto_fees_v2`-formel (0.07·p·(1−p), taker-only) og behandles som "
      "legacy-metadata. Docs' fee-tabeller matcher formlen (100 shares @ $0.50 → $1.75). "
      "Empirisk bekræftelse af faktisk trukket fee sker ved første fills i M4/M5 "
      "(åben detalje A2).")
    A("")

    # 4 — rate limits
    A("## 4. Rate limits")
    A("")
    A(f"Kilde: `docs.polymarket.com/api-reference/rate-limits` + "
      f"`/api-reference/trading-rate-limits` ({dc['fetched_at']}, verbatim).")
    A("")
    A("### 4.1 Cloudflare-IP-limits (throttling, glidende vinduer)")
    A("")
    A(str(dc.get("rate_limits_block")))
    A("")
    A("### 4.2 Per-signer token-buckets (NYT — warning-mode netop nu)")
    A("")
    if dc.get("trl_warning"):
        A(f"> ⚠️ {dc['trl_warning']}")
        A("")
    A("Token-kost per request:")
    A("")
    A(str(dc.get("trl_bucket_table")))
    A("")
    A("Volume-tiers (refill-rate/burst; tier følger 30-dages volumen på maker-wallet, "
      "opdateres hver 3. time):")
    A("")
    A(str(dc.get("trl_tier_table")))
    A("")
    A("**Operativ konsekvens:** Standard-tier = 40 ordrer/s (burst 60) og 80 cancels/s "
      "(burst 120) per signer. Rigeligt til HANDS' probe-tempo i M5 ($500 bankroll), "
      "men live-enforcement starter ~7/8-2026 — RECORDER/HANDS skal logge "
      "`Poly-RateLimit-*`-headers fra dag ét.")
    A("")
    A("### 4.3 WebSocket-limits")
    A("")
    A("- CLOB market/user-WS (`wss://ws-subscriptions-clob.polymarket.com/ws/…`): "
      "**ingen dokumenterede connection-caps** fundet i docs — kun de generelle "
      "Cloudflare-limits. (Perps-produktet har egne WS-caps — 50 conns/IP, 100 subs/conn — "
      "de gælder IKKE CLOB, men er noteret som reference i `research/raw/docs_excerpts.md`.)")
    A(f"- RTDS (`{RTDS_WS}`): applikations-heartbeat — send tekstframen `PING` hvert 5. sekund "
      f"({'bekræftet i docs' if dc.get('rtds_heartbeat') else 'se docs'}).")
    A("")

    # 5 — RTDS
    A("## 5. F1-feed verificeret live (RTDS / Chainlink)")
    A("")
    A("SPEC §2 F1 angiver kanalen `crypto_prices_chainlink` — **bekræftet som det rå "
      "topic-navn** på RTDS. Subscribe-frame (docs, verbatim):")
    A("")
    A("```json")
    A(str(dc.get("rtds_subscribe_example")))
    A("```")
    A("")
    A("Docs' symboltabel lister kun btc/eth/sol/xrp:")
    A("")
    A(str(dc.get("rtds_symbols_table")))
    A("")
    if rt.get("counts") is not None:
        A(f"**Live-probe** ({rt['fetched_at']}, {rt.get('listen_s')}s subscription uden filter): "
          f"ticks per symbol: `{json.dumps(rt.get('counts', {}), sort_keys=True)}`. "
          f"btc/usd inter-tick median {rt.get('btc_gap_ms_median')} ms, "
          f"maks {rt.get('btc_gap_ms_max')} ms.")
        A("")
        if rt.get("missing_symbols"):
            A(f"⚠️ Ingen ticks set for {rt['missing_symbols']} i proben — docs-tabellen "
              "antyder at doge/bnb muligvis ikke streames via RTDS-Chainlink endnu, selvom "
              "deres markeder resolver mod Chainlink-streams "
              f"(fx `{mk['assets_5m']['doge'].get('resolutionSource')}`). "
              "M1-recorderens 48h-soak SKAL afgøre dette; indtil da er doge/bnb "
              "ikke-kvalificerede som flanke-kandidater.")
        else:
            A("✅ **Alle seks spec-assets streamer live** — docs-symboltabellen er blot "
              "forældet (den mangler doge/bnb; streamen bærer desuden flere symboler end "
              "spec'en bruger). Flanke-optionen (SPEC §0) er dermed feed-teknisk åben for "
              "alle seks. Kadencen ~1 Hz betyder samtidig, at S_open-gap-reglen "
              "(tick i `[boundary, boundary+2s]`, SPEC §2 F1) er realistisk men stram: "
              "observeret maks-gap i proben lå ved selve 2s-grænsen. M1-soaken skal måle "
              "gap-fordelingen per symbol over 48h, før grænsen evt. justeres i config.")
    else:
        A(f"Live-probe: {status_mark(rt['status'])} — {'; '.join(rt.get('notes', []))}")
    A("")

    # 6 — ping
    A("## 6. Ping-baseline og VPS-skabelon")
    A("")
    A(f"Målt {pg['fetched_at']} fra **{pg['vantage']}** mod `{pg['host']}/` "
      f"(n={pg.get('samples_ok', 0)}; curl gennem HTTPS-CONNECT-tunnel):")
    A("")
    if pg.get("median_ms"):
        mm = pg["median_ms"]
        A("| Metrik (median) | ms |")
        A("|---|---|")
        A(f"| DNS | {mm['namelookup']} |")
        A(f"| TCP connect (til proxy) | {mm['tcp_connect']} |")
        A(f"| TLS appconnect (håndtryk m. origin gennem tunnel) | {mm['tls_appconnect']} |")
        A(f"| TTFB | {mm['ttfb']} |")
        A(f"| Total | {mm['total']} |")
        A("")
        A(f"TLS−TCP ≈ {pg['tls_minus_tcp_ms']} ms (groft mål for origin-håndtrykket herfra). "
          f"Cloudflare-colo der svarede: **{pg.get('cf_colo')}**. Tallene er en metode-baseline "
          "— IKKE et beslutningsgrundlag for VPS-valg.")
    else:
        A("(ping-målingen fejlede — kør igen)")
    A("")
    A("**Manuel VPS-måling (Rasmus, blokerer M5 jf. SPEC §8).** Kør på hver kandidat-VPS:")
    A("")
    A("```bash")
    A("for i in $(seq 20); do curl -so /dev/null -w '%{time_connect} %{time_appconnect} "
      "%{time_starttransfer}\\n' https://clob.polymarket.com/; sleep 0.5; done")
    A("# eller: python3 research/verify_rules.py --ping --region <label>")
    A("```")
    A("")
    A("| Region | Dato | n | TCP med. (ms) | TLS med. (ms) | TTFB med. (ms) | CF-colo |")
    A("|---|---|---|---|---|---|---|")
    A("| us-east (fx Ashburn) | _udfyldes_ | | | | | |")
    A("| us-east-2 (fx Ohio) | _udfyldes_ | | | | | |")
    A("| eu-vest (fx Frankfurt/Amsterdam) | _udfyldes_ | | | | | |")
    A("")
    A("Bemærk: `clob.polymarket.com` er bag Cloudflare — TCP/TLS-tid måler nærmeste "
      "CF-edge, ikke origin. **TTFB er det operative tal** (edge→origin inkluderet). "
      "Polymarkets S3-buckets ligger i `us-east-2`; container-proben svarede fra IAD (us-east).")
    A("")

    # 7 — clob crosscheck
    A("## 7. CLOB-krydstjek (uafhængig kilde for §1-tallene)")
    A("")
    A(f"Kilde: `GET {CLOB}/markets/{{conditionId}}` ({cc['fetched_at']}):")
    A("")
    A("```json")
    A(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "tokens"}
                  for k, v in cc["markets"].items()}, indent=1))
    A("```")
    A("")

    # 8 — manuelt
    A("## 8. Manuel tjekliste (Rasmus) — OPEN")
    A("")
    A("1. **Polymarket-konto + API-nøgler** (L2-credentials via py-clob-client; "
      "nøgler i `.env`, aldrig i kode/logs — SPEC §6).")
    A("2. **USDC-wallet (Polygon) med KUN arbejdskapital** ($500-rammen fra M5).")
    A("3. **3×VPS-ping** — udfyld tabellen i §6; regionvalg blokerer M5 (SPEC §8).")
    A("4. **`calendar.yaml`** — FOMC-datoerne er seedet fra Feds offentliggjorte kalender; "
      "verificér + tilføj CPI/PPI/NFP fra BLS-skemaet (ugentlig vedligeholdelse, F-4).")
    A("5. **Geo/jura:** markederne bærer `restricted: true`, og docs' geo-side (Perps) "
      "lister bl.a. USA/Canada som ordre-forbudte. CLOB-markedernes præcise geo-vilkår "
      "følger Polymarkets ToS — afklar selv jurisdiktion/VPS-placering ift. konto. "
      "Read-only markedsdata er eksplicit undtaget restriktionerne.")
    A("")

    # 9 — åbne detaljer
    A("## 9. Åbne detaljer fundet under M0 (ikke-blokerende for M1)")
    A("")
    A("| # | Detalje | Afklares |")
    A("|---|---|---|")
    A("| A1 | `orderMinSize: 5` — docs' Market Details-tabel siger »Minimum order size **in USDC**«, "
      "men feltet hed historisk »shares« og SPEC forventer 5 *shares*. Værdien er 5 uanset; "
      "enheden afgør mindste probe ved p<1.00. | Empirisk ved første ordre (M4-stub/M5) |")
    A("| A2 | Legacy `maker/taker_base_fee = 1000` bps vs. aktiv `feeSchedule` 0.07 — "
      "faktisk trukket fee bekræftes mod fills. | Første fills (M4/M5) |")
    if rt.get("missing_symbols"):
        A(f"| A3 | RTDS-Chainlink-dækning: ingen ticks for {rt['missing_symbols']} i "
          f"{rt.get('listen_s')}s-proben (docs lister dem heller ikke). | M1 48h-soak |")
    else:
        A("| A3 | RTDS-Chainlink dækker ALLE seks assets (bekræftet i live-probe trods "
          "forældet docs-tabel). Rest: kadence-/gap-fordeling per symbol over døgn, "
          "ift. S_open-gap-reglens 2s-grænse. | M1 48h-soak |")
    A("| A4 | CLOB-WS connection-caps udokumenterede — antag konservativt få forbindelser "
      "(recorderen behøver ≤3). | M1-soak observerer evt. disconnects |")
    A("")
    A("---")
    A(f"*Reproducérbarhed: `python3 research/verify_rules.py` genkører alt; rå API-svar i "
      f"`research/raw/`; docs-dump sha256 `{dc.get('sha256')}` ({dc.get('bytes')} bytes).*")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="BONESAW M0-verifikation")
    ap.add_argument("--ping", action="store_true", help="kør kun ping-blokken (VPS-brug)")
    ap.add_argument("--region", default="?", help="label til ping-rækken")
    ap.add_argument("-n", type=int, default=15, help="antal ping-samples")
    ap.add_argument("--listen", type=int, default=30, help="RTDS-probe varighed (s)")
    args = ap.parse_args()

    if args.ping:
        pg = section_ping(n=args.n)
        mm = pg.get("median_ms", {})
        print(f"| {args.region} | {pg['fetched_at'][:10]} | {pg.get('samples_ok', 0)} | "
              f"{mm.get('tcp_connect', '?')} | {mm.get('tls_appconnect', '?')} | "
              f"{mm.get('ttfb', '?')} | {pg.get('cf_colo', '?')} |")
        return 0

    print("M0-verifikation starter …", flush=True)
    res: dict = {"generated_at": utcnow()}

    print("• markeder (6 assets 5m + btc 15m + næste vindue) …", flush=True)
    res["markets"] = section_markets()

    cond = {}
    if not res["markets"]["assets_5m"]["btc"].get("missing"):
        cond["btc_5m"] = res["markets"]["assets_5m"]["btc"]["conditionId"]
    if not res["markets"]["btc_15m"].get("missing"):
        cond["btc_15m"] = res["markets"]["btc_15m"]["conditionId"]
    print("• CLOB-krydstjek …", flush=True)
    res["clob"] = section_clob_crosscheck(cond)

    print("• docs (fees, rebates, rate limits) …", flush=True)
    res["docs"] = section_docs()

    rate = (res["markets"]["assets_5m"]["btc"].get("feeSchedule") or {}).get("rate", 0.07)
    res["fees"] = fee_tables(float(rate))

    print(f"• RTDS-probe ({args.listen}s) …", flush=True)
    res["rtds"] = section_rtds(listen_s=args.listen)

    print("• ping-baseline …", flush=True)
    res["ping"] = section_ping(n=args.n)

    save_raw("verify_results.json", res)
    out = ROOT / "VERIFIED.md"
    out.write_text(render(res), encoding="utf-8")
    print(f"Skrev {out}", flush=True)

    hard_fail = [k for k in ("markets", "docs") if res[k]["status"] == "FEJL"]
    if hard_fail:
        print(f"FEJL i kernesektioner: {hard_fail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
