{
  "verdict": "NOT_VERIFIED_LIVE_MARKET_READINESS",
  "test_id": "lmr-20260906-170137",
  "defect_confirmed": true,
  "failures": [
    "real live boot: Dhan WS not connected (no live ticks)",
    "real Dhan WS not connected",
    "reversal exit_signal_id == next trade entry_signal_id in real data (SIG-NEW shared)",
    "lineage/DB gates failed",
    "dashboard API + WS serve the same canonical data (positions/trades/fills reconcile; WS snapshot lists all 4)",
    "dashboard/API/WS reconciliation failed"
  ],
  "errors": [],
  "required": "exact defect, reproduction, source file, root cause, minimum fix, tests required \u2014 see artifacts in this report dir",
  "artifacts": [
    "DASHBOARD.json",
    "DB_FORENSICS.json",
    "ENV.json",
    "FINAL_REPORT.json",
    "META.json",
    "REAL_LIVE_BOOT.json",
    "REAL_SOURCE.json",
    "RESTART.json",
    "SAFETY.json",
    "SIGNAL_EVIDENCE.json",
    "independence.json",
    "lineage.json",
    "real_fetch.json",
    "rehearsal.json",
    "safety_paper_gate.json",
    "setup_env_and_token.json"
  ]
}
