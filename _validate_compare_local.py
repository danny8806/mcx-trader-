"""Pure unit test of compare_replay_trades.run_comparison matching logic.

Uses hand-fabricated closed/open trade dicts to prove every comparison branch:
identical->match, and each corruption (net_pnl, exit_price, qty, exit_reason,
entry mismatch, extra prod trade, extra ref trade, open side/qty/avg_entry/ts)
is individually detected.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import compare_replay_trades as C

IC = {'GOLDM': {'security_id': '569003'}, 'SILVERM': {'security_id': '483080'}}


def mk_closed(sid='gold_01', side='LONG', ets='2026-08-28 11:30:00', ep=162000.0,
              xts='2026-08-28 13:00:00', xp=162500.0, qty=1, net=500.0, reason='exit_signal'):
    return {
        'strategy_id': sid, 'side': side, 'entry_timestamp': ets, 'entry_price': ep,
        'exit_timestamp': xts, 'exit_price': xp, 'quantity': qty, 'net_pnl': net,
        'exit_reason': reason,
    }


def mk_open(sid='gold_01', side='SHORT', qty=1, avg=162000.0, ets='2026-08-28 11:30:00'):
    return {
        'strategy_id': sid, 'side': side, 'quantity': qty, 'average_entry': avg,
        'entry_timestamp': ets,
    }


def main() -> int:
    passed = 0
    def check(name, results, expect):
        nonlocal passed
        mis = sum(1 for _, ok, _ in results if not ok)
        status = "PASS" if mis == expect else "FAIL"
        if status == "PASS":
            passed += 1
        print(f"  [{status}] {name}: checks={len(results)} mismatches={mis} (expected {expect})")

    # 1) identical single closed + open -> match
    c = mk_closed(); o = mk_open()
    r, m = C.run_comparison([c], [o], [c], [o], "x", "y")
    check("identical 1 closed + 1 open", r, 0)

    # 2) two closed trades same strategy, different entry -> each matched
    c1 = mk_closed(ets='2026-08-27 09:05:00'); c2 = mk_closed(ets='2026-08-28 11:30:00')
    r, m = C.run_comparison([c1, c2], [], [c1, c2], [], "x", "y")
    check("two closed distinct entries", r, 0)

    # 3) corrupted net_pnl
    c_bad = copy.deepcopy(c); c_bad['net_pnl'] = 99999.0
    r, m = C.run_comparison([c_bad], [o], [c], [o], "x", "y")
    check("net_pnl corruption", r, 1)

    # 4) corrupted exit_price
    c_bad = copy.deepcopy(c); c_bad['exit_price'] = 1.0
    r, m = C.run_comparison([c_bad], [o], [c], [o], "x", "y")
    check("exit_price corruption", r, 1)

    # 5) corrupted exit_reason
    c_bad = copy.deepcopy(c); c_bad['exit_reason'] = 'stop_loss'
    r, m = C.run_comparison([c_bad], [o], [c], [o], "x", "y")
    check("exit_reason corruption", r, 1)

    # 6) corrupted qty
    c_bad = copy.deepcopy(c); c_bad['quantity'] = 2
    r, m = C.run_comparison([c_bad], [o], [c], [o], "x", "y")
    check("qty corruption", r, 1)

    # 7) corrupted entry_price
    c_bad = copy.deepcopy(c); c_bad['entry_price'] = 1.0
    r, m = C.run_comparison([c_bad], [o], [c], [o], "x", "y")
    check("entry_price corruption", r, 1)

    # 8) extra prod trade with no ref (different entry) -> prod-only flagged
    c_extra = mk_closed(ets='2026-08-26 14:00:00')
    r, m = C.run_comparison([c, c_extra], [o], [c], [o], "x", "y")
    check("extra prod trade (ref-only detection)", r, 1)

    # 9) extra ref trade not in prod -> ref-only flagged
    c_extra = mk_closed(ets='2026-08-26 14:00:00')
    r, m = C.run_comparison([c], [o], [c, c_extra], [o], "x", "y")
    check("extra ref trade (prod-only detection)", r, 1)

    # 10) different side (SHORT vs LONG) -> different key -> both flagged
    c_short = mk_closed(side='SHORT')
    r, m = C.run_comparison([c], [o], [c_short], [o], "x", "y")
    check("side mismatch (key difference)", r, 2)

    # 11) open qty corruption
    o_bad = copy.deepcopy(o); o_bad['quantity'] = 3
    r, m = C.run_comparison([c], [o_bad], [c], [o], "x", "y")
    check("open qty corruption", r, 1)

    # 12) open avg_entry corruption
    o_bad = copy.deepcopy(o); o_bad['average_entry'] = 1.0
    r, m = C.run_comparison([c], [o_bad], [c], [o], "x", "y")
    check("open avg_entry corruption", r, 1)

    # 13) open entry_ts corruption
    o_bad = copy.deepcopy(o); o_bad['entry_timestamp'] = '2026-08-27 09:05:00'
    r, m = C.run_comparison([c], [o_bad], [c], [o], "x", "y")
    check("open entry_ts corruption", r, 1)

    # 14) extra open position in prod only
    o_extra = mk_open(sid='silver_02', side='LONG')
    r, m = C.run_comparison([c], [o, o_extra], [c], [o], "x", "y")
    check("extra prod open (ref-only)", r, 2)

    # 15) multiple open positions same side/count mismatch
    o2 = mk_open(sid='gold_01', side='SHORT', ets='2026-08-27 09:05:00')
    r, m = C.run_comparison([c], [o, o2], [c], [o], "x", "y")
    check("open count mismatch", r, 2)

    print(f"\nVALIDATION RESULT: {passed}/15 cases passed")
    return 0 if passed == 15 else 1


if __name__ == "__main__":
    sys.exit(main())
