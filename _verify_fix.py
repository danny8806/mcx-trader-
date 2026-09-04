"""Verify engine is running properly after fix."""
import paramiko, json, sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('200.234.44.93', username='root', password='Deltacapitals@123', timeout=15)

def run(cmd, timeout=15):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace')
    if out.strip(): print(out[:3000])
    return out

print("=" * 60)
print("Engine State (via API)")
print("=" * 60)
run("curl -s http://localhost:8000/api/health/system 2>/dev/null | python3 -m json.tool")

print("\n" + "=" * 60)
print("Positions")
print("=" * 60)
run("curl -s http://localhost:8000/api/positions 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); print(f'Open: {d.get(\\\"count\\\",0)}'); [print(f'  {p[\\\"strategy_id\\\"]}: {p[\\\"side\\\"]} @ {p[\\\"average_entry\\\"]} status={p[\\\"status\\\"]}') for p in d.get('positions',[])]\"")

print("\n" + "=" * 60)
print("PNL")
print("=" * 60)
run("curl -s http://localhost:8000/api/pnl 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); [print(f'{k}: realized_net={v.get(\\\"realized_net\\\",{}).get(\\\"value\\\",0)}, trade_count={v.get(\\\"trade_count\\\",{}).get(\\\"value\\\",0)}') for k,v in d.items() if isinstance(v,dict)]\"")

print("\n" + "=" * 60)
print("Last 20 startup logs")
print("=" * 60)
run("docker logs mcx-trader 2>&1 | grep -iE 'heal|reconcil|safe|ready|started|signal|position|error|CRITICAL' | tail -20")

ssh.close()
