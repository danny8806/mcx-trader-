# FINAL_VERIFIED_ARCHITECTURE — MCX-TRADER (CENTRAL PARK repo)

Audit legs: PASS 1 (5 static-discovery agents) + PASS 2 (independent _audit_pass2.py AST scan + full rig re-run).
Window: 2 passes agree only on the CURRENT working tree. Generated 2026-08-31 05:28 UTC

## System shape (PASS-2 independent recount)
- 103 production Python modules / 21251 non-test source lines
- 80 classes, 785 functions/methods, 56 router/app route decorators, 1 WS endpoint
- 160 non-test source files total (py/tsx/ts/json/css/html) — see AUDIT_FILE_INVENTORY.csv

## Layers (verified present and wired)
- `engine/` — TradingEngine (singleton), lifecycle, EOD, safe-mode, market gating
- `core/` — market_status, fill_dedup, indicators (DEMA-ATR), schemas, config
- `indicators/` — DEMA, ATR, DEMAATR (snapshot with ema1/ema2/tr_values/count/initialized)
- `execution/` — order dispatch + fill capture (Dhan)
- `persistence/` — SQLite manager (orders/fills/trades/checkpoint/system_state), get_fill + save_fill
- `dashboard/` — FastAPI app, 56 routes, 1 /ws endpoint, env-gated admin auth
- `dashboard-ui/` — React/Vite frontend (built: dist regenerated, tsc -b clean)
- `strategies/` — 3 strategies (4 instances) source-discriminated
- `analytics/`, `reconciliation/`, `monitoring/`, `portfolio/`, `htf/`, `notifications/`

## Verified invariants (5-day lifecycle, 73/73)
- I0 no safe-mode; I1-I2 zero dup fills + dedup covers DB; I3 orders all filled;
  I4 trades status ok; I5 accounts match; I6 equity identity; I7 margin identity;
  I8 reconciliation consistent; I9 entry-fill chain complete; I10 restart restores
  strategies+positions; I11 WS disconnect->recover; I12 REST-outage tolerant + warmup
  rebuild; I13 restart restores position set; I14 crash state recovered + faithful
  checkpoint + no dup rows; I15 replayed crash-window fill IGNORED with position kept
  open (post-fix behavior); I16 audit actually traded.

## Auth topology (fixed)
DASHBOARD_API_KEY env optional: if set -> HTTP 403 unless x-api-key matches
(secrets.compare_digest), /api/health exempt, /ws requires ?key= else close 1008.
Open by default for local paper trading.
