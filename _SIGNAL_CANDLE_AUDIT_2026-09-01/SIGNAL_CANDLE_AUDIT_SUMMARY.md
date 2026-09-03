# SIGNAL-CANDLE COMPREHENSIVE AUDIT (per-trade + per-crossing)

Window: 2026-08-26..2026-08-28   mode: OFFLINE (CSV feed)

prod_closed=24 prod_open=4

## PART A - per stored trade, open + close signal candle detail

records=28 links_ok=True open_parity_ok=True close_parity_ok=True

| strategy | side | entry | exit | reason | open_signal | open_parity | close_signal | close_parity |
|---|---|---|---|---|---|---|---|---|
| gold_01 | LONG | 2026-08-26 10:10:00 | 2026-08-26 10:20:00 | stop_loss_hit | 2026-08-26 10:00:00(LONG) | OK | 2026-08-26 10:20:00(NONE) | OK |
| gold_01 | LONG | 2026-08-26 18:00:00 | 2026-08-26 18:05:00 | stop_loss_hit | 2026-08-26 17:45:00(LONG) | OK | 2026-08-26 18:05:00(NONE) | OK |
| gold_01 | LONG | 2026-08-27 10:35:00 | 2026-08-27 11:25:00 | stop_loss_hit | 2026-08-27 10:25:00(LONG) | OK | 2026-08-27 11:25:00(NONE) | OK |
| gold_01 | LONG | 2026-08-27 21:20:00 | 2026-08-27 22:55:00 | short_reversal | 2026-08-27 21:10:00(LONG) | OK | 2026-08-27 22:55:00(SHORT) | OK |
| gold_01 | SHORT | 2026-08-27 23:10:00 | 2026-08-27 23:15:00 | stop_loss_hit | 2026-08-27 22:55:00(SHORT) | OK | 2026-08-27 23:15:00(SHORT) | OK |
| gold_01 | SHORT | 2026-08-28 03:35:00 | 2026-08-28 07:30:00 | stop_loss_hit | 2026-08-27 23:15:00(SHORT) | OK | 2026-08-28 07:30:00(NONE) | OK |
| gold_01 | LONG | 2026-08-28 13:00:00 | 2026-08-28 19:50:00 | short_reversal | 2026-08-28 12:25:00(LONG) | OK | 2026-08-28 19:50:00(NONE) | OK |
| gold_01 | SHORT | 2026-08-28 19:50:00 | OPEN | OPEN | 2026-08-28 19:40:00(SHORT) | OK | NA(NA) | NA |
| gold_02 | LONG | 2026-08-26 18:15:00 | 2026-08-26 18:15:00 | stop_loss_hit | 2026-08-26 17:45:00(LONG) | OK | 2026-08-26 18:15:00(NONE) | OK |
| gold_02 | LONG | 2026-08-27 10:45:00 | 2026-08-27 23:05:00 | short_reversal | 2026-08-27 10:15:00(LONG) | OK | 2026-08-27 23:00:00(NONE) | OK |
| gold_02 | SHORT | 2026-08-28 03:35:00 | 2026-08-28 12:35:00 | long_reversal | 2026-08-27 23:15:00(SHORT) | OK | 2026-08-28 12:30:00(NONE) | OK |
| gold_02 | LONG | 2026-08-28 13:00:00 | 2026-08-28 19:50:00 | short_reversal | 2026-08-28 12:15:00(LONG) | OK | 2026-08-28 19:45:00(NONE) | OK |
| gold_02 | SHORT | 2026-08-28 20:00:00 | OPEN | OPEN | 2026-08-28 19:30:00(SHORT) | OK | NA(NA) | NA |
| silver_01 | SHORT | 2026-08-26 11:50:00 | 2026-08-26 18:05:00 | long_reversal | 2026-08-26 11:30:00(SHORT) | OK | 2026-08-26 18:00:00(NONE) | OK |
| silver_01 | LONG | 2026-08-26 18:15:00 | 2026-08-26 18:15:00 | stop_loss_hit | 2026-08-26 17:45:00(LONG) | OK | 2026-08-26 18:15:00(NONE) | OK |
| silver_01 | LONG | 2026-08-27 10:40:00 | 2026-08-28 09:20:00 | short_reversal | 2026-08-27 10:15:00(LONG) | OK | 2026-08-28 09:15:00(NONE) | OK |
| silver_01 | LONG | 2026-08-28 12:15:00 | 2026-08-28 19:50:00 | short_reversal | 2026-08-28 11:45:00(LONG) | OK | 2026-08-28 19:45:00(NONE) | OK |
| silver_01 | SHORT | 2026-08-28 20:00:00 | OPEN | OPEN | 2026-08-28 19:30:00(SHORT) | OK | NA(NA) | NA |
| silver_02 | SHORT | 2026-08-26 11:45:00 | 2026-08-26 11:45:00 | stop_loss_hit | 2026-08-26 11:40:00(SHORT) | OK | 2026-08-26 11:45:00(NONE) | OK |
| silver_02 | SHORT | 2026-08-26 11:50:00 | 2026-08-26 18:05:00 | long_reversal | 2026-08-26 11:40:00(SHORT) | OK | 2026-08-26 18:05:00(NONE) | OK |
| silver_02 | LONG | 2026-08-26 18:05:00 | 2026-08-26 18:05:00 | stop_loss_hit | 2026-08-26 17:55:00(LONG) | OK | 2026-08-26 18:05:00(NONE) | OK |
| silver_02 | LONG | 2026-08-27 09:05:00 | 2026-08-27 09:50:00 | stop_loss_hit | 2026-08-27 09:00:00(LONG) | OK | 2026-08-27 09:50:00(NONE) | OK |
| silver_02 | LONG | 2026-08-27 10:30:00 | 2026-08-27 18:20:00 | stop_loss_hit | 2026-08-27 10:20:00(LONG) | OK | 2026-08-27 18:20:00(NONE) | OK |
| silver_02 | LONG | 2026-08-27 21:25:00 | 2026-08-28 03:35:00 | stop_loss_hit | 2026-08-27 21:10:00(LONG) | OK | 2026-08-28 03:35:00(NONE) | OK |
| silver_02 | SHORT | 2026-08-28 03:45:00 | 2026-08-28 06:50:00 | stop_loss_hit | 2026-08-28 03:30:00(SHORT) | OK | 2026-08-28 06:50:00(NONE) | OK |
| silver_02 | SHORT | 2026-08-28 09:15:00 | 2026-08-28 12:05:00 | long_reversal | 2026-08-28 09:00:00(SHORT) | OK | 2026-08-28 12:05:00(LONG) | OK |
| silver_02 | LONG | 2026-08-28 12:05:00 | 2026-08-28 19:40:00 | short_reversal | 2026-08-28 12:05:00(LONG) | OK | 2026-08-28 19:40:00(NONE) | OK |
| silver_02 | SHORT | 2026-08-28 19:40:00 | OPEN | OPEN | 2026-08-28 19:30:00(SHORT) | OK | NA(NA) | NA |

## PART B - crossing-signal-candle parity matrix

| check | result |
|---|---|
| S_GOLDM_gold_01(fast=5m)_candles | **PASS** (crossings=16 bars=715 value_mismatch=0 emission_mismatch=0) |
| S_GOLDM_gold_02(fast=15m)_candles | **PASS** (crossings=8 bars=169 value_mismatch=0 emission_mismatch=0) |
| S_SILVERM_silver_01(fast=15m)_candles | **PASS** (crossings=9 bars=169 value_mismatch=0 emission_mismatch=0) |
| S_SILVERM_silver_02(fast=5m)_candles | **PASS** (crossings=18 bars=715 value_mismatch=0 emission_mismatch=0) |

crossing candles emitted: 51; value mismatches: 0.

**RESULT: ALL MATCH**
