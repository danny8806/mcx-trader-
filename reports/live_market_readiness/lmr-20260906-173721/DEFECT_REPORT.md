{
  "verdict": "NOT_VERIFIED_LIVE_MARKET_READINESS",
  "test_id": "lmr-20260906-173721",
  "defect_confirmed": true,
  "failures": [
    "real source acquisition failed"
  ],
  "errors": [
    {
      "phase": "real_fetch",
      "type": "DhanAuthError",
      "detail": "/charts/intraday: {\"errorType\":\"Order_Error\",\"errorCode\":\"DH-906\",\"errorMessage\":\"Invalid Token\"}",
      "trace": "Traceback (most recent call last):\n  File \"C:\\Users\\pc\\Desktop\\MCX-TRADER\\tools\\live_market_readiness.py\", line 187, in phase\n    result = fn()\n  File \"C:\\Users\\pc\\Desktop\\MCX-TRADER\\tools\\live_market_readiness.py\", line 312, in real_fetch\n    bars = probe.fetch_historical_candles(name, tf_id, base_from, to_date)\n  File \"C:\\Users\\pc\\Desktop\\MCX-TRADER\\data\\dhan\\adapter.py\", line 223, in fetch_historical_candles\n    return self.rest.fetch_intraday(\n           ~~~~~~~~~~~~~~~~~~~~~~~~^\n        meta.security_id, timeframe, from_dt, to_dt,\n        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n        meta.exchange_segment, meta.instrument,\n        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n    )\n    ^\n  File \"C:\\Users\\pc\\Desktop\\MCX-TRADER\\data\\dhan\\rest_client.py\", line 434, in fetch_intraday\n    j = self._post(\"/charts/intraday\", {\n        \"securityId\": str(security_id),\n    ...<5 lines>...\n        \"toDate\": to_dt.strftime(\"%Y-%m-%d %H:%M:%S\"),\n    })\n  File \"C:\\Users\\pc\\Desktop\\MCX-TRADER\\data\\dhan\\rest_client.py\", line 378, in _post\n    raise DhanAuthError(f\"{path}: {r.text[:200]}\")\ndata.dhan.rest_client.DhanAuthError: /charts/intraday: {\"errorType\":\"Order_Error\",\"errorCode\":\"DH-906\",\"errorMessage\":\"Invalid Token\"}\n"
    }
  ],
  "required": "exact defect, reproduction, source file, root cause, minimum fix, tests required \u2014 see artifacts in this report dir",
  "artifacts": [
    "ENV.json",
    "FINAL_REPORT.json",
    "META.json",
    "SAFETY.json",
    "real_fetch.json",
    "safety_paper_gate.json",
    "setup_env_and_token.json"
  ]
}
