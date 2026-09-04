"""Deploy heal P&L fix to VPS + fix corrupted state file."""
import paramiko
import json
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

VPS_HOST = '200.234.44.93'
VPS_USER = 'root'
VPS_PASS = 'Deltacapitals@123'
VPS_BASE = '/home/jadhavdnyaneshwar701/mcx-trader-data'
LOCAL_MCX = r'C:\Users\pc\Desktop\MCX-TRADER'

SYNC_FILES = [
    ('trading_engine.py', 'trading_engine.py'),
    ('portfolio/position_manager.py', 'portfolio/position_manager.py'),
]

def run_ssh(ssh, cmd, timeout=30):
    print(f"\n>>> {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    if out.strip():
        print(out)
    if err.strip():
        print(f"STDERR: {err}")
    return out, err

def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(VPS_HOST, username=VPS_USER, password=VPS_PASS, timeout=15)
    print("Connected!")

    # Step 1: Copy files to VPS
    print("\n" + "=" * 60)
    print("STEP 1: Copy files to VPS")
    print("=" * 60)
    sftp = ssh.open_sftp()
    for local_rel, remote_rel in SYNC_FILES:
        local_path = os.path.join(LOCAL_MCX, local_rel)
        remote_path = f"{VPS_BASE}/{remote_rel}"
        print(f"  Uploading: {local_rel}")
        sftp.put(local_path, remote_path)
    sftp.close()

    # Step 2: Copy into container
    print("\n" + "=" * 60)
    print("STEP 2: Copy into container")
    print("=" * 60)
    for _, remote_rel in SYNC_FILES:
        run_ssh(ssh, f"docker cp {VPS_BASE}/{remote_rel} mcx-trader:/app/{remote_rel}")

    # Step 3: Fix the state file - halve the double-counted P&L
    print("\n" + "=" * 60)
    print("STEP 3: Fix corrupted state file P&L")
    print("=" * 60)
    # The heal double-counted P&L. Fix: halve realized_pnl in accounts and PNL engines
    run_ssh(ssh, """docker exec mcx-trader python3 -c "
import json

path = '/app/data/db/system_state.json'
with open(path) as f:
    state = json.load(f)

# Fix global account: realized_pnl was doubled
acct = state.get('account', {})
if acct.get('realized_pnl', 0) > 0:
    # The correct realized_pnl = gold_01 (30611.11) + silver_02 (31425.03) = 62036.14
    # After double-count it became 124072.28
    print(f'BEFORE global realized_pnl: {acct[\"realized_pnl\"]}')
    acct['realized_pnl'] = 62036.14
    acct['charges'] = 543.86
    print(f'AFTER global realized_pnl: {acct[\"realized_pnl\"]}')

# Fix per-strategy accounts
for strat_id in ['gold_01', 'silver_02']:
    sa = state.get('accounts_by_strategy', {}).get(strat_id, {})
    if sa:
        print(f'BEFORE {strat_id} realized_pnl: {sa.get(\"realized_pnl\", 0)}')
        if strat_id == 'gold_01':
            sa['realized_pnl'] = 30611.11
            sa['charges'] = 298.89
        elif strat_id == 'silver_02':
            sa['realized_pnl'] = 31425.03
            sa['charges'] = 244.97
        print(f'AFTER {strat_id} realized_pnl: {sa[\"realized_pnl\"]}')

# Fix PNL engines
for strat_id in ['gold_01', 'silver_02']:
    pnl = state.get('pnl', {}).get(strat_id, {})
    if pnl:
        print(f'BEFORE {strat_id} realized_net: {pnl.get(\"realized_net\", 0)}, trade_count: {pnl.get(\"trade_count\", 0)}')
        if strat_id == 'gold_01':
            pnl['realized_net'] = 30611.11
            pnl['realized_gross'] = 30910.0
            pnl['realized_charges'] = 298.89
            pnl['trade_count'] = 1
        elif strat_id == 'silver_02':
            pnl['realized_net'] = 31425.03
            pnl['realized_gross'] = 31669.999
            pnl['realized_charges'] = 244.97
            pnl['trade_count'] = 1
        print(f'AFTER {strat_id} realized_net: {pnl[\"realized_net\"]}, trade_count: {pnl[\"trade_count\"]}')

# Remove healed positions from open_positions (they should be closed)
# gold_01 and silver_02 positions were healed but state file still has them open
pos = state.get('positions', {})
open_pos = pos.get('open_positions', {})
for strat_id in ['gold_01', 'silver_02']:
    if strat_id in open_pos:
        del open_pos[strat_id]
        print(f'Removed {strat_id} from open_positions (was healed)')

with open(path, 'w') as f:
    json.dump(state, f, indent=2, default=str)
print('State file fixed!')
" """)

    # Step 4: Restart
    print("\n" + "=" * 60)
    print("STEP 4: Restart container")
    print("=" * 60)
    run_ssh(ssh, "docker restart mcx-trader", timeout=60)

    # Step 5: Wait and check
    print("\nWaiting 15s for startup...")
    import time
    time.sleep(15)

    print("\n" + "=" * 60)
    print("STEP 5: Check Heal + Reconciliation output")
    print("=" * 60)
    out, err = run_ssh(ssh, "docker logs mcx-trader 2>&1 | grep -iE 'heal|reconcil|safe|error|CRITICAL|mismatch' | tail -20", timeout=15)

    ssh.close()
    print("\nDeploy complete!")

import os
if __name__ == "__main__":
    main()
