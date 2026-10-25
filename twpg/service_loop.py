"""Low-volume resumable batches; systemd controls the process lifecycle."""
import os
import signal
import subprocess
import sys
import time

stopping=False
child=None

def stop(sig,frame):
    global stopping
    stopping=True
    if child and child.poll() is None:
        child.send_signal(signal.SIGINT)

signal.signal(signal.SIGTERM,stop)
signal.signal(signal.SIGINT,stop)

while not stopping:
    cmd=[sys.executable,'-m','twpg.worker','--limit',os.getenv('TW_BATCH_LIMIT','5'),
         '--delay-ms',os.getenv('TW_DELAY_MS','1500')]
    if os.getenv('TW_RETRY_PARTIAL','false').lower()=='true':cmd.append('--retry-partial')
    print('[LOOP]',cmd,flush=True)
    child=subprocess.Popen(cmd)
    while child.poll() is None and not stopping:time.sleep(0.5)
    if child.poll() is None:child.wait(timeout=60)
    status=child.returncode
    child=None
    print('[LOOP] batch return:',status,flush=True)
    for _ in range(max(60,int(os.getenv('TW_RESCAN_SECONDS','300')))):
        if stopping:break
        time.sleep(1)
