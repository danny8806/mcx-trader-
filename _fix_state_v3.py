"""Fix state file by: stop container -> fix state file -> start container."""
import paramiko, json, sys, time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('200.234.44.93', username='root', password='Deltacapitals@123', timeout=15)

def run(cmd, timeout=15):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    if out.strip(): print(out[:2000])
    if err.strip(): print(f"STDERR: {err[:500]}")
    return out

# Step 1: STOP container (don't restart - shutdown handler saves corrupted state)
print("=" * 60)
print("STEP 1: STOP container (prevents shutdown handler from overwriting fix)")
print("=" * 60)
run("docker stop mcx-trader", timeout=30)
time.sleep(3)

# Step 2: Fix the state file on the host volume
print("\n" + "=" * 60)
print("STEP 2: Fix state file on host volume")
print("=" * 60)
sftp = ssh.open_sftp()
host_path = '/home/jadhavdnyaneshwar701/mcx-trader-data/data/db/system_state.json'
with sftp.open(host_path, 'r') as f:
    state = json.load(f)

acct = state.get('account', {})
print("BEFORE realized_pnl:", acct.get('realized_pnl'))

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
        pnl['wins'] = 1
        pnl['losses'] = 0
    elif sid == 'silver_02':
        pnl['realized_net'] = 31425.03
        pnl['realized_gross'] = 31670.0
        pnl['realized_charges'] = 244.97
        pnl['trade_count'] = 1
        pnl['wins'] = 1
        pnl['losses'] = 0

# Write fixed state
with sftp.open(host_path, 'w') as f:
    json.dump(state, f, indent=2, default=str)
sftp.close()
print("AFTER realized_pnl:", state['account']['realized_pnl'])
print("State file fixed!")

# Step 3: Start container
print("\n" + "=" * 60)
print("STEP 3: START container (loads fixed state)")
print("=" * 60)
run("docker start mcx-trader", timeout=30)

print("Waiting 15s for startup...")
time.sleep(15)

# Step 4: Check logs
print("\n" + "=" * 60)
print("STEP 4: Check Heal + Reconciliation")
print("=" * 60)
run("docker logs mcx-trader 2>&1 | grep -iE 'heal|reconcil|safe|error|CRITICAL|mismatch' | tail -25", timeout=15)

ssh.close()
print("\nDone!")
