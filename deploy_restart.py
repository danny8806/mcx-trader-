"""Restart the mcx-trader container on the VPS (no rebuild).

Credentials are read ONLY from the local gitignored env file (default
mcx-trader.env) or environment variables -- never hardcoded here.  They are
uploaded as an env file on the VPS and passed to `docker run --env-file`, so
they never appear in `ps`, shell history, or `docker inspect`.

Usage:
    python deploy_restart.py
    python deploy_restart.py --env-file C:\\path\\to\\custom.env
"""
import argparse
import os
import stat
import sys
import tempfile
import time

import paramiko

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

VPS_HOST = '200.234.44.93'
VPS_USER = 'root'
VPS_BASE = '/home/jadhavdnyaneshwar701/mcx-trader-data'
MCX_TRADER_DIR = os.path.dirname(os.path.abspath(__file__))


def load_env_file(path: str) -> dict:
    seed = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                seed[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        return {}
    return seed


def main():
    parser = argparse.ArgumentParser(description="Restart mcx-trader on VPS")
    parser.add_argument("--env-file",
                        default=os.path.join(MCX_TRADER_DIR, "mcx-trader.env"),
                        help="Local env file with VPS_PASS + Dhan creds")
    args = parser.parse_args()

    seed = load_env_file(args.env_file)
    required = ["VPS_PASS", "DHAN_CLIENT_ID", "TRADING_PIN", "TOTP_SECRET"]
    missing = [k for k in required
               if not (seed.get(k) or os.environ.get(k, ""))]
    if missing:
        sys.exit("Restart aborted: missing in env/env-file (never stored "
                 "in source): " + ", ".join(missing))

    def resolve(var: str) -> str:
        return os.environ.get(var, "") or seed.get(var, "")

    vps_pass = resolve("VPS_PASS")
    creds = {
        "DHAN_CLIENT_ID": resolve("DHAN_CLIENT_ID"),
        "DHAN_ACCESS_TOKEN": resolve("DHAN_ACCESS_TOKEN"),
        "TRADING_PIN": resolve("TRADING_PIN"),
        "TOTP_SECRET": resolve("TOTP_SECRET"),
    }
    if resolve("TELEGRAM_BOT_TOKEN") and resolve("TELEGRAM_CHAT_ID"):
        creds["TELEGRAM_BOT_TOKEN"] = resolve("TELEGRAM_BOT_TOKEN")
        creds["TELEGRAM_CHAT_ID"] = resolve("TELEGRAM_CHAT_ID")

    fd, env_local = tempfile.mkstemp(prefix="mcx_restart_", suffix=".env")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        for k, v in creds.items():
            f.write(f"{k}={v}\n")
    vps_env = f"{VPS_BASE}/mcx-trader.env"

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Connecting to {VPS_HOST}...")
    try:
        ssh.connect(VPS_HOST, username=VPS_USER, password=vps_pass, timeout=15)
    except Exception as e:
        os.unlink(env_local)
        sys.exit(f"SSH connect failed: {e}")
    print("Connected!")

    def run(cmd: str, timeout: int = 60) -> tuple[str, str, int]:
        print(f"\n>>> {cmd}")
        stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        rc = stdout.channel.recv_exit_status()
        if out.strip():
            print(out)
        if err.strip():
            print(f"STDERR: {err}")
        return out, err, rc

    try:
        print("=" * 60)
        print("STEP 1: Uploading credentials (--env-file, never echoed)")
        print("=" * 60)
        run(f"mkdir -p {VPS_BASE}")
        run("docker stop mcx-trader || true")
        run("docker rm mcx-trader || true")
        sftp = ssh.open_sftp()
        sftp.put(env_local, vps_env)
        sftp.chmod(vps_env, stat.S_IRUSR | stat.S_IWUSR)
        sftp.close()
        os.unlink(env_local)  # delete local temp credentials immediately

        print("=" * 60)
        print("STEP 2: Starting container (creds already uploaded)")
        print("=" * 60)
        run(
            f"docker run -d --name mcx-trader --restart unless-stopped "
            f"--env-file {vps_env} "
            f"-p 8000:8000 "
            f"-v {VPS_BASE}/data/db:/app/data/db "
            f"mcx-trader:new python dashboard/run.py"
        )

        print("=" * 60)
        print("STEP 3: Status / health / logs")
        print("=" * 60)
        time.sleep(5)
        run('docker ps --filter name=mcx-trader --format "{{.Status}}"')
        run("curl -s http://localhost:8000/api/health", timeout=20)
        run("docker logs mcx-trader --tail 30")
    finally:
        if os.path.exists(env_local):
            os.unlink(env_local)
        ssh.close()

    print("\nRestart completed.")


if __name__ == "__main__":
    main()