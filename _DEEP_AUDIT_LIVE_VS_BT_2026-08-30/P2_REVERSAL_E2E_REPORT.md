# P2 — REVERSAL E2E INPUT->OUTPUT TEST (fresh, 2026-08-30)

Full-chain reversal verification on the CURRENT live code — real OrderManager,
PaperBroker (slippage=0 so fills equal model prices), TradeCloseManager, P&L
accounts, persistence DB, all 4 strategies parallel. Input: the real LAST5
5m stream. No past result files used.

- Input rows: GOLDM 870, SILVERM 870 (5m)
- Reversal scenarios located+verified: **21**

| check | result |
|-------|--------|
| P_parallel_GOLDM_gold_01_processed | **PASS** | bars=865 acct=True pnl=True |
| P_parallel_GOLDM_gold_02_processed | **PASS** | bars=285 acct=True pnl=True |
| P_parallel_SILVERM_silver_01_processed | **PASS** | bars=285 acct=True pnl=True |
| P_parallel_SILVERM_silver_02_processed | **PASS** | bars=865 acct=True pnl=True |
| A1_GOLDM_gold_01_rev@08-25 11:50 | **PASS** | SHORT->LONG trigger=162440.0 |
|   A1_armed(LONG) | **PASS** | exit=True reason=long_reversal trig=162440.0 stop=162151.0 |
|   A2_exit_fill_next_open | **PASS** | side=BUY px=162426.0 exp=162426.0 |
|   A3_trade_saved | **PASS** | side=SHORT exit=162426.0 st=closed |
|   A4_pnl_math | **PASS** | gross=-860.0 chg=313.01 |
|   A5_net | **PASS** | net=-1173.01 |
|   A6_parallel_account | **PASS** | strat_realized=-44578.689999999995 sum_trades=-44578.69 |
|   A7_bt_reentry_ref | **PASS** | bt_reentry=162426.0 live_reentry=True live_trigger=162440.0 delta/lot=140.0 |
| A1_GOLDM_gold_01_rev@08-25 22:00 | **PASS** | LONG->SHORT trigger=161650.0 |
|   A1_armed(SHORT) | **PASS** | exit=True reason=short_reversal trig=161650.0 stop=161803.0 |
|   A2_exit_fill_next_open | **PASS** | side=SELL px=161650.0 exp=161650.0 |
|   A3_trade_saved | **PASS** | side=LONG exit=161650.0 st=closed |
|   A4_pnl_math | **PASS** | gross=-3960.0 chg=311.98 |
|   A5_net | **PASS** | net=-4271.98 |
|   A6_parallel_account | **PASS** | strat_realized=-44578.689999999995 sum_trades=-44578.69 |
|   A7_bt_reentry_ref | **PASS** | bt_reentry=161650.0 live_reentry=True live_trigger=161650.0 delta/lot=0.0 |
| A1_GOLDM_gold_01_rev@08-26 17:45 | **PASS** | SHORT->LONG trigger=161451.0 |
|   A1_armed(LONG) | **PASS** | exit=True reason=long_reversal trig=161451.0 stop=161257.0 |
|   A2_exit_fill_next_open | **PASS** | side=BUY px=161450.0 exp=161450.0 |
|   A3_trade_saved | **PASS** | side=SHORT exit=161450.0 st=closed |
|   A4_pnl_math | **PASS** | gross=-130.0 chg=311.51 |
|   A5_net | **PASS** | net=-441.51 |
|   A6_parallel_account | **PASS** | strat_realized=-44578.689999999995 sum_trades=-44578.69 |
|   A7_bt_reentry_ref | **PASS** | bt_reentry=161360.0 live_reentry=True live_trigger=161451.0 delta/lot=910.0 |
| A1_GOLDM_gold_01_rev@08-27 22:45 | **PASS** | LONG->SHORT trigger=158005.0 |
|   A1_armed(SHORT) | **PASS** | exit=True reason=short_reversal trig=158005.0 stop=158333.0 |
|   A2_exit_fill_next_open | **PASS** | side=SELL px=158102.0 exp=158102.0 |
|   A3_trade_saved | **PASS** | side=LONG exit=158102.0 st=closed |
|   A4_pnl_math | **PASS** | gross=-3490.0 chg=306.16 |
|   A5_net | **PASS** | net=-3796.16 |
|   A6_parallel_account | **PASS** | strat_realized=-44578.689999999995 sum_trades=-44578.69 |
|   A7_bt_reentry_ref | **PASS** | bt_reentry=158101.0 live_reentry=True live_trigger=158005.0 delta/lot=220.0 |
| A1_GOLDM_gold_01_rev@08-28 12:25 | **PASS** | SHORT->LONG trigger=158400.0 |
|   A1_armed(LONG) | **PASS** | exit=True reason=long_reversal trig=158400.0 stop=157950.0 |
|   A2_exit_fill_next_open | **PASS** | side=BUY px=158305.0 exp=158305.0 |
|   A3_trade_saved | **PASS** | side=SHORT exit=158305.0 st=closed |
|   A4_pnl_math | **PASS** | gross=-2310.0 chg=306.07 |
|   A5_net | **PASS** | net=-2616.07 |
|   A6_parallel_account | **PASS** | strat_realized=-44578.689999999995 sum_trades=-44578.69 |
|   A7_bt_reentry_ref | **PASS** | bt_reentry=158352.0 live_reentry=True live_trigger=158400.0 delta/lot=480.0 |
| A1_GOLDM_gold_01_rev@08-28 19:40 | **PASS** | LONG->SHORT trigger=158253.0 |
|   A1_armed(SHORT) | **PASS** | exit=True reason=short_reversal trig=158253.0 stop=159100.0 |
|   A2_exit_fill_next_open | **PASS** | side=SELL px=158342.0 exp=158342.0 |
|   A3_trade_saved | **PASS** | side=LONG exit=158342.0 st=closed |
|   A4_pnl_math | **PASS** | gross=-580.0 chg=306.46 |
|   A5_net | **PASS** | net=-886.46 |
|   A6_parallel_account | **PASS** | strat_realized=-44578.689999999995 sum_trades=-44578.69 |
|   A7_bt_reentry_ref | **PASS** | bt_reentry=158342.0 live_reentry=True live_trigger=158253.0 delta/lot=-890.0 |
| A1_GOLDM_gold_02_rev@08-24 13:45 | **PASS** | LONG->SHORT trigger=161800.0 |
|   A1_armed(SHORT) | **PASS** | exit=True reason=short_reversal trig=161800.0 stop=162312.0 |
|   A2_exit_fill_next_open | **PASS** | side=SELL px=161814.0 exp=161814.0 |
|   A3_trade_saved | **PASS** | side=LONG exit=161814.0 st=closed |
|   A4_pnl_math | **PASS** | gross=-2540.0 chg=312.2 |
|   A5_net | **PASS** | net=-2852.2 |
|   A6_parallel_account | **PASS** | strat_realized=-21446.69 sum_trades=-21446.690000000002 |
|   A7_bt_reentry_ref | **PASS** | bt_reentry=162394.0 live_reentry=True live_trigger=161800.0 delta/lot=-940.0 |
| A1_GOLDM_gold_02_rev@08-25 11:45 | **PASS** | SHORT->LONG trigger=162546.0 |
|   A1_armed(LONG) | **PASS** | exit=True reason=long_reversal trig=162546.0 stop=162100.0 |
|   A2_exit_fill_next_open | **PASS** | side=BUY px=162452.0 exp=162452.0 |
|   A3_trade_saved | **PASS** | side=SHORT exit=162452.0 st=closed |
|   A4_pnl_math | **PASS** | gross=-1520.0 chg=312.97 |
|   A5_net | **PASS** | net=-1832.97 |
|   A6_parallel_account | **PASS** | strat_realized=-21446.69 sum_trades=-21446.690000000002 |
|   A7_bt_reentry_ref | **PASS** | bt_reentry=161471.0 live_reentry=True live_trigger=162546.0 delta/lot=190.0 |
| A1_GOLDM_gold_02_rev@08-27 22:45 | **PASS** | LONG->SHORT trigger=158005.0 |
|   A1_armed(SHORT) | **PASS** | exit=True reason=short_reversal trig=158005.0 stop=158529.0 |
|   A2_exit_fill_next_open | **PASS** | side=SELL px=158167.0 exp=158167.0 |
|   A3_trade_saved | **PASS** | side=LONG exit=158167.0 st=closed |
|   A4_pnl_math | **PASS** | gross=-4970.0 chg=306.31 |
|   A5_net | **PASS** | net=-5276.31 |
|   A6_parallel_account | **PASS** | strat_realized=-21446.69 sum_trades=-21446.690000000002 |
|   A7_bt_reentry_ref | **PASS** | bt_reentry=156987.0 live_reentry=True live_trigger=158005.0 delta/lot=10870.0 |
| A1_GOLDM_gold_02_rev@08-28 12:15 | **PASS** | SHORT->LONG trigger=158400.0 |
|   A1_armed(LONG) | **PASS** | exit=True reason=long_reversal trig=158400.0 stop=157550.0 |
|   A2_exit_fill_next_open | **PASS** | side=BUY px=158305.0 exp=158305.0 |
|   A3_trade_saved | **PASS** | side=SHORT exit=158305.0 st=closed |
|   A4_pnl_math | **PASS** | gross=-2310.0 chg=306.07 |
|   A5_net | **PASS** | net=-2616.07 |
|   A6_parallel_account | **PASS** | strat_realized=-21446.69 sum_trades=-21446.690000000002 |
|   A7_bt_reentry_ref | **PASS** | bt_reentry=158228.0 live_reentry=True live_trigger=158400.0 delta/lot=1720.0 |
| A1_GOLDM_gold_02_rev@08-28 19:30 | **PASS** | LONG->SHORT trigger=158014.0 |
|   A1_armed(SHORT) | **PASS** | exit=True reason=short_reversal trig=158014.0 stop=159706.0 |
|   A2_exit_fill_next_open | **PASS** | side=SELL px=158342.0 exp=158342.0 |
|   A3_trade_saved | **PASS** | side=LONG exit=158342.0 st=closed |
|   A4_pnl_math | **PASS** | gross=-580.0 chg=306.46 |
|   A5_net | **PASS** | net=-886.46 |
|   A6_parallel_account | **PASS** | strat_realized=-21446.69 sum_trades=-21446.690000000002 |
|   A7_bt_reentry_ref | **PASS** | bt_reentry=158342.0 live_reentry=True live_trigger=158014.0 delta/lot=-3280.0 |
| A1_SILVERM_silver_01_rev@08-25 11:45 | **PASS** | SHORT->LONG trigger=253333.0 |
|   A1_armed(LONG) | **PASS** | exit=True reason=long_reversal trig=253333.0 stop=251673.0 |
|   A2_exit_fill_next_open | **PASS** | side=BUY px=253001.0 exp=253001.0 |
|   A3_trade_saved | **PASS** | side=SHORT exit=253001.0 st=closed |
|   A4_pnl_math | **PASS** | gross=11980.0 chg=255.89 |
|   A5_net | **PASS** | net=11724.11 |
|   A6_parallel_account | **PASS** | strat_realized=-12840.689999999999 sum_trades=-12840.689999999999 |
|   A7_bt_reentry_ref | **PASS** | bt_reentry=253001.0 live_reentry=True live_trigger=253333.0 delta/lot=1660.0 |
| A1_SILVERM_silver_01_rev@08-26 11:45 | **PASS** | LONG->SHORT trigger=253025.0 |
|   A1_armed(SHORT) | **PASS** | exit=True reason=short_reversal trig=253025.0 stop=254624.0 |
|   A2_exit_fill_next_open | **PASS** | side=SELL px=253167.0 exp=253167.0 |
|   A3_trade_saved | **PASS** | side=LONG exit=253167.0 st=closed |
|   A4_pnl_math | **PASS** | gross=5090.0 chg=254.28 |
|   A5_net | **PASS** | net=4835.72 |
|   A6_parallel_account | **PASS** | strat_realized=-12840.689999999999 sum_trades=-12840.689999999999 |
|   A7_bt_reentry_ref | **PASS** | bt_reentry=253167.0 live_reentry=True live_trigger=253025.0 delta/lot=-710.0 |
| A1_SILVERM_silver_01_rev@08-26 17:45 | **PASS** | SHORT->LONG trigger=253500.0 |
|   A1_armed(LONG) | **PASS** | exit=True reason=long_reversal trig=253500.0 stop=252521.0 |
|   A2_exit_fill_next_open | **PASS** | side=BUY px=253500.0 exp=253500.0 |
|   A3_trade_saved | **PASS** | side=SHORT exit=253500.0 st=closed |
|   A4_pnl_math | **PASS** | gross=-2375.0 chg=254.4 |
|   A5_net | **PASS** | net=-2629.4 |
|   A6_parallel_account | **PASS** | strat_realized=-12840.689999999999 sum_trades=-12840.689999999999 |
|   A7_bt_reentry_ref | **PASS** | bt_reentry=253500.0 live_reentry=True live_trigger=253500.0 delta/lot=0.0 |
| A1_SILVERM_silver_01_rev@08-28 09:00 | **PASS** | LONG->SHORT trigger=247751.0 |
|   A1_armed(SHORT) | **PASS** | exit=True reason=short_reversal trig=247751.0 stop=249610.0 |
|   A2_exit_fill_next_open | **PASS** | side=SELL px=248098.0 exp=248098.0 |
|   A3_trade_saved | **PASS** | side=LONG exit=248098.0 st=closed |
|   A4_pnl_math | **PASS** | gross=-8260.0 chg=250.56 |
|   A5_net | **PASS** | net=-8510.56 |
|   A6_parallel_account | **PASS** | strat_realized=-12840.689999999999 sum_trades=-12840.689999999999 |
|   A7_bt_reentry_ref | **PASS** | bt_reentry=250132.0 live_reentry=True live_trigger=247751.0 delta/lot=-610.0 |
| A1_SILVERM_silver_01_rev@08-28 19:30 | **PASS** | LONG->SHORT trigger=250010.0 |
|   A1_armed(SHORT) | **PASS** | exit=True reason=short_reversal trig=250010.0 stop=254450.0 |
|   A2_exit_fill_next_open | **PASS** | side=SELL px=250132.0 exp=250132.0 |
|   A3_trade_saved | **PASS** | side=LONG exit=250132.0 st=closed |
|   A4_pnl_math | **PASS** | gross=3805.0 chg=251.84 |
|   A5_net | **PASS** | net=3553.16 |
|   A6_parallel_account | **PASS** | strat_realized=-12840.689999999999 sum_trades=-12840.689999999999 |
|   A7_bt_reentry_ref | **PASS** | bt_reentry=250132.0 live_reentry=True live_trigger=250010.0 delta/lot=-610.0 |
| A1_SILVERM_silver_02_rev@08-25 11:55 | **PASS** | SHORT->LONG trigger=253333.0 |
|   A1_armed(LONG) | **PASS** | exit=True reason=long_reversal trig=253333.0 stop=252171.0 |
|   A2_exit_fill_next_open | **PASS** | side=BUY px=253001.0 exp=253001.0 |
|   A3_trade_saved | **PASS** | side=SHORT exit=253001.0 st=closed |
|   A4_pnl_math | **PASS** | gross=11980.0 chg=255.89 |
|   A5_net | **PASS** | net=11724.11 |
|   A6_parallel_account | **PASS** | strat_realized=-21042.740000000005 sum_trades=-21042.739999999998 |
|   A7_bt_reentry_ref | **PASS** | bt_reentry=253001.0 live_reentry=True live_trigger=253333.0 delta/lot=1660.0 |
| A1_SILVERM_silver_02_rev@08-26 11:55 | **PASS** | LONG->SHORT trigger=253025.0 |
|   A1_armed(SHORT) | **PASS** | exit=True reason=short_reversal trig=253025.0 stop=253673.0 |
|   A2_exit_fill_next_open | **PASS** | side=SELL px=253167.0 exp=253167.0 |
|   A3_trade_saved | **PASS** | side=LONG exit=253167.0 st=closed |
|   A4_pnl_math | **PASS** | gross=6350.0 chg=254.24 |
|   A5_net | **PASS** | net=6095.76 |
|   A6_parallel_account | **PASS** | strat_realized=-21042.740000000005 sum_trades=-21042.739999999998 |
|   A7_bt_reentry_ref | **PASS** | bt_reentry=253167.0 live_reentry=True live_trigger=253025.0 delta/lot=-710.0 |
| A1_SILVERM_silver_02_rev@08-26 17:55 | **PASS** | SHORT->LONG trigger=253500.0 |
|   A1_armed(LONG) | **PASS** | exit=True reason=long_reversal trig=253500.0 stop=252950.0 |
|   A2_exit_fill_next_open | **PASS** | side=BUY px=253500.0 exp=253500.0 |
|   A3_trade_saved | **PASS** | side=SHORT exit=253500.0 st=closed |
|   A4_pnl_math | **PASS** | gross=-1495.0 chg=254.52 |
|   A5_net | **PASS** | net=-1749.52 |
|   A6_parallel_account | **PASS** | strat_realized=-21042.740000000005 sum_trades=-21042.739999999998 |
|   A7_bt_reentry_ref | **PASS** | bt_reentry=253500.0 live_reentry=True live_trigger=253500.0 delta/lot=0.0 |
| A1_SILVERM_silver_02_rev@08-28 11:55 | **PASS** | SHORT->LONG trigger=249371.0 |
|   A1_armed(LONG) | **PASS** | exit=True reason=long_reversal trig=249371.0 stop=248776.0 |
|   A2_exit_fill_next_open | **PASS** | side=BUY px=249267.0 exp=249267.0 |
|   A3_trade_saved | **PASS** | side=SHORT exit=249267.0 st=closed |
|   A4_pnl_math | **PASS** | gross=-7060.0 chg=250.32 |
|   A5_net | **PASS** | net=-7310.32 |
|   A6_parallel_account | **PASS** | strat_realized=-21042.740000000005 sum_trades=-21042.739999999998 |
|   A7_bt_reentry_ref | **PASS** | bt_reentry=249267.0 live_reentry=True live_trigger=249371.0 delta/lot=520.0 |
| A1_SILVERM_silver_02_rev@08-28 19:30 | **PASS** | LONG->SHORT trigger=251650.0 |
|   A1_armed(SHORT) | **PASS** | exit=True reason=short_reversal trig=251650.0 stop=254450.0 |
|   A2_exit_fill_next_open | **PASS** | side=SELL px=251798.0 exp=251798.0 |
|   A3_trade_saved | **PASS** | side=LONG exit=251798.0 st=closed |
|   A4_pnl_math | **PASS** | gross=12135.0 chg=252.94 |
|   A5_net | **PASS** | net=11882.06 |
|   A6_parallel_account | **PASS** | strat_realized=-21042.740000000005 sum_trades=-21042.739999999998 |
|   A7_bt_reentry_ref | **PASS** | bt_reentry=251798.0 live_reentry=True live_trigger=251650.0 delta/lot=-740.0 |
| R_GOLDM_no_dup_fills | **PASS** | dup=0 |
| R_GOLDM_no_dup_orders | **PASS** | dup=0 |
| R_GOLDM_no_orphan_fills | **PASS** | orphan=0 |
| R_GOLDM_events | **PASS** | trade_closed=21 trades=21 |
| R_SILVERM_no_dup_fills | **PASS** | dup=0 |
| R_SILVERM_no_dup_orders | **PASS** | dup=0 |
| R_SILVERM_no_orphan_fills | **PASS** | orphan=0 |
| R_SILVERM_events | **PASS** | trade_closed=22 trades=22 |
| R_global_reversal_found | **PASS** | reversal scenarios located across slices = 21 |

**VERDICT: ALL PASSED**

A1 reversed-pending armed on the crossing bar (trigger/SL = signal-bar formula).
A2 old position exited at the NEXT fast bar's OPEN with the correct side order.
A3 closed trade persisted (side, prices, status).
A4/A5 P&L gross = (exit-entry)*mult*qty; charges = fee model; net = gross-charges.
A6 per-strategy account realized P&L equals its own closed trades (parallel isolation).
A7 backtest reference: same next-open exit; re-entry would fill at the trigger-
crossing bar's OPEN (the one documented D1 level difference, per-lot delta listed).

Input/Output detail rows -> P2_REVERSAL_E2E_INPUT_OUTPUT.csv