"""Kill the server on port 8900 using Windows API"""
import ctypes
import subprocess
import time

# Find PID from netstat
result = subprocess.run(['netstat', '-ano'], capture_output=True, text=True)
pid = None
for line in result.stdout.splitlines():
    if ':8900' in line and 'LISTENING' in line:
        parts = line.split()
        pid = int(parts[-1])
        break

if pid:
    print(f'Found PID: {pid}')
    PROCESS_TERMINATE = 0x0001
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if handle:
        ret = ctypes.windll.kernel32.TerminateProcess(handle, 1)
        ctypes.windll.kernel32.CloseHandle(handle)
        print(f'TerminateProcess result: {ret}')
    else:
        err = ctypes.windll.kernel32.GetLastError()
        print(f'OpenProcess failed: error {err}')
else:
    print('No process found on port 8900')

time.sleep(3)

# Verify
result2 = subprocess.run(['netstat', '-ano'], capture_output=True, text=True)
found = False
for line in result2.stdout.splitlines():
    if ':8900' in line and 'LISTENING' in line:
        found = True
        print(f'Still listening: {line}')
if not found:
    print('Port 8900 is free!')
