# PARKING.md — parkerede idéer (SPEC §6: ingen scope-udvidelser undervejs)

Idéer opstået under arbejdet. Ingen af dem bygges uden ny beslutning fra Rasmus.

1. **RTDS Binance-relay som F2-sanity-check.** RTDS har også topic
   `crypto_prices` (Binance-priser relayed af Polymarket). Kunne bruges som
   billig krydsvalidering af vores direkte Binance-WS (F2) — samme socket som F1.
   (Fundet under M0, 2026-07-27.)
2. **`GET /rebates/current` (CLOB) til rebate-tracking.** Uautentificeret
   endpoint der viser faktisk rebatede fees per maker-adresse per dag. Oplagt til
   SCOREBOARD/økonomi-rapportering fra M4. (Fundet under M0.)
3. **Taker-tier-bevidst økonomimodel.** Taker Rebate-programmet (0–50 % efter
   30-dages weighted volume, crypto-vægt 2,3) kan modelleres som fee-reduktions-
   kurve i Gate B — og sizing kunne i princippet optimeres mod tier-tærskler.
   Det sidste er en scope-udvidelse: parkeret. (Fundet under M0.)
