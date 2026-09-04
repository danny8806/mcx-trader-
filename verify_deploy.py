import paramiko, json

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('200.234.44.93', username='root', password='Deltacapitals@123', timeout=15)

# Dashboard HTTP
stdin, stdout, stderr = client.exec_command('curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/')
print('Dashboard HTTP:', stdout.read().decode().strip())

# Container status
stdin, stdout, stderr = client.exec_command('docker ps --filter name=mcx-trader --format "{{.Status}}"')
print('Container:', stdout.read().decode().strip())

# Trades count
stdin, stdout, stderr = client.exec_command('curl -s http://localhost:8000/api/trades')
data = json.loads(stdout.read().decode())
print('Trades:', data.get('count', 0), '(source:', data.get('source', '?') + ')')

# Health
stdin, stdout, stderr = client.exec_command('curl -s http://localhost:8000/api/health')
health = json.loads(stdout.read().decode())
print('Health:', health.get('status'), '| engine:', health.get('engine'))

# Lifecycle endpoints
stdin, stdout, stderr = client.exec_command('curl -s http://localhost:8000/api/trades/orphan-scan')
orphan = json.loads(stdout.read().decode())
print('Orphan scan: clean=', orphan.get('is_clean'), '| total_orphans=', orphan.get('total_orphans'))

client.close()
