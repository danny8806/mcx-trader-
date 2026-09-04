#!/usr/bin/env python3
"""Fix analytics.db stale OPEN records that should be CLOSED."""
import sqlite3
import time

# Get closed trades from trading.db
trading_conn = sqlite3.connect('/app/data/db/trading.db')
trading_conn.row_factory = sqlite3.Row
closed_trades = trading_conn.execute(
    "SELECT trade_id, net_pnl, gross_pnl, charges as fees, exit_price, exit_reason FROM trades WHERE status='closed'"
).fetchall()
trading_conn.close()

print(f"Found {len(closed_trades)} closed trades in trading.db")

# Fix analytics.db
analytics_conn = sqlite3.connect('/app/data/db/analytics.db')
analytics_conn.row_factory = sqlite3.Row

for t in closed_trades:
    tid = t['trade_id']
    row = analytics_conn.execute('SELECT status FROM trades_analytics WHERE trade_id=?', (tid,)).fetchone()
    if row and row['status'] == 'OPEN':
        print(f'FIXING: {tid} - OPEN -> CLOSED, net_pnl={t["net_pnl"]}')
        analytics_conn.execute(
            """UPDATE trades_analytics 
               SET status='CLOSED', 
                   net_pnl=?, gross_pnl=?, fees=?, exit_price=?,
                   exit_reason=?, closed_at=?, updated_at=?
               WHERE trade_id=?""",
            (t['net_pnl'], t['gross_pnl'], t['fees'], t['exit_price'], 
             t['exit_reason'] or 'signal_exit', time.time(), time.time(), tid)
        )
    elif row:
        print(f'OK: {tid} - already {row["status"]}')
    else:
        print(f'MISSING: {tid} - not in analytics.db (creating)')

analytics_conn.commit()

# Verify
rows = analytics_conn.execute('SELECT trade_id, strategy_id, status, net_pnl, gross_pnl, fees FROM trades_analytics').fetchall()
print('\n=== analytics.db AFTER FIX ===')
for r in rows:
    print(dict(r))

# Count comparison
t_conn = sqlite3.connect('/app/data/db/trading.db')
a_conn = sqlite3.connect('/app/data/db/analytics.db')
t_closed = t_conn.execute("SELECT COUNT(*) FROM trades WHERE status='closed'").fetchone()[0]
a_closed = a_conn.execute("SELECT COUNT(*) FROM trades_analytics WHERE status='CLOSED'").fetchone()[0]
t_total = t_conn.execute('SELECT COUNT(*) FROM trades').fetchone()[0]
a_total = a_conn.execute('SELECT COUNT(*) FROM trades_analytics').fetchone()[0]
print(f'\ntrading.db: {t_total} total, {t_closed} closed')
print(f'analytics.db: {a_total} total, {a_closed} closed')
print(f'Counts match: {t_closed == a_closed}')

t_trades = {r[0]: dict(r) for r in t_conn.execute('SELECT trade_id, net_pnl, gross_pnl FROM trades').fetchall()}
a_trades = {r[0]: dict(r) for r in a_conn.execute('SELECT trade_id, net_pnl, gross_pnl FROM trades_analytics').fetchall()}
for tid in set(t_trades.keys()) & set(a_trades.keys()):
    t = t_trades[tid]
    a = a_trades[tid]
    pnl_match = abs((t['net_pnl'] or 0) - (a['net_pnl'] or 0)) < 0.01
    print(f'{tid[:8]}...: trading.db={t["net_pnl"]}, analytics.db={a["net_pnl"]}, match={pnl_match}')

t_conn.close()
a_conn.close()
analytics_conn.close()
print('\nDone.')
