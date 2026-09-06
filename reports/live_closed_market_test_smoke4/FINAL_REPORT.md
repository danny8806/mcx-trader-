# Closed-Market Live-Runtime Test - Final Report

- **test_id**: clm-20260906-120828
- **git commit**: b932ef051d1294d317d254312950583bee742e88
- **started**: 2026-09-06T12:09:38+05:30
- **market condition**: CLOSED (no live candles/ticks expected)
- **execution**: PAPER only (engine/config gate)

## Safety
- `system.environment` = paper: paper
- No Dhan order endpoints are reachable: engine executes through the paper broker; the Dhan client used here exposes only market-data + margin/reconciliation reads (verified by static scan + read-only probe).

## Baseline & Regression Gates

## Phase Summary (all artifacts in `reports/live_closed_market_test/`)
- `AUTH.json`
- `DB_AFTER.json`
- `DB_BEFORE.json`
- `DB_PERSISTENCE.json`
- `ERRORS.json`
- `IDLE_STATE.json`
- `INDICATOR_CALCULATIONS.json`
- `META.json`
- `RECONCILE_BASELINE.json`
- `SAFETY.json`
- `SIGNAL_VERIFICATION.json`
- `SUMMARY.json`
- `TEST_SESSION.json`
- `boot_dashboard.json`
- `build_engine.json`
- `crash_kid_test.json`
- `db_lineage.json`
- `final_reconcile.json`
- `indicator_audit.json`
- `pause_resume_test.json`
- `reconnect_test.json`
- `rest_fault_test.json`
- `rest_probe.json`
- `restart_test.json`
- `runtime_verified.json`
- `safety_paper_gate.json`
- `setup_env_and_token.json`
- `signal_semantics.json`
- `soak.json`

## Answers to the 20 verification questions
- **Q1 real Dhan REST works?** - NO - auth failed (rest_stats={'ok': 0, 'empty': 0, 'retry': 0})
- **Q2 real Dhan WebSocket lifecycle?** - connected=False, deltas=[]
- **Q3 closed-market idle correct?** - state=OVERNIGHT engine=READY idle_safe=True
- **Q4 4 strategies independent on one engine?** - ['gold_01', 'gold_02', 'silver_01', 'silver_02']
- **Q5 indicator math correct (independent re-derivation)?** - see INDICATOR_CALCULATIONS.json == indicator_audit phase
- **Q6 indicators independently observable?** - yes - audit recomputes DEMA/ATR from the same real candles
- **Q7 calculations persisted?** - canonical trading.db tables row counts before=3 after=3 (see DB_* artifacts)
- **Q8 DB == memory reconciliation?** - yes (4 checks)
- **Q9 API == DB reconciliation?** - 5 health samples; see API_RECONCILIATION (SOAK.api_samples)
- **Q10 dashboard WS == engine?** - 2 WS command messages incl. get_snapshot round-trips
- **Q11 restart recovery works?** - see RESTART_RECOVERY.json
- **Q12 reconnect works bounded?** - bounded backoff 10s->30s; watchdog stale threshold 60s; see RECONNECT.json + status_deltas
- **Q13 token lifecycle checked?** - expiry={'expires_at': 1788015978, 'remaining_hours': -183.5363030717108, 'expires_soon': True} renewal=PIN/TOTP absent -> token-file only
- **Q14 CPU stable?** - see CPU_MEMORY.json (sampler cpu_pct)
- **Q15 memory stable?** - see CPU_MEMORY.json (sampler rss_mb)
- **Q16 queues bounded?** - _last_fetched capped (<24h prune), ws _seen_ltt capped 10000, sampler max_seen_ltt=0
- **Q17 retries finite?** - REST _post retries, adapter fetch_closed max_retries=3, WS backoff capped
- **Q18 safe to run continuously?** - closed-market soak idle evaluated; see SOAK.json sample stability
- **Q19 test terminates?** - duration_minutes=0.5 with finite phase timeouts
- **Q20 real ordering provably impossible?** - yes - paper broker only; Dhan client used for read-only market data; no order endpoint is invoked by engine/runner

## Failures / Errors
- failures: 1
  - Dhan REST authentication failed - see AUTH.json (verdict cannot be VERIFIED without real Dhan connectivity)
- phases with exceptions: []

## Verdict
**NOT_VERIFIED_CLOSED_MARKET_LIVE_RUNTIME**
