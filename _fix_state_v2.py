"""Fix system_state.json by writing directly to the volume-mounted host path."""
import paramiko, json, sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('200.234.44.93', username='root', password='Deltacapitals@123', timeout=15)

# Read state from the HOST volume path (same as what container sees)
sftp = ssh.open_sftp()
with sftp.open('/home/jadhavdnyaneshwar701/mcx-trader-data/data/db/system_state.json', 'r') as f:
    state = json.load(f)
sftp.close()

# Show current corrupted values
acct = state.get('account', {})
print("BEFORE global realized_pnl:", acct.get('realized_pnl'))

for sid in ['gold_01', 'silver_02']:
    pnl = state.get('pnl', {}).get(sid, {})
    print("BEFORE", sid, "realized_net:", pnl.get('realized_net'), "trade_count:", pnl.get('trade_count'))

# Fix global account
acct['realized_pnl'] = 62036.14
acct['charges'] = 543.86
acct['cash'] = acct.get('starting_capital', 1200000) + acct['realized_pnl']
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

# Write directly to the HOST volume path
state_json = json.dumps(state, indent=2, default=str)
sftp = ssh.open_sftp()
with sftp.open('/home/jadhavdnyaneshwar701/mcx-trader-data/data/db/system_state.json', 'w') as f:
    f.write(state_json)
sftp.close()
print("\nState file written to host volume path!")

# Verify from container
stdin, stdout, stderr = ssh.exec_command(
    "docker exec mcx-trader python3 -c 'import json; d=json.load(open(\"/app/data/db/system_state.json\")); print(\"Verified:\", d[\"account\"][\"realized_pnl\"], d[\"pnl\"][\"gold_01\"][\"realized_net\"], d[\"pnl\"][\"silver_02\"][\"realized_net\"])'",
    timeout=10
)
print(stdout.read().decode())
err = stderr.read().decode()
if err.strip():
    print("STDERR:", err)

# Restart container
print("Restarting container...")
stdin, stdout, stderr = ssh.exec_command("docker restart mcx-trader", timeout=60)
print(stdout.read().decode())

import time
time.sleep(15)

# Check logs
stdin, stdout, stderr = ssh.exec_command(
    "docker logs mcx-trader 2>&1 | grep -iE 'heal|reconcil|safe|error|CRITICAL|mismatch' | tail -25", timeout=15
)
print("\nStartup logs:")
print(stdout.read().decode())

ssh.close()
print("Done!")
