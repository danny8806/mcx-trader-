import paramiko, json

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('200.234.44.93', username='root', password='Deltacapitals@123')

stdin, stdout, stderr = ssh.exec_command('docker ps | grep mcx-trader')
print('Container:', stdout.read().decode().strip())

stdin, stdout, stderr = ssh.exec_command('docker logs mcx-trader --tail 10 2>&1')
print('Logs:', stdout.read().decode())

stdin, stdout, stderr = ssh.exec_command('cat /home/jadhavdnyaneshwar701/mcx-trader-data/data/db/system_state.json')
raw = stdout.read().decode().strip()
if raw:
    state = json.loads(raw)
    override = state.get('market_status', {}).get('force_state_override')
    print('force_state_override:', override)
else:
    print('State file empty/missing')

ssh.close()
