import paramiko, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('200.234.44.93', username='root', password='Deltacapitals@123', timeout=10)

commands = [
    ("Check system_state.json in container", "docker exec mcx-trader python3 -c \"import json; d=json.load(open('/app/data/db/system_state.json')); pos=d.get('positions',{}); print('OPEN:', json.dumps(pos.get('open_positions',{}), indent=2)); print('CLOSED:', json.dumps(pos.get('closed_positions',[]), indent=2)[:2000]); print('ACCOUNT:', json.dumps(d.get('account',{}), indent=2)); print('ACCTS_BY_STRAT:', json.dumps(d.get('accounts_by_strategy',{}), indent=2)); print('PNL:', json.dumps(d.get('pnl',{}), indent=2)); print('STRATEGIES:', json.dumps({k:v for k,v in d.get('strategies',{}).items()}, indent=2)[:2000])\""),
    ("Check if system_state.json exists on VPS", "ls -la /home/jadhavdnyaneshwar701/mcx-trader-data/data/db/system_state.json 2>/dev/null || echo 'NOT FOUND'"),
    ("Docker full startup logs", "docker logs mcx-trader 2>&1 | head -100"),
    ("Docker stderr logs", "docker logs mcx-trader --tail 500 2>&1 | grep -iE 'error|heal|reconcil|safe|position|restore|warn|fail|WARNING|CRITICAL' | tail -40"),
]

for label, cmd in commands:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=15)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    if out.strip():
        print(out[:4000])
    if err.strip():
        print(f"STDERR: {err[:2000]}")

ssh.close()
