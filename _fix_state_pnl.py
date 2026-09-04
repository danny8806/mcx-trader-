"""Fix the corrupted system_state.json on VPS."""
import paramiko, json, sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('200.234.44.93', username='root', password='Deltacapitals@123', timeout=15)

# Read the state file
stdin, stdout, stderr = ssh.exec_command(
    "docker exec mcx-trader cat /app/data/db/system_state.json", timeout=10
)
state = json.loads(stdout.read().decode())

# Show current values
acct = state.get('account', {})
print("BEFORE global realized_pnl:", acct.get('realized_pnl'))
print("BEFORE global charges:", acct.get('charges'))

for sid in ['gold_01', 'silver_02']:
    sa = state.get('accounts_by_strategy', {}).get(sid, {})
    pnl = state.get('pnl', {}).get(sid, {})
    print("BEFORE", sid, "realized_pnl:", sa.get('realized_pnl'), "charges:", sa.get('charges'))
    print("BEFORE", sid, "realized_net:", pnl.get('realized_net'), "trade_count:", pnl.get('trade_count'))

# Fix global account
acct['realized_pnl'] = 62036.14
acct['charges'] = 543.86
# Recalc cash from starting_capital + realized_pnl
acct['cash'] = acct.get('starting_capital', 1200000) + acct['realized_pnl']
# Recalc equity
acct['equity'] = acct['cash'] + acct.get('unrealized_pnl', 0)
acct['available_margin'] = acct['equity'] - acct.get('used_margin', 0)
acct['net_pnl'] = acct['realized_pnl'] + acct.get('unrealized_pnl', 0)

# Fix per-strategy accounts
for sid in ['gold_01', 'silver_02']:
    sa = state.get('accounts_by_strategy', {}).get(sid, {})
    if sid == 'gold_01':
        sa['realized_pnl'] = 30611.11
        sa['charges'] = 298.89
    elif sid == 'silver_02':
        sa['realized_pnl'] = 31425.03
        sa['charges'] = 244.97
    sa['cash'] = sa.get('starting_capital', 300000) + sa['realized_pnl']
    sa['equity'] = sa['cash'] + sa.get('unrealized_pnl', 0)
    sa['available_margin'] = sa['equity'] - sa.get('used_margin', 0)
    sa['net_pnl'] = sa['realized_pnl'] + sa.get('unrealized_pnl', 0)

# Fix PNL engines
for sid in ['gold_01', 'silver_02']:
    pnl = state.get('pnl', {}).get(sid, {})
    if sid == 'gold_01':
        pnl['realized_net'] = 30611.11
        pnl['realized_gross'] = 30910.0
        pnl['realized_charges'] = 298.89
        pnl['trade_count'] = 1
    elif sid == 'silver_02':
        pnl['realized_net'] = 31425.03
        pnl['realized_gross'] = 31670.0
        pnl['realized_charges'] = 244.97
        pnl['trade_count'] = 1

# Show fixed values
acct = state.get('account', {})
print("\nAFTER global realized_pnl:", acct.get('realized_pnl'))
for sid in ['gold_01', 'silver_02']:
    sa = state.get('accounts_by_strategy', {}).get(sid, {})
    pnl = state.get('pnl', {}).get(sid, {})
    print("AFTER", sid, "realized_pnl:", sa.get('realized_pnl'), "trade_count:", pnl.get('trade_count'))

# Write back
state_json = json.dumps(state, indent=2, default=str)
# Write to temp file in container, then move
sftp = ssh.open_sftp()
local_tmp = r'C:\Users\pc\AppData\Local\Temp\opencode\state_fixed.json'
with open(local_tmp, 'w') as f:
    f.write(state_json)
sftp.put(local_tmp, '/home/jadhavdnyaneshwar701/mcx-trader-data/data/db/system_state_fixed.json')
sftp.close()

# Copy into container and replace
stdin, stdout, stderr = ssh.exec_command(
    "docker cp /home/jadhavdnyaneshwar701/mcx-trader-data/data/db/system_state_fixed.json mcx-trader:/app/data/db/system_state.json", timeout=10
)
print("\nCopy result:", stdout.read().decode(), stderr.read().decode())

# Verify
stdin, stdout, stderr = ssh.exec_command(
    "docker exec mcx-trader python3 -c \"import json; d=json.load(open('/app/data/db/system_state.json')); print('Verified global realized_pnl:', d['account']['realized_pnl'])\"", timeout=10
)
print(stdout.read().decode())

# Restart container
print("\nRestarting container...")
stdin, stdout, stderr = ssh.exec_command("docker restart mcx-trader", timeout=60)
print(stdout.read().decode())

import time
time.sleep(15)

# Check logs
stdin, stdout, stderr = ssh.exec_command(
    "docker logs mcx-trader 2>&1 | grep -iE 'heal|reconcil|safe|error|CRITICAL|mismatch' | tail -20", timeout=15
)
print("\nStartup logs:")
print(stdout.read().decode())

ssh.close()
print("Done!")
