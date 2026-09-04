import paramiko, sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('200.234.44.93', username='root', password='Deltacapitals@123', timeout=10)

print("=== Full reconciliation report ===")
stdin, stdout, stderr = ssh.exec_command("docker logs mcx-trader 2>&1 | grep -A 30 'Reconciliation Report'", timeout=15)
print(stdout.read().decode())

print("\n=== Heal logs ===")
stdin, stdout, stderr = ssh.exec_command("docker logs mcx-trader 2>&1 | grep -iE '\\[Heal\\]|heal|closed.*DB|healed'", timeout=15)
print(stdout.read().decode())

print("\n=== Positions API ===")
stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:8000/api/positions 2>/dev/null", timeout=10)
raw = stdout.read().decode()
try:
    d = json.loads(raw)
    for p in d.get('positions', []):
        print(f"  {p['strategy_id']}: {p['instrument']} {p['side']} @ {p['average_entry']} margin={p['margin']} open={p['is_open']}")
except:
    print(raw[:2000])

print("\n=== Strategies ===")
stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:8000/api/strategies 2>/dev/null", timeout=10)
raw = stdout.read().decode()
try:
    d = json.loads(raw)
    for s in d.get('strategies', []):
        print(f"  {s['strategy_id']}: state={s['state']} position_side={s.get('position_side')}")
except:
    print(raw[:2000])

ssh.close()
