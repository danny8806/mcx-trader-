{
  "verdict": "NOT_VERIFIED_LIVE_MARKET_READINESS",
  "test_id": "lmr-20260906-173355",
  "defect_confirmed": true,
  "failures": [
    "dashboard/API/WS reconciliation failed"
  ],
  "errors": [
    {
      "phase": "dashboard",
      "type": "TypeError",
      "detail": "argument of type 'bool' is not a container or iterable",
      "trace": "Traceback (most recent call last):\n  File \"C:\\Users\\pc\\Desktop\\MCX-TRADER\\tools\\live_market_readiness.py\", line 187, in phase\n    result = fn()\n  File \"C:\\Users\\pc\\Desktop\\MCX-TRADER\\tools\\live_market_readiness.py\", line 1245, in phase_dashboard\n    if \"count\" in v else v) for k, v in rows.items()},\n       ^^^^^^^^^^^^\nTypeError: argument of type 'bool' is not a container or iterable\n"
    }
  ],
  "required": "exact defect, reproduction, source file, root cause, minimum fix, tests required \u2014 see artifacts in this report dir",
  "artifacts": [
    "DB_FORENSICS.json",
    "ENV.json",
    "FINAL_REPORT.json",
    "META.json",
    "REAL_LIVE_BOOT.json",
    "REAL_SOURCE.json",
    "RESTART.json",
    "SAFETY.json",
    "SIGNAL_EVIDENCE.json",
    "dashboard.json",
    "independence.json",
    "lineage.json",
    "real_fetch.json",
    "rehearsal.json",
    "safety_paper_gate.json",
    "setup_env_and_token.json"
  ]
}
