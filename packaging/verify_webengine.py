"""Verify the packaged build really initializes its Qt WebEngine reader.

Runs the shipped executable with Chromium logging enabled and fails if WebEngine
cannot start or reports a fatal renderer problem, so an over-trimmed bundle is
caught even though the shell itself still starts.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / 'dist' / 'CoremailDesktopPreview' / 'CoremailDesktopPreview.exe'
LOG = ROOT / 'runtime' / 'packaged-webengine.log'
ALIVE_SECONDS = 20

FATAL_MARKERS = ('Check failed', 'FATAL', 'DLL load failed', 'ImportError',
                 'Could not find QtWebEngineProcess', 'QtWebEngineProcess.exe')

environment = dict(os.environ)
environment['QT_QPA_PLATFORM'] = 'offscreen'
environment['QTWEBENGINE_CHROMIUM_FLAGS'] = '--enable-logging=stderr --v=0'
environment['QTWEBENGINE_DISABLE_SANDBOX'] = '1'

with open(LOG, 'w', encoding='utf-8') as stream:
    process = subprocess.Popen([str(EXE), '--demo'], cwd=str(EXE.parent), env=environment,
                               stdout=stream, stderr=subprocess.STDOUT)
    deadline = time.time() + ALIVE_SECONDS
    while time.time() < deadline and process.poll() is None:
        time.sleep(0.5)
    alive = process.poll() is None
    if alive:
        process.kill()
    process.wait()

text = LOG.read_text(encoding='utf-8', errors='replace')
problems = sorted({marker for marker in FATAL_MARKERS if marker in text})
report = {
    'executable': str(EXE),
    'status': 'running' if alive else 'exited_early',
    'exit_code': process.returncode,
    'webengine_started': 'QtWebEngineProcess' in text or 'WebEngine' in text or 'webengine' in text,
    'fatal_markers': problems,
    'verdict': 'ok' if alive and not problems else 'needs_review',
}
print(json.dumps(report, ensure_ascii=False, indent=2))
sys.exit(0 if report['verdict'] == 'ok' else 1)
