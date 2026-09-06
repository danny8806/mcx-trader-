# CPU / Memory — CPU_MEMORY.md

## Closed-market steady state (runtime_v3 soak sampler, 17 samples / ~3.5 min)

| metric | value |
|---|---|
| threads | 13 → 18 (idle pool, stable) |
| rss_mb (last sample) | 152.04 |
| cpu_pct (last sample) | 0.0 (idle — closed market, no feed traffic) |
| ws_connected | true throughout |
| engine_status | READY throughout |
| ws parse_err | 0 |
| REST retries | ≤4 total across run (429 backoff, bounded) |
| bus counters | candle feeds 0 (closed); tick snapshot counters stable |

## Behavior notes
- No sustained CPU/RAM growth; controller resisted burning CPU during the closed session — engine idles correctly (waits on async WS + timers).
- Memory footprint ~152 MB RSS for the full stack (engine + ws + REST + dashboard + DB) at closed-market idle.
- The earlier 30-min soak run (v2, pre-fix 1-min rule era) exhibited the same idle profile (threads ~13, no leaks observed).

## Conclusion
Runtime is idle-safe in closed markets; no leak signal within the observed session.