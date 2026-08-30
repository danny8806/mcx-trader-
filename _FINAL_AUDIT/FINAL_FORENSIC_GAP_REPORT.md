# FINAL_FORENSIC_GAP_REPORT — post-fix residual gaps (work from earlier forensic rounds)

Generated 2026-08-30 09:34 UTC. This is the final consolidated gap statement for the current tree.

## Closed defects (all 12 PASS-1 fixes VERIFIED in PASS-2 B-leg and re-tested green)
1-3. Fill-dedup crash window (get_fill guard + note_processed + durable mark order).
4-5. Naive UTC now() in warmup + Dhan reconcile (IST-aware).
6. No dashboard auth when desired (env gate added; open default documented).
7-9. API contract drift (entry_price, starting_capital, flattened indicators, events shape).
10-11. Hardcoded client id + wrong token path in scripts.
12. dead analytics/reconciliation.py (removed) + data/db/ gitignore coverage.

## Residual items (documented, not blocking; owner: operator/deploy)
- R1 Token renewal is user-side: on-disk data/dhan_token.json expired; must be refreshed
  before any live re-run. The audit never needed live market data (synthetic tape + real
  engine paths).
- R2 Deployment to VM 34.93.47.220 and GCP firewall TCP:8000 were NOT performed
  (out of scope: audit only; no Docker artifacts produced). See DOCKER_REQUIREMENTS.md.
- R3 `kill_switch_enabled=false`, max_daily_loss=999999999, max_drawdown_pct=100 used
  deliberately so paper/live pass-through never halts on a scaled loss; set real caps
  before live funding.
- R4 Deep-check harness carries a small reliability patch (catch asyncio.TimeoutError
  during ws.recv polling); assertions unchanged — pre-existing flake, not a product bug.
- R5 DEMAATR snapshot raw keys and flattened aliases both served (UI contract settled).
- R6 win_rate unit dichotomy is documented and deep-verified (Pnl fraction*100), not a bug.

## Open questions (auto-generated row set)
See FINAL_SYSTEM_QUESTION_MATRIX.csv (12 answered rows; no UNKNOWN status remained).
