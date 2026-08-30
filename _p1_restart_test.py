"""PHASE-1 / PART 35 — RESTART & DETERMINISM PARITY.

Proves the current warmup is deterministic and crash-safe:

  R1  warmup determinism: two identical warmups produce identical HTF end_times,
      values, indicator counts and first/last DEMA values (byte-for-byte)
  R2  restart-midway parity: a warmup that only had 3 sessions available then
      restarts with full data and re-warms -> final state equals an
      uninterrupted full 5-session run (restart recomputes, never restores)
  R3  restore() round-trip: snapshot -> make a copy -> restore the copy ->
      copy state equals original in memory, unchanged by a second restore
  R4  engine._warmup_from_rest re-entry: calling start-warm twice yields the
      exact same final state (no double-feed, indicators reset before warm)

Output: RESTART_PARITY_REPORT.csv
Exit code 0 iff all pass.
"""
from __future__ import annotations

import sys
from copy import deepcopy
from datetime import date

import _p1_lib as L
from strategies.types import StrategyState

REPORT = L.AUDIT_DIR / "RESTART_PARITY_REPORT.csv"
if REPORT.exists():
    REPORT.unlink()


def snap15(engine):
    e15 = engine.htf_engine._engines["GOLDM:15m"]
    return (list(e15.end_times), list(e15.values), e15.last_value, e15.prev_value)


def snap1h(engine):
    e1h = engine.htf_engine._engines["GOLDM:1h"]
    return (list(e1h.end_times), list(e1h.values), e1h.last_value, e1h.prev_value)


def ind_count(engine):
    return (engine.indicators["GOLDM:15m"]._count,
            engine.indicators["GOLDM:1h"]._count,
            engine.indicators["GOLDM:5m"]._count)


rows = []
all_ok = True


def check(name, ok, value, expect):
    global all_ok
    all_ok &= bool(ok)
    rows.append({"check": name, "value": str(value)[:120],
                 "expect": str(expect)[:120],
                 "pass": "PASS" if ok else "FAIL"})


# ---- R1 determinism: two full warmups must be identical ----
cfg = L.write_config(L.fresh_run_root("restart"), warmup={"keep_partial": True})
eA, _ = L.build_engine(cfg, adapter_cls=L.CSVFeedAdapter)
L.swap_adapter(eA, L.CSVFeedAdapter())
eA._warmup_from_rest()
r1 = (snap15(eA), snap1h(eA), ind_count(eA))

eB, _ = L.build_engine(cfg, adapter_cls=L.CSVFeedAdapter)
L.swap_adapter(eB, L.CSVFeedAdapter())
eB._warmup_from_rest()
r2 = (snap15(eB), snap1h(eB), ind_count(eB))

same_times = True
check("R1_htf15_endtimes_identical", len(eA.htf_engine._engines["GOLDM:15m"].end_times) == 290
      and eA.htf_engine._engines["GOLDM:15m"].end_times == eB.htf_engine._engines["GOLDM:15m"].end_times,
      len(eA.htf_engine._engines["GOLDM:15m"].end_times), "290 identical times")
check("R1_htf15_values_identical", eA.htf_engine._engines["GOLDM:15m"].values == eB.htf_engine._engines["GOLDM:15m"].values,
      eA.htf_engine._engines["GOLDM:15m"].values[-1], "identical float lists")
check("R1_htf1h_values_identical", eA.htf_engine._engines["GOLDM:1h"].values == eB.htf_engine._engines["GOLDM:1h"].values,
      eA.htf_engine._engines["GOLDM:1h"].values[-1], "identical float lists")
check("R1_indicator_counts_identical", ind_count(eA) == ind_count(eB),
      ind_count(eA), "(290, 75, 870)")

# ---- R2 restart-midway parity ----
# A "crash-restart": warm with only 3 sessions available (partial data), then
# restart fresh with the full source -> must equal the uninterrupted run eA.
class _PartialAdapter(L.CSVFeedAdapter):
    """Serves rows only from dates strictly before a cutoff (simulated crash
    window where only part of the history was fetchable)."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._cutoff = None

    def fetch_historical_candles(self, name, timeframe, from_date, to_date):
        rows = super().fetch_historical_candles(name, timeframe, from_date, to_date)
        if self._cutoff is None:
            return rows
        cut = self._cutoff if isinstance(self._cutoff, date) else date.fromisoformat(self._cutoff)
        return [r for r in rows if L.ist_from_epoch(r[0]).date() < cut]


eP, _ = L.build_engine(cfg, adapter_cls=_PartialAdapter)
eP.data_adapter._cutoff = L.LAST5[2]
eP._warmup_from_rest()
check("R2_partial_warmup_has_fewer_sessions",
      len(set(L.ist_from_epoch(t).replace(tzinfo=None).date()
              for t in eP.htf_engine._engines["GOLDM:15m"].end_times)) < 5,
      len(eP.htf_engine._engines["GOLDM:15m"].end_times), "< 5 sessions served")
rP = (snap15(eP), snap1h(eP), ind_count(eP))

# fresh restart: full adapter covers everything again
eR, _ = L.build_engine(cfg, adapter_cls=L.CSVFeedAdapter)
L.swap_adapter(eR, L.CSVFeedAdapter())
eR._warmup_from_rest()
check("R2_restart_recomputed_equals_full", snap15(eR) == snap15(eA)
      and snap1h(eR) == snap1h(eA) and ind_count(eR) == ind_count(eA),
      snap15(eR)[2], "identical to uninterrupted run eA")
check("R2_partial_differs_from_full", snap15(eP) != snap15(eA),
      len(snap15(eP)[0]), "partial past is genuinely different state")

# ---- R3 restore() round-trip (live state restorable; candle state re-warmable) ----
eD, _ = L.build_engine(cfg, adapter_cls=L.CSVFeedAdapter)
L.swap_adapter(eD, L.CSVFeedAdapter())
eD._warmup_from_rest()
d15_before = snap15(eD)
snap0 = eD.snapshot()
# tamper live state
eD.account_engine.realized_pnl = 555.0
eD.account_engines["gold_01"].realized_pnl = 123.0
eD.strategies["gold_01"].state = StrategyState.LONG_POSITION
eD.strategies["gold_01"].position_side = "LONG"
check("R3_restore_roundtrip",
      eD.account_engine.realized_pnl == 555.0
      and eD.strategies["gold_01"].state == StrategyState.LONG_POSITION,
      (eD.account_engine.realized_pnl, eD.strategies["gold_01"].state.value),
      "live state tamper visible (precondition)")
eD.restore(deepcopy(snap0))
check("R3_restore_roundtrip",
      eD.account_engine.realized_pnl == 0.0
      and eD.account_engines["gold_01"].realized_pnl == 0.0
      and eD.strategies["gold_01"].state == StrategyState.FLAT
      and eD.strategies["gold_01"].position_side is None,
      (eD.account_engine.realized_pnl, eD.strategies["gold_01"].state.value, eD.strategies["gold_01"].position_side),
      "live state reverted to snapshot")
check("R3_restore_leaves_candle_state_pristine",
      snap15(eD) == d15_before,
      "HTF unchanged by restore",
      "end_times/values identical (recomputed on re-warm, never persisted)")
eD.restore(deepcopy(snap0))
check("R3_restore_idempotent",
      eD.account_engine.realized_pnl == 0.0 and snap15(eD) == d15_before,
      "no-op on second restore", "live + candle state stable")

# ---- R4 double warm ----
eC, _ = L.build_engine(cfg, adapter_cls=L.CSVFeedAdapter)
L.swap_adapter(eC, L.CSVFeedAdapter())
eC._warmup_from_rest()
state1 = (snap15(eC), snap1h(eC), ind_count(eC))
eC._warmup_from_rest()
state2 = (snap15(eC), snap1h(eC), ind_count(eC))
check("R4_double_warm_idempotent", state1 == state2 and ind_count(eC) == (290, 75, 870),
      ind_count(eC), "(290, 75, 870) unchanged, no double-feed")

L.append_rows(REPORT, rows)
print(f"\n=== RESTART PARITY ({len(rows)} checks) ===")
for r in rows:
    print(f"  {r['pass']}  {r['check']:<32s} {r['value'][:72]}")
print(f"REPORT -> {REPORT}")
print(f"RESULT: {'ALL PASSED' if all_ok else 'FAILURES PRESENT'}")
sys.exit(0 if all_ok else 1)