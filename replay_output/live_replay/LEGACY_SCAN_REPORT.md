# LEGACY_SCAN_REPORT

Phase 62 — Repository-wide pattern scan for legacy/dead/foreign architecture references.

## Method

Text scan of the whole repo (excluding `replay_output/`, `.git`, caches, data volume,
binaries) with 9 pattern families: `dema_mtf`, foreign/fake clocks, engine injection,
candle hotswap, indicator override, pnl suppression, legacy/bronze markers, `asyncio.run`
in dual-duty spots, stress/dream harness markers. Emitted as `forensics/legacy_scan.json`
summary (nonzero families below).

## Measured results (hits by family)

| family | hits | where | classification |
|---|---|---|---|
| `dema_mtf` | 103 | `tools/` replay/comparison/parity scripts + `docs/` | Reference-oracle usage — legitimate, never production imports |
| `async_dual` | 11 | `docs/audit`, `_audit_*.py`, `_frontend_deep_check.py` (top-level scripts) | Test/audit tooling only, not core runtime |
| `bronze_meta` | 4 | `docs/migration/*`, `dashboard/routes/reconciliation.py` | Doc references to the old architecture inventory + one integrity-check *name* (`legacy_reconciliation`) |
| `candle_hotswap` | 3 | `audit_signal_candles.py`, `_p1_resample_test.py`, `tests/fresh_audit/test_full_pipeline_audit.py` | Adversarial tests that inject late candles to verify sort/update handling |
| `indicator_override` | 3 | `tools/parity_signal_harness.py`, `_BACKTEST_VS_LIVE_AUDIT` docs | Test harness forced-grid reference only |
| `engine_injection` | 2 | `docs/...` | Documentation of engine registration |
| `pnl_suppression` | 1 | `tests/adversarial_trade_lifecycle/test_pnl_and_close_correctness.py` | A test asserting non-zero PnL is preserved (adversarial) |
| `foreign_clock` | 1 | `_rebuild_prod_db.py` comment | Documentary comment about the replay clock |
| `dream_stress` | 0 | — | clear |

## Conclusion

- **Zero hits in live runtime modules** (`trading_engine.py`, `strategies/`, `execution/`,
  `htf/`, `indicators/`, `core/`, `dashboard/` non-test code, `ws/`).
- All hits are tools, docs, or adversarial tests — no legacy/foreign execution path
  exists in the production stack exercised by this replay.

## Status

**PASS** — production codebase carries no legacy/foreign architecture traces that the replay could have used.