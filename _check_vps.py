import paramiko, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('200.234.44.93', username='root', password='Deltacapitals@123', timeout=10)

# List all folders in /home/jadhavdnyaneshwar701/
cmd = "ls -la /home/jadhavdnyaneshwar701/"
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=10)
print("Home dir:")
print(stdout.read().decode('utf-8', errors='replace'))

# Check Docker run to see what mount is used
cmd2 = "docker inspect mcx-trader --format '{{json .Mounts}}' 2>/dev/null | python3 -m json.tool"
stdin, stdout, stderr = ssh.exec_command(cmd2, timeout=10)
print("Docker mounts:")
print(stdout.read().decode('utf-8', errors='replace'))

# Check if there's a 'mcx-trader' folder too
cmd3 = "ls -la /home/jadhavdnyaneshwar701/mcx-trader/ 2>/dev/null || echo 'No mcx-trader folder'"
stdin, stdout, stderr = ssh.exec_command(cmd3, timeout=10)
print("mcx-trader folder:")
print(stdout.read().decode('utf-8', errors='replace'))

ssh.close()
