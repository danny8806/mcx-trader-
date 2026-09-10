"""
Deploy MCX-TRADER to VPS.

Credentials are taken as INPUT AT DEPLOY TIME (no secrets are hardcoded):
  1. VPS password  — environment variable VPS_PASS, or prompted (getpass).
  2. Dhan client id - command-line --client-id, env DHAN_CLIENT_ID, or prompt.
  3. Dhan PIN / TOTP secret (and optional access token) - command-line args,
     env vars, or secret prompts (getpass, never echoed).

Credentials are written to a gitignored env file on the VPS and passed to
`docker run --env-file` so they never appear in `ps` output, shell history,
or `-e` process arguments.  The local temp file is deleted afterwards.

Usage (from cmd):
    python deploy_vps.py --totp-secret <TOTP> --pin <PIN> --client-id <CLIENT_ID>
    python deploy_vps.py --client-id <CLIENT_ID>      # prompt for PIN + TOTP
    python deploy_vps.py                              # prompt for everything

Credentials may also be seeded from a local env file (default mcx-trader.env,
the gitignored file next to this script).  Precedence:
    command-line args > process environment > env file > interactive prompt.
"""
import argparse
import getpass
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

# Source of truth: MCX-TRADER directory
MCX_TRADER_DIR = r'C:\Users\pc\Desktop\MCX-TRADER'

# Files/dirs never uploaded (secrets, caches, heavy artifacts, VCS)
SKIP = {'.git', '__pycache__', '.pytest_cache', 'node_modules', 'reports',
        'replay_output', 'tests', 'data/db', 'dashboard-ui/dist', '.vscode',
        '.idea', 'mcx-trader.env'}
SKIP_SUFFIX = ('.pyc', '.pyo', '.db', '.db-shm', '.db-wal', '.log', '.tmp',
               '.bak', '.lock')


def load_env_file(path: str) -> dict:
    """Parse a local KEY=VALUE env file; values are never printed."""
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


def prompt_secret(prompt: str) -> str:
    """Prompt for a secret via getpass; aborts cleanly if not a TTY."""
    try:
        return getpass.getpass(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def prompt_visible(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def gather_credentials(args: argparse.Namespace,
                       seed: dict) -> tuple[dict, str]:
    """Collect Dhan credentials: args -> env -> env-file -> interactive prompts.

    Returns (credential_dict, vps_password).  Secrets are never printed.
    Access token is optional: PIN+TOTP renew_token() mints a fresh token on
    container boot, so an empty token is valid when PIN/TOTP are provided.
    """
    vps_pass = (os.environ.get("VPS_PASS", "")
                or seed.get("VPS_PASS", "")
                or prompt_secret("VPS password (not echoed): "))
    if not vps_pass:
        sys.exit("Deploy aborted: no VPS password "
                 "(VPS_PASS in env/env-file or enter it).")

    client_id = (args.client_id or os.environ.get("DHAN_CLIENT_ID", "")
                 or seed.get("DHAN_CLIENT_ID", "")
                 or prompt_visible("Dhan client id (10/26 chars): "))
    pin = (args.pin or os.environ.get("TRADING_PIN", "")
           or seed.get("TRADING_PIN", "")
           or prompt_secret("Dhan trading PIN (not echoed): "))
    totp = (args.totp_secret or os.environ.get("TOTP_SECRET", "")
            or seed.get("TOTP_SECRET", "")
            or prompt_secret("Dhan TOTP seed base32 (not echoed): "))

    if not client_id or not pin or not totp:
        sys.exit("Deploy aborted: DHAN_CLIENT_ID, TRADING_PIN and TOTP_SECRET "
                 "are all required (arg, env, env-file, or prompt).")

    access_token = (args.access_token or os.environ.get("DHAN_ACCESS_TOKEN", "")
                    or seed.get("DHAN_ACCESS_TOKEN", ""))
    api_key = (args.api_key or os.environ.get("DASHBOARD_API_KEY", "")
               or seed.get("DASHBOARD_API_KEY", ""))
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "") or \
        seed.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "") or \
        seed.get("TELEGRAM_CHAT_ID", "")

    creds = {
        "DHAN_CLIENT_ID": client_id,
        "DHAN_ACCESS_TOKEN": access_token,
        "TRADING_PIN": pin,
        "TOTP_SECRET": totp,
    }
    if api_key:
        creds["DASHBOARD_API_KEY"] = api_key
    if tg_token and tg_chat:
        creds["TELEGRAM_BOT_TOKEN"] = tg_token
        creds["TELEGRAM_CHAT_ID"] = tg_chat
    return creds, vps_pass


def write_env_file(creds: dict) -> str:
    """Write credentials to a local temp env file (deleted after upload).

    newline="\\n" keeps LF-only line endings on Windows so values are not
    padded with CR (a trailing CR in TOTP_SECRET breaks pyotp -> Invalid TOTP).
    """
    fd, path = tempfile.mkstemp(prefix="mcx_env_", suffix=".env")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        for k, v in creds.items():
            f.write(f"{k}={v}\n")
    return path


def should_skip(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    if any(p in SKIP for p in parts):
        return True
    if rel.lower().endswith(SKIP_SUFFIX):
        return True
    return False


def sync_tree(sftp, local_root: str, remote_root: str) -> None:
    """Full source sync to VPS (skips secrets/caches/artifacts)."""
    n = 0
    for dirpath, dirnames, filenames in os.walk(local_root):
        rel_dir = os.path.relpath(dirpath, local_root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace("\\", "/")
        # prune ignored dirs in-place so os.walk does not descend
        dirnames[:] = [d for d in dirnames
                       if not should_skip(f"{rel_dir}/{d}".strip("/"))]
        if should_skip(rel_dir):
            continue
        remote_dir = remote_root if not rel_dir else f"{remote_root}/{rel_dir}"
        if rel_dir:
            try:
                sftp.stat(remote_dir)
            except IOError:
                sftp.mkdir(remote_dir)
        for fn in filenames:
            rel = f"{rel_dir}/{fn}".strip("/")
            if should_skip(rel):
                continue
            local_p = os.path.join(dirpath, fn)
            remote_p = f"{remote_dir}/{fn}"
            sftp.put(local_p, remote_p)
            n += 1
            if n % 50 == 0:
                print(f"  ... {n} files synced")
    print(f"Uploaded {n} files")


def run_ssh(ssh, cmd, timeout=30):
    """Execute command on VPS and return (out, err, rc)."""
    print(f"\n>>> {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    rc = stdout.channel.recv_exit_status()
    if out.strip():
        print(out)
    if err.strip():
        print(f"STDERR: {err}")
    return out, err, rc


def verify_env_no_leak(ssh) -> None:
    """Check presence of env vars in the container WITHOUT printing values."""
    checks = {
        "DHAN_CLIENT_ID": "client id",
        "TRADING_PIN": "trading PIN",
        "TOTP_SECRET": "TOTP secret",
    }
    for var, label in checks.items():
        # single quotes on the remote host => container's sh expands $VAR
        out, _, _ = run_ssh(
            ssh, f"docker exec mcx-trader sh -c "
                 f"'[ -n \"${var}\" ] && echo {label}_PRESENT "
                 f"|| echo {label}_MISSING'")
        if f"{label}_MISSING" in out:
            print(f"  WARNING: {label} missing in container")


def main():
    parser = argparse.ArgumentParser(description="Deploy MCX-TRADER to VPS")
    parser.add_argument("--client-id", default="",
                        help="Dhan client id (else env DHAN_CLIENT_ID or prompt)")
    parser.add_argument("--pin", default="",
                        help="Dhan trading PIN (else env TRADING_PIN or prompt)")
    parser.add_argument("--totp-secret", default="",
                        help="Dhan TOTP base32 seed (else env TOTP_SECRET or prompt)")
    parser.add_argument("--access-token", default="",
                        help="Optional existing Dhan access token (else renewed on boot)")
    parser.add_argument("--api-key", default="",
                        help="DASHBOARD_API_KEY (recommended for public VPS; "
                             "else env DASHBOARD_API_KEY)")
    parser.add_argument("--env-file", default=os.path.join(MCX_TRADER_DIR,
                                                           "mcx-trader.env"),
                        help="Local env file seeding VPS_PASS + Dhan creds "
                             "(default mcx-trader.env; gitignored)")
    parser.add_argument("--host", default=VPS_HOST)
    parser.add_argument("--user", default=VPS_USER)
    parser.add_argument("--base", default=VPS_BASE)
    parser.add_argument("--purge-data", action="store_true",
                        help="Wipe the runtime data/db on the VPS (trading.db, "
                             "system_state.json, dhan_token.json) so the "
                             "container restarts with a brand-new architecture "
                             "state and empty DB")
    args = parser.parse_args()

    seed = load_env_file(args.env_file)
    creds, vps_pass = gather_credentials(args, seed)
    env_file = write_env_file(creds)
    vps_env_path = f"{args.base}/mcx-trader.env"

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Connecting to {args.host}...")
    try:
        ssh.connect(args.host, username=args.user, password=vps_pass, timeout=15)
    except Exception as e:
        os.unlink(env_file)
        sys.exit(f"SSH connect failed: {e}")
    print("Connected!")

    try:
        # ── Step A: Full source sync ──
        print("=" * 60)
        print("STEP A: Syncing source to VPS")
        print("=" * 60)
        run_ssh(ssh, f"mkdir -p {args.base}")
        sftp = ssh.open_sftp()
        sync_tree(sftp, MCX_TRADER_DIR, args.base)
        sftp.close()

        # ── Step B: Upload credentials as env file (not visible in ps) ──
        print("\n" + "=" * 60)
        print("STEP B: Uploading credentials (--env-file, never echoed)")
        print("=" * 60)
        sftp = ssh.open_sftp()
        sftp.put(env_file, vps_env_path)
        sftp.chmod(vps_env_path, stat.S_IRUSR | stat.S_IWUSR)
        sftp.close()
        os.unlink(env_file)  # delete local temp credentials immediately
        print("  env file uploaded to", vps_env_path, "(chmod 600)")

        # ── Step C: Rebuild Docker container ──
        print("\n" + "=" * 60)
        print("STEP C: Rebuilding Docker image")
        print("=" * 60)
        run_ssh(ssh, f"cd {args.base} && docker build --no-cache -t mcx-trader:new .",
                timeout=900)
        run_ssh(ssh, "docker stop mcx-trader || true")
        run_ssh(ssh, (
            "docker rm -f $(docker ps -aq --filter name=mcx-trader) "
            "2>/dev/null || true"))
        # Remove previous images (old containers are gone, so they are free).
        run_ssh(ssh, (
            "docker rmi mcx-trader:latest mcx-trader:fulltest "
            "2>/dev/null || true"))
        # Fresh start: wipe the runtime data volume (old DB/state/token).
        if args.purge_data:
            print("  --purge-data: wiping runtime data/db (new architecture "
                  "starts from an empty DB)")
            run_ssh(ssh,
                    f"rm -rf {args.base}/data/db && mkdir -p {args.base}/data/db")
        run_ssh(ssh, (
            f"docker run -d --name mcx-trader --restart unless-stopped "
            f"-e TZ=Asia/Kolkata "
            f"--env-file {vps_env_path} "
            f"-p 8000:8000 "
            f"-v {args.base}/data/db:/app/data/db "
            f"mcx-trader:new python dashboard/run.py"
        ))
        try:
            run_ssh(ssh, "docker image prune -f || true", timeout=300)
        except Exception as e:
            print(f"  (image prune skipped: {e})")

        # ── Step D: Verify container + env presence ──
        print("\n" + "=" * 60)
        print("STEP D: Verifying container")
        print("=" * 60)
        time.sleep(3)
        out, _, _ = run_ssh(ssh, "docker ps | grep mcx-trader")
        if "mcx-trader" in out:
            print("  Container is running")
        else:
            print("  Container NOT found in docker ps!")
        verify_env_no_leak(ssh)

        # ── Step D.5: Telegram status (chat ids only, never the token) ──
        out, _, _ = run_ssh(
            ssh, "docker exec mcx-trader python -c "
                 "\"from notifications.telegram_client import TelegramClient; "
                 "s = TelegramClient().get_stats(); print('configured:', "
                 "s['configured'], '| chat_ids:', s['chat_ids'])\"")
        if "configured: False" in out:
            print("  Telegram DISABLED: set TELEGRAM_BOT_TOKEN + "
                  "TELEGRAM_CHAT_ID in the env file and restart.")

        # ── Step E: Verify WS / engine boot (10s) ──
        print("\n" + "=" * 60)
        print("STEP E: Waiting 10s then checking logs")
        print("=" * 60)
        time.sleep(10)
        out, _, _ = run_ssh(ssh, "docker logs mcx-trader --tail 25")
        if "auth] token renewed" in out or "token" in out.lower():
            print("  token/renewal activity present in logs")
    finally:
        if os.path.exists(env_file):
            os.unlink(env_file)
        ssh.close()

    print("\n" + "=" * 60)
    print("DEPLOYMENT COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()