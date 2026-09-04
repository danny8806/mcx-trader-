"""Fix safe_mode in VPS state file and restart container."""
import json
import paramiko

SSH_HOST = "200.234.44.93"
SSH_USER = "root"
SSH_PASS = "Deltacapitals@123"
STATE_PATH = "/home/jadhavdnyaneshwar701/mcx-trader-data/data/db/system_state.json"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(SSH_HOST, username=SSH_USER, password=SSH_PASS)

# Read current state
stdin, stdout, stderr = ssh.exec_command(f"cat {STATE_PATH}")
raw = stdout.read().decode().strip()
if not raw:
    print("State file empty or missing.")
else:
    state = json.loads(raw)
    current = state.get("market_status", {}).get("force_state_override")
    print(f"Current force_state_override: {current}")
    if current is not None:
        state["market_status"]["force_state_override"] = None
        payload = json.dumps(state)
        # Use a temp file to avoid quoting issues
        ssh.exec_command(f"cat > {STATE_PATH} << 'JSONEOF'\n{payload}\nJSONEOF")
        import time; time.sleep(1)
        print("State file cleaned: force_state_override set to None.")
    else:
        print("No safe_mode to clean.")

# Restart container
stdin, stdout, stderr = ssh.exec_command("docker restart mcx-trader")
stdout.read()
print("Container restarted.")

ssh.close()
