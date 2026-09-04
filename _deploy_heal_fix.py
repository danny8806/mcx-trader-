"""Deploy heal fix to VPS: position_manager.py key fix + trading_engine.py logging."""
import paramiko
import os
import sys
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

VPS_HOST = '200.234.44.93'
VPS_USER = 'root'
VPS_PASS = 'Deltacapitals@123'
VPS_BASE = '/home/jadhavdnyaneshwar701/mcx-trader-data'
LOCAL_MCX = r'C:\Users\pc\Desktop\MCX-TRADER'

# Files to sync: (local_path, remote_path) — both relative to their bases
SYNC_FILES = [
    ('portfolio/position_manager.py', 'portfolio/position_manager.py'),
    ('trading_engine.py', 'trading_engine.py'),
    ('core/trade_close.py', 'core/trade_close.py'),
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
    print(f"Connecting to {VPS_HOST}...")
    ssh.connect(VPS_HOST, username=VPS_USER, password=VPS_PASS, timeout=15)
    print("Connected!\n")

    # Step A: Backup current container files
    print("=" * 60)
    print("STEP A: Backup current container files")
    print("=" * 60)
    ts = int(time.time())
    run_ssh(ssh, f"docker exec mcx-trader cp /app/trading_engine.py /app/trading_engine.py.bak.{ts} 2>/dev/null || true")
    run_ssh(ssh, f"docker exec mcx-trader cp /app/portfolio/position_manager.py /app/portfolio/position_manager.py.bak.{ts} 2>/dev/null || true")

    # Step B: Copy files to VPS host
    print("\n" + "=" * 60)
    print("STEP B: Copy files to VPS host")
    print("=" * 60)
    sftp = ssh.open_sftp()
    for local_rel, remote_rel in SYNC_FILES:
        local_path = os.path.join(LOCAL_MCX, local_rel)
        remote_path = f"{VPS_BASE}/{remote_rel}"
        print(f"  Uploading: {local_rel} -> {remote_rel}")
        sftp.put(local_path, remote_path)
    sftp.close()
    print("Upload complete.")

    # Step C: Copy from host to container
    print("\n" + "=" * 60)
    print("STEP C: Copy files into running container")
    print("=" * 60)
    for local_rel, remote_rel in SYNC_FILES:
        run_ssh(ssh, f"docker cp {VPS_BASE}/{remote_rel} mcx-trader:/app/{remote_rel}")

    # Step D: Restart container
    print("\n" + "=" * 60)
    print("STEP D: Restart container")
    print("=" * 60)
    run_ssh(ssh, "docker restart mcx-trader", timeout=60)

    # Step E: Wait for startup and check logs
    print("\n" + "=" * 60)
    print("STEP E: Wait and check logs")
    print("=" * 60)
    print("Waiting 15s for startup...")
    time.sleep(15)
    out, err = run_ssh(ssh, "docker logs mcx-trader --tail 60 2>&1", timeout=15)

    # Check specifically for Heal output
    print("\n" + "=" * 60)
    print("STEP F: Check Heal output")
    print("=" * 60)
    out, err = run_ssh(ssh, "docker logs mcx-trader 2>&1 | grep -i 'heal\\|reconcil\\|safe\\|error\\|CRITICAL'", timeout=15)

    # Check positions API
    print("\n" + "=" * 60)
    print("STEP G: Check positions via API")
    print("=" * 60)
    out, err = run_ssh(ssh, "curl -s http://localhost:8000/api/positions 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); print(f'Open positions: {d.get(\\\"count\\\",0)}'); [print(f'  {p[\\\"strategy_id\\\"]}: {p[\\\"position_id\"][:8]}... status={p[\\\"status\\\"]}') for p in d.get('positions',[])]\"", timeout=15)

    ssh.close()
    print("\nDeploy complete!")

if __name__ == "__main__":
    main()
