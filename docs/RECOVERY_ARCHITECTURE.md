# Recovery Architecture

Startup recovery opens the canonical `trading.db`, enables foreign keys,
runs SQLite integrity and foreign-key checks, validates required lifecycle
lineage, and only then reconstructs the in-memory lifecycle cache.

`TradeRecoveryManager` is the recovery boundary. A failed report is a safe-mode
condition; strategies must not start on an invalid or ambiguous database.
Runtime caches are disposable. Recovery reads explicit `trade_id` links from
canonical rows and never derives identity from the latest position, order,
fill, symbol, or timestamp.