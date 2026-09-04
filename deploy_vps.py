"""
Deploy MCX-TRADER to VPS.
Syncs modified files, rebuilds Docker, restarts container with env vars, verifies.
"""
import paramiko
import os
import sys
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

VPS_HOST = '200.234.44.93'
VPS_USER = 'root'
VPS_PASS = 'Deltacapitals@123'
VPS_BASE = '/home/jadhavdnyaneshwar701/mcx-trader-data'

# Source of truth: MCX-TRADER directory
MCX_TRADER_DIR = r'C:\Users\pc\Desktop\MCX-TRADER'

# Env vars for the container (Dhan API credentials)
CONTAINER_ENV = {
    'DHAN_CLIENT_ID': '1102461741',
    'DHAN_ACCESS_TOKEN': '',
    'TRADING_PIN': '',
    'TOTP_SECRET': '',
}

# Files to sync (local path relative to MCX_TRADER_DIR -> remote path relative to VPS_BASE)
SYNC_FILES = [
    'config/settings.json',
    'strategies/base_dema_strategy.py',
    'strategies/silver/__init__.py',
    'trading_engine.py',
    'portfolio/position_manager.py',
    'core/trade_close.py',
    'core/market_status.py',
    'core/lifecycle.py',
    'persistence/manager.py',
    'dashboard/routes/trades.py',
    'dashboard/routes/reconciliation.py',
    'dashboard-ui/src/pages/LiveTrading.tsx',
    'dashboard-ui/src/pages/Orders.tsx',
    'dashboard-ui/src/pages/Trades.tsx',
    'dashboard-ui/src/lib/api.ts',
    'tests/fresh_audit/test_comprehensive.py',
    'tests/fresh_audit/test_deep_backend.py',
    'tests/fresh_audit/test_full_deep_architecture.py',
    'tests/fresh_audit/test_lifecycle.py',
    'tests/adversarial_trade_lifecycle/test_master_reverse_engineering.py',
    'tests/adversarial_trade_lifecycle/test_memory_db_reconciliation.py',
    'tests/adversarial_trade_lifecycle/test_corruption_mutation.py',
    'tests/adversarial_trade_lifecycle/test_trade_identity_divergence.py',
    'tests/adversarial_trade_lifecycle/test_lifecycle_persistence_failure.py',
    'tests/adversarial_trade_lifecycle/test_pnl_and_close_correctness.py',
    'tests/adversarial_trade_lifecycle/test_signal_id_immutability.py',
    'tests/adversarial_trade_lifecycle/test_db_integrity_orphan.py',
    'tests/adversarial_trade_lifecycle/test_duplicate_and_edge_cases.py',
    'ADVERSARIAL_TEST_VERIFICATION_REPORT.md',
    'reverse_engineering_results.json',
]

def run_ssh(ssh, cmd, timeout=30):
    """Execute command on VPS and return output."""
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
    print(f"Connecting to {VPS_HOST}...")
    ssh.connect(VPS_HOST, username=VPS_USER, password=VPS_PASS, timeout=15)
    print("Connected!\n")

    # ── Step A: Sync files ──
    print("=" * 60)
    print("STEP A: Syncing files to VPS")
    print("=" * 60)

    sftp = ssh.open_sftp()
    # Create remote directories first
    remote_dirs = set()
    for file_rel in SYNC_FILES:
        remote_dir = os.path.dirname(f"{VPS_BASE}/{file_rel}")
        remote_dirs.add(remote_dir)
    for d in sorted(remote_dirs):
        run_ssh(ssh, f"mkdir -p {d}")

    for file_rel in SYNC_FILES:
        local_path = os.path.join(MCX_TRADER_DIR, file_rel)
        remote_path = f"{VPS_BASE}/{file_rel}"
        if not os.path.exists(local_path):
            print(f"  SKIP (not found locally): {file_rel}")
            continue
        print(f"  Uploading: {file_rel} -> {remote_path}")
        sftp.put(local_path, remote_path)
    sftp.close()
    print("\nAll files synced.")

    # ── Step B: Rebuild Docker ──
    print("\n" + "=" * 60)
    print("STEP B: Rebuilding Docker container")
    print("=" * 60)

    run_ssh(ssh, f"cd {VPS_BASE} && docker build --no-cache -t mcx-trader:new .", timeout=600)
    run_ssh(ssh, "docker stop mcx-trader || true")
    run_ssh(ssh, "docker rm mcx-trader || true")
    env_flags = " ".join(f"-e {k}={v}" for k, v in CONTAINER_ENV.items())
    run_ssh(ssh, (
        f"docker run -d --name mcx-trader --restart unless-stopped "
        f"{env_flags} "
        f"-p 8000:8000 "
        f"-v /home/jadhavdnyaneshwar701/mcx-trader-data/data/db:/app/data/db "
        f"mcx-trader:new python dashboard/run.py"
    ))

    # ── Step C: Verify container running ──
    print("\n" + "=" * 60)
    print("STEP C: Verifying container status")
    print("=" * 60)

    time.sleep(3)
    out, _ = run_ssh(ssh, "docker ps | grep mcx-trader")
    if "mcx-trader" in out:
        print("✓ Container is running")
    else:
        print("✗ Container NOT found in docker ps!")

    run_ssh(ssh, "docker logs mcx-trader --tail 5")

    # ── Step D: Verify env vars and config ──
    print("\n" + "=" * 60)
    print("STEP D: Verifying container env vars and config")
    print("=" * 60)

    run_ssh(ssh, 'docker exec mcx-trader printenv | grep -i DHAN')
    run_ssh(ssh, (
        'docker exec mcx-trader python -c "'
        "import json; "
        "c=json.load(open('config/settings.json')); "
        "print('silver_01 fast_timeframe:', c['strategies']['silver_01']['fast_timeframe'])"
        '"'
    ))

    # ── Step E: Verify WS connectivity ──
    print("\n" + "=" * 60)
    print("STEP E: Verifying WS connectivity (waiting 10s)")
    print("=" * 60)
    time.sleep(10)
    run_ssh(ssh, "docker logs mcx-trader --tail 20")

    ssh.close()
    print("\n" + "=" * 60)
    print("DEPLOYMENT COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    main()
