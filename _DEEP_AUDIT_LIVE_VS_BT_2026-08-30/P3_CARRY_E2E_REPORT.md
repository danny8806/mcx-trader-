# P3 - CARRY / NO-EOD-EXIT E2E INPUT->OUTPUT TEST (fresh re-run, 2026-08-30)

Everything re-run on the CURRENT live code with a FRESH engine temp dir;
no past result file is read or trusted. Real PaperBroker (slippage=0 ->
fills equal model price), real OrderManager / TradeCloseManager / P&L
accounts / persistence DB / all 4 strategies parallel. Input: real LAST5
5m stream fed DAY-BY-DAY; after every non-final day the engine's EOD
guard (`_on_tick` -> `_execute_eod_close`) is replayed in all four
session states with a spy on `_execute_eod_close`.

- Input rows: GOLDM 870, SILVERM 870 (5m)
- Nightly held-across-boundary carries verified: **9**
- Pending breakouts carried across the break: **2**
- Carried-over-night closed trades P&L-verified: **9**

## Component checklist
| component | result |
|-----------|--------|
| `P_parallel_GOLDM_gold_01_processed` | **PASS** | bars=865 acct=True pnl=True |
| `P_parallel_GOLDM_gold_02_processed` | **PASS** | bars=285 acct=True pnl=True |
| `P_parallel_SILVERM_silver_01_processed` | **PASS** | bars=285 acct=True pnl=True |
| `P_parallel_SILVERM_silver_02_processed` | **PASS** | bars=865 acct=True pnl=True |
| `EOD_inert_GOLDM_guard_never_fires` | **PASS** | fired=0 _execute_eod_close_calls=0 sims=16 |
| `EOD_positions_preserved_GOLDM` | **PASS** | day-boundaries=4 preserved=True |
| `EOD_inert_SILVERM_guard_never_fires` | **PASS** | fired=0 _execute_eod_close_calls=0 sims=16 |
| `EOD_positions_preserved_SILVERM` | **PASS** | day-boundaries=4 preserved=True |
| `DB_GOLDM_no_eod_close_anywhere` | **PASS** | eod_close_trades=0 |
| `DB_GOLDM_no_dup_fills` | **PASS** | dup=0 |
| `DB_GOLDM_no_dup_orders` | **PASS** | dup=0 |
| `DB_GOLDM_no_orphan_fills` | **PASS** | orphan=0 |
| `DB_GOLDM_tracking` | **PASS** | closed=21 trade_closed_events=21 |
| `DB_SILVERM_no_eod_close_anywhere` | **PASS** | eod_close_trades=0 |
| `DB_SILVERM_no_dup_fills` | **PASS** | dup=0 |
| `DB_SILVERM_no_dup_orders` | **PASS** | dup=0 |
| `DB_SILVERM_no_orphan_fills` | **PASS** | orphan=0 |
| `DB_SILVERM_tracking` | **PASS** | closed=22 trade_closed_events=22 |
| `B1_GOLDM_gold_01_carry@2026-08-24->2026-08-25` | **PASS** | end=SHORT@162340.0 pre=SHORT pid=3e2285c6-fc29-40f5-bcca-0ae08d080766 same_trade=True pend=None |
| `B2_GOLDM_gold_02_carry@2026-08-24->2026-08-25` | **PASS** | end=SHORT@162300.0 pre=SHORT pid=c1dfb08d-89c6-401d-b478-c34ae076e08c same_trade=True pend=None |
| `B3_GOLDM_gold_02_carry@2026-08-25->2026-08-26` | **PASS** | end=SHORT@161449.0 pre=SHORT pid=754adc78-f8bc-4415-86ca-4c18b0db1654 same_trade=True pend=None |
| `B4_SILVERM_silver_01_carry@2026-08-24->2026-08-25` | **PASS** | end=SHORT@255397.0 pre=SHORT pid=79391492-62e2-4ce2-aab8-7a1c9f46f90b same_trade=True pend=None |
| `B5_SILVERM_silver_01_carry@2026-08-25->2026-08-26` | **PASS** | end=LONG@252149.0 pre=LONG pid=602be540-eab1-4ebe-93e2-0c4ea3940c14 same_trade=True pend=None |
| `B6_SILVERM_silver_01_carry@2026-08-27->2026-08-28` | **PASS** | end=LONG@249750.0 pre=LONG pid=ab0d9c28-3109-4891-992b-816893ad7d31 same_trade=True pend=None |
| `B7_SILVERM_silver_02_carry@2026-08-24->2026-08-25` | **PASS** | end=SHORT@255397.0 pre=SHORT pid=760bf9e9-b474-4ddd-abe3-58b0e40872b4 same_trade=True pend=None |
| `B8_SILVERM_silver_02_carry@2026-08-25->2026-08-26` | **PASS** | end=LONG@251897.0 pre=LONG pid=285c4edf-c6a2-4c62-882e-73d2e2414092 same_trade=True pend=None |
| `B9_SILVERM_silver_02_carry@2026-08-27->2026-08-28` | **PASS** | end=LONG@249750.0 pre=LONG pid=ed6b73b4-2952-445c-b742-19f993a2a164 same_trade=True pend=None |
| `G_carry_boundaries_found` | **PASS** | nightly carried positions = 9 |
| `G_carry_all_ok` | **PASS** | ok=9/9 |
| `PEND_GOLDM_gold_01_carry@2026-08-27->2026-08-28` | **PASS** | pend=SHORT armed end=23:25 trigger=158074.0 pre_next=SHORT entry_fill_at_trigger=True |
| `PEND_GOLDM_gold_02_carry@2026-08-27->2026-08-28` | **PASS** | pend=SHORT armed end=23:15 trigger=158074.0 pre_next=SHORT entry_fill_at_trigger=True |
| `G_pending_carried` | **PASS** | pending boundaries = 2 |
| `G_carried_pnl_trades_found` | **PASS** | carried overnight trades = 9 |
| `G_carried_pnl_all_ok` | **PASS** | ok=9/9 |
| `PL_GOLDM_gold_01_carried_2026-08-24->2026-08-25` | **PASS** | SHORT entry=162340.0 exit=162426.0 pid=3e2285c6-fc29-40f5-bcca-0ae08d080766 gross=-860.0 chg=313.01 net=-1173.01 reason=long_reversal nights=1 |
| `PL_GOLDM_gold_02_carried_2026-08-24->2026-08-25` | **PASS** | SHORT entry=162300.0 exit=162452.0 pid=c1dfb08d-89c6-401d-b478-c34ae076e08c gross=-1520.0 chg=312.97 net=-1832.97 reason=long_reversal nights=1 |
| `PL_GOLDM_gold_02_carried_2026-08-25->2026-08-26` | **PASS** | SHORT entry=161449.0 exit=161771.0 pid=754adc78-f8bc-4415-86ca-4c18b0db1654 gross=-3220.0 chg=311.63 net=-3531.63 reason=stop_loss_hit nights=1 |
| `PL_SILVERM_silver_01_carried_2026-08-24->2026-08-25` | **PASS** | SHORT entry=255397.0 exit=253001.0 pid=79391492-62e2-4ce2-aab8-7a1c9f46f90b gross=11980.0 chg=255.89 net=11724.11 reason=long_reversal nights=1 |
| `PL_SILVERM_silver_01_carried_2026-08-25->2026-08-26` | **PASS** | LONG entry=252149.0 exit=253167.0 pid=602be540-eab1-4ebe-93e2-0c4ea3940c14 gross=5090.0 chg=254.28 net=4835.72 reason=short_reversal nights=1 |
| `PL_SILVERM_silver_01_carried_2026-08-27->2026-08-28` | **PASS** | LONG entry=249750.0 exit=248098.0 pid=ab0d9c28-3109-4891-992b-816893ad7d31 gross=-8260.0 chg=250.56 net=-8510.56 reason=short_reversal nights=1 |
| `PL_SILVERM_silver_02_carried_2026-08-24->2026-08-25` | **PASS** | SHORT entry=255397.0 exit=253001.0 pid=760bf9e9-b474-4ddd-abe3-58b0e40872b4 gross=11980.0 chg=255.89 net=11724.11 reason=long_reversal nights=1 |
| `PL_SILVERM_silver_02_carried_2026-08-25->2026-08-26` | **PASS** | LONG entry=251897.0 exit=253167.0 pid=285c4edf-c6a2-4c62-882e-73d2e2414092 gross=6350.0 chg=254.24 net=6095.76 reason=short_reversal nights=1 |
| `PL_SILVERM_silver_02_carried_2026-08-27->2026-08-28` | **PASS** | LONG entry=249750.0 exit=248263.0 pid=ed6b73b4-2952-445c-b742-19f993a2a164 gross=-7435.0 chg=250.66 net=-7685.66 reason=stop_loss_hit nights=1 |
| `ACCT_GOLDM_gold_01_realized` | **PASS** | realized=-44578.689999999995 sum_of_its_trades=-44578.69 closed=14 |
| `ACCT_GOLDM_gold_02_realized` | **PASS** | realized=-21446.69 sum_of_its_trades=-21446.690000000002 closed=7 |
| `ACCT_SILVERM_silver_01_realized` | **PASS** | realized=-12840.689999999999 sum_of_its_trades=-12840.689999999999 closed=9 |
| `ACCT_SILVERM_silver_02_realized` | **PASS** | realized=-21042.740000000005 sum_of_its_trades=-21042.739999999998 closed=13 |

**VERDICT: ALL PASSED**

Components tested (input->output):
1. Trade placement  - orders+fills created at the right model price;
   a breakout pending carried overnight places its entry fill at the
   pending trigger on the next session.
2. P&L calc         - carried trades (entry_day != exit_day): gross =
   (exit-entry)*mult*qty, charges = fee model, net = gross-charges.
3. Trade saving     - carried trades persisted open->closed with their
   fills/orders; no duplicate/orphan rows; trade_closed events == closed
   trades.
4. NO EOD exit      - should_force_close() is False in LIVE_TRADING /
   MARKET_CLOSE / AFTER_MARKET / OVERNIGHT at every day boundary;
   `_execute_eod_close` is never invoked; positions and their entry
   prices survive the break untouched.
5. Carried pending  - the armed breakout pending (trigger/SL) survives
   the break and resolves only by fill / expiry rule.
6. Parallel accounts- each strategy's account realized == the sum of its
   own closed trades (realized does not include the still-open carried
   position).

Input/Output detail rows -> P3_CARRY_E2E_INPUT_OUTPUT.csv