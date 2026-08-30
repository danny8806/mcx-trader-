# P4 - FULL-FLOW START-TO-END E2E (fresh re-run, full depth, 2026-08-30)

Whole pipeline re-run NEWLY on the CURRENT live code with a FRESH temp
engine per instrument; every stage's input->output captured and verified
with independent recomputation (no production code is its own reference,
no past audit file is read).

- Inputs: GOLDM 870, SILVERM 870 raw 5m rows -> bars/ind/map/strat/order/fill/pos/pnl/db.
- Strobed per contact: every bar of every strategy, every fill, every
  closed trade, every day boundary.

## Component checklist (input -> output)
| component | result |
|-----------|--------|
| `RAW_GOLDM_input_valid` | **PASS** | rows=870 first=2026-08-24 09:00 last=2026-08-28 23:25 |
| `BARS_GOLDM_5m_reagg` | **PASS** | bars5=870 buckets=870 rows=870 covered=870 |
| `BARS_GOLDM_15m_1h_agg` | **PASS** | bars15=290 bars1h=75 bad15=0 bad1h=0 |
| `INDMAP_GOLDM_per_bar_parity` | **PASS** | bars_compared=1150 mismatches=0 |
| `ORDERFILL_GOLDM_prices_exact` | **PASS** | fills=44 price_mismatch=0 |
| `PNL_GOLDM_closed_trades_all` | **PASS** | closed=21 pnl_mismatch=0 eod_close_rows=0 |
| `DB_GOLDM_integrity` | **PASS** | orders=44 fills=44 closed=21 open_engine=2 (expect fills=2*closed+open) dup_orders=0 dup_fills=0 orphan=0 trade_closed_ev=21 ledger_closed/open_parity=OK trade_row_match_bad=0 |
| `ACCT_GOLDM_gold_01_realized_is_sum` | **PASS** | acct_realized=-44578.689999999995 sum_nets=-44578.69 pnl_gross=-40240.0 pnl_net=-44578.69 trades=14 |
| `ACCT_GOLDM_gold_02_realized_is_sum` | **PASS** | acct_realized=-21446.69 sum_nets=-21446.69 pnl_gross=-19280.0 pnl_net=-21446.69 trades=7 |
| `EOD_GOLDM_guard_inert` | **PASS** | fired=0 calls=0 sims=16 |
| `EOD_GOLDM_positions_preserved` | **PASS** | boundaries=4 preserved=True |
| `CARRY_GOLDM_overnight_holds` | **PASS** | carried_holds=3 ok=3 |
| `RAW_SILVERM_input_valid` | **PASS** | rows=870 first=2026-08-24 09:00 last=2026-08-28 23:25 |
| `BARS_SILVERM_5m_reagg` | **PASS** | bars5=870 buckets=870 rows=870 covered=870 |
| `BARS_SILVERM_15m_1h_agg` | **PASS** | bars15=290 bars1h=75 bad15=0 bad1h=0 |
| `INDMAP_SILVERM_per_bar_parity` | **PASS** | bars_compared=1150 mismatches=0 |
| `ORDERFILL_SILVERM_prices_exact` | **PASS** | fills=46 price_mismatch=0 |
| `PNL_SILVERM_closed_trades_all` | **PASS** | closed=22 pnl_mismatch=0 eod_close_rows=0 |
| `DB_SILVERM_integrity` | **PASS** | orders=46 fills=46 closed=22 open_engine=2 (expect fills=2*closed+open) dup_orders=0 dup_fills=0 orphan=0 trade_closed_ev=22 ledger_closed/open_parity=OK trade_row_match_bad=0 |
| `ACCT_SILVERM_silver_01_realized_is_sum` | **PASS** | acct_realized=-12840.689999999999 sum_nets=-12840.69 pnl_gross=-10555.0 pnl_net=-12840.69 trades=9 |
| `ACCT_SILVERM_silver_02_realized_is_sum` | **PASS** | acct_realized=-21042.740000000005 sum_nets=-21042.74 pnl_gross=-17745.0 pnl_net=-21042.74 trades=13 |
| `EOD_SILVERM_guard_inert` | **PASS** | fired=0 calls=0 sims=16 |
| `EOD_SILVERM_positions_preserved` | **PASS** | boundaries=4 preserved=True |
| `CARRY_SILVERM_overnight_holds` | **PASS** | carried_holds=6 ok=6 |

## Stages verified
1. raw input  - rows sorted, gap-less, finite, OHLC valid.
2. bars       - every 5m bar == first/max/min/last/sum of its raw rows;
   every 15m == its 5m members; every 1h == its 15m members (all 3 feeds).
3. indicator  - engine DEMAATR per bar == independent ref_dema_atr.
4. mapping    - engine searchsorted 1h/15m->fast mapping == independent
   bisect_right(end_times, fast.end_ts)-1, PER BAR.
5. strategy   - state machine pre/post side + pending trigger/stop tracked
   per bar (reversal/stop/pending-consistency is input->output of the flow).
6. order/fill - 1 signal->1 order->1 fill; breakout entry fill == pending
   trigger, stop exit fill == breaking bar CLOSE, reversal/deferred exit
   fill == next bar OPEN (rated against the real fed bar).
7. position   - one open at a time per strategy; entry at fill price;
   carried across nights (no EOD close) till reversal/stop.
8. pnl        - every closed trade gross/charges/net == independent model;
   account realized == sum of its own nets == pnl-engine totals.
9. db         - no dup orders/fills, no orphan fills, orders==fills==2x
   closed trades, trade_closed events == closed trades.
10. eod       - should_force_close inert in all 4 session states at every
    day boundary; positions + position_id survive the break untouched.

Input/Output detail rows -> P4_FULL_FLOW_E2E_INPUT_OUTPUT.csv