import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('200.234.44.93', username='root', password='Deltacapitals@123')

# Check the container's market_status.py snapshot/restore methods
stdin, stdout, stderr = ssh.exec_command('docker exec mcx-trader grep -n "def snapshot" /app/core/market_status.py')
print('snapshot method lines:', stdout.read().decode())

stdin, stdout, stderr = ssh.exec_command('docker exec mcx-trader grep -n "def restore" /app/core/market_status.py')
print('restore method lines:', stdout.read().decode())

# Show the snapshot method content
stdin, stdout, stderr = ssh.exec_command('docker exec mcx-trader python -c "import inspect, core.market_status as ms; print(inspect.getsource(ms.MarketStatus.snapshot))"')
print('snapshot source:')
print(stdout.read().decode())

# Show the restore method content
stdin, stdout, stderr = ssh.exec_command('docker exec mcx-trader python -c "import inspect, core.market_status as ms; print(inspect.getsource(ms.MarketStatus.restore))"')
print('restore source:')
print(stdout.read().decode())

ssh.close()
