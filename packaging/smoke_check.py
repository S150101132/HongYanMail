"""Launch the packaged build offscreen in demo mode and report whether it stays alive."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from verify_data import clean, isolate

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / 'dist' / 'Hongyan' / 'Hongyan.exe'
WAIT_SECONDS = 25

environment = dict(os.environ)
environment['QT_QPA_PLATFORM'] = 'offscreen'
isolate(environment)
started = time.time()
process = subprocess.Popen([str(EXE), '--demo'], cwd=str(EXE.parent), env=environment,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    code = process.wait(timeout=WAIT_SECONDS)
    status, exit_code = 'exited_early', code
except subprocess.TimeoutExpired:
    status, exit_code = 'running', None
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
clean()
elapsed = round(time.time() - started, 2)
report = {'executable': str(EXE), 'exists': EXE.is_file(),
          'size_bytes': EXE.stat().st_size if EXE.is_file() else None,
          'arguments': '--demo', 'platform_plugin': 'offscreen',
          'status': status, 'exit_code': exit_code, 'elapsed_seconds': elapsed}
print(json.dumps(report, ensure_ascii=False, indent=2))
sys.exit(0 if status == 'running' else 1)
