# FORENSIC BASELINE

- Generated: 09/05/2026 00:48:42 (local)
- Repository root: C:\Users\pc\Desktop\MCX-TRADER
- Branch: main
- HEAD: 815138c feat: complete analytics.db migration to canonical single trading.db
- Remote: https://github.com/danny8806/mcx-trader-.git
- Working tree: clean (only untracked tests/live_runtime_v2/reports/)
- OS: Windows, PowerShell 5.1
- Python: 3.14.6, Node: v24.16.0, Docker: NOT installed locally (VPS-only)
- DB: canonical data/db/trading.db (WAL, 270336 B); data/db/analytics.db STILL PRESENT (139264 B, shm 09-04 21:59, wal 0 B) - flagged for zero-legacy re-test
- Reference backtest project: C:\Users\pc\Desktop\nifty dema backtest\project (dema_mtf.py, goldm_dema_mtf_futures.py, build_15min_enriched.py; base dema_mtf_base.py at C:\Users\pc\AppData\Local\Temp\opencode\dema_mtf_base.py)
- Actual MCX data: GOLDM_5m_mcx.csv (19972 rows, 2026-03-06 14:44 -> 2026-08-26 14:00); SILVERM_5m_mcx.csv (21404 rows, 2026-02-23 09:07 -> 2026-08-21 12:30)
- Data gap: mandate replay window 2026-09-02+ NOT COVERED by actual data files - no newer CSV source found locally
- Contract files named in mandate (GOLDM_04Sep2026_5m.csv, SILVERM_30Nov2026_5m.csv) DO NOT EXIST; sanctioned substitution: data_mcx current files
- Reference contract tokens: gold 569003 (settings GOLDM202610), silver 483080 (settings SILVERM202611)
- Backtest params reference: DEMA 3 / ATR 6 / factor 1.0 / session 09:00 / gold multiplier 10 qty 1 / silver multiplier 5 qty 1
- Backtest CAPITAL in goldm_dema_mtf_futures.py = 100000 (NOT 300000); silver variant absent
- Edge: existing replay_output/replay_2026-09-02_to_latest = BLOCKED (Dhan DH-901 auth) - prior replay did NOT run
