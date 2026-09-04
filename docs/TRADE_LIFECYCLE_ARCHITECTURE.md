# Trade Lifecycle Architecture

The canonical flow is:

`SIGNAL -> TRADE -> PENDING ORDER -> ORDER -> FILL -> POSITION -> EXIT -> P&L -> CLOSED`

A trade receives one immutable `trade_id` and every entry requires an
`entry_signal_id`. Orders, fills, positions, and events carry explicit
lineage. `position_id` is a separate identity and never replaces `trade_id`.

Stop loss closes the existing trade without requiring an exit signal. A
reversal closes the old trade with the reversal signal as `exit_signal_id`,
then creates a new trade using that same signal as `entry_signal_id`.

Duplicate fills are rejected by durable IDs. Restart reconstructs runtime state
from `trading.db`; an empty in-memory cache must never imply that a trade does
not exist. Missing identity is an error, not a latest-object lookup or a
symbol/timestamp fallback.

Trade closure persists the canonical trade, exit fill, event, and P&L before
updating dependent runtime state. Analytics and the frontend consume canonical
queries and do not reconstruct lifecycle identity independently.
