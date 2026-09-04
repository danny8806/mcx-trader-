import paramiko, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('200.234.44.93', username='root', password='Deltacapitals@123', timeout=10)

commands = [
    ("Startup logs (1000 lines)", "docker logs mcx-trader --tail 1000 2>&1 | head -200"),
    ("Find state file", "find /home/jadhavdnyaneshwar701/mcx-trader-data -name 'state.json' -o -name 'state*.json' 2>/dev/null"),
    ("Find state file in container", "docker exec mcx-trader find /app -name 'state.json' -o -name 'state*.json' 2>/dev/null"),
    ("DB file location", "docker exec mcx-trader find /app -name 'trading.db' 2>/dev/null"),
    ("DB all trades", "docker exec mcx-trader python3 -c \"import sqlite3,json; conn=sqlite3.connect('/app/data/db/trading.db'); conn.row_factory=sqlite3.Row; rows=conn.execute('SELECT trade_id,strategy_id,instrument,side,status,entry_price,exit_price,exit_reason,net_pnl FROM trades ORDER BY rowid DESC').fetchall(); print(json.dumps([dict(r) for r in rows], indent=2))\""),
    ("Open positions from API", "curl -s http://localhost:8000/api/positions 2>/dev/null | python3 -m json.tool"),
    ("Safe mode state", "docker exec mcx-trader python3 -c \"import sqlite3,json; conn=sqlite3.connect('/app/data/db/trading.db'); conn.row_factory=sqlite3.Row; rows=conn.execute('SELECT * FROM safe_mode ORDER BY rowid DESC LIMIT 5').fetchall(); print(json.dumps([dict(r) for r in rows], indent=2))\" 2>/dev/null || echo 'no safe_mode table'"),
]

for label, cmd in commands:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=15)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    if out.strip():
        print(out[:3000])
    if err.strip():
        print(f"STDERR: {err[:1000]}")

ssh.close()
