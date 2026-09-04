import paramiko, time

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('200.234.44.93', username='root', password='Deltacapitals@123', timeout=15)

# Stop and remove old container
print('=== Stopping old container ===')
stdin, stdout, stderr = client.exec_command('docker stop mcx-trader 2>/dev/null; docker rm mcx-trader 2>/dev/null; echo done')
print(stdout.read().decode().strip())

# Start new container
print('=== Starting new container ===')
docker_cmd = (
    'docker run -d --name mcx-trader --restart unless-stopped '
    '-p 8000:8000 '
    '-e DHAN_CLIENT_ID=1102461741 '
    '-e DHAN_ACCESS_TOKEN= '
    '-e TRADING_PIN= '
    '-e TOTP_SECRET= '
    '-v /home/jadhavdnyaneshwar701/mcx-trader-data/data/db:/app/data/db '
    'mcx-trader:new python dashboard/run.py'
)
stdin, stdout, stderr = client.exec_command(docker_cmd)
print(stdout.read().decode().strip())
err = stderr.read().decode().strip()
if err:
    print('STDERR:', err)

# Wait and check status
time.sleep(5)
print()
print('=== Container status ===')
stdin, stdout, stderr = client.exec_command('docker ps --filter name=mcx-trader --format "{{.Status}}"')
print(stdout.read().decode().strip())

# Check health endpoint
print()
print('=== Health check ===')
stdin, stdout, stderr = client.exec_command('curl -s http://localhost:8000/api/health')
print(stdout.read().decode().strip()[:500])

# Check logs
print()
print('=== Recent logs ===')
stdin, stdout, stderr = client.exec_command('docker logs mcx-trader --tail 30')
print(stdout.read().decode().strip()[:1500])

client.close()
