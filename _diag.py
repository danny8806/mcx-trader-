import paramiko, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('200.234.44.93', username='root', password='Deltacapitals@123', timeout=10)

commands = [
    ("Docker logs (last 100)", "docker logs mcx-trader --tail 100 2>&1"),
    ("State file positions", "docker exec mcx-trader python3 -c \"import json; d=json.load(open('/app/data/db/state.json')); print('OPEN POSITIONS:', json.dumps(d.get('positions',{}).get('open_positions',{}), indent=2)); print('ACCOUNTS:', json.dumps(d.get('accounts_by_strategy',{}), indent=2)); print('GLOBAL ACCOUNT:', json.dumps(d.get('account',{}), indent=2))\""),
    ("DB trades", "docker exec mcx-trader python3 -c \"import sqlite3,json; conn=sqlite3.connect('/app/data/db/trading.db'); conn.row_factory=sqlite3.Row; rows=conn.execute('SELECT trade_id,strategy_id,status,exit_reason,exit_price,net_pnl FROM trades ORDER BY rowid DESC LIMIT 20').fetchall(); print(json.dumps([dict(r) for r in rows], indent=2))\""),
]

for label, cmd in commands:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=15)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    if out.strip():
        print(out)
    if err.strip():
        print(f"STDERR: {err}")

ssh.close()
