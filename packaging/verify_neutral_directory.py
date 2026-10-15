"""Launch the packaged build from a neutral working directory.

A shipped onedir bundle must not depend on being started from its own folder, and
it must not scatter files into whatever directory the user happens to be in. This
runs the executable with cwd set to an unrelated temporary directory and checks
both properties.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / 'dist' / 'CoremailDesktopPreview' / 'CoremailDesktopPreview.exe'
ALIVE_SECONDS = 18


def main():
    if not EXE.is_file():
        print(json.dumps({'status': 'missing', 'executable': str(EXE)}, ensure_ascii=False))
        return 1
    with tempfile.TemporaryDirectory() as neutral:
        before = {item.name for item in Path(neutral).iterdir()}
        environment = dict(os.environ)
        environment['QT_QPA_PLATFORM'] = 'offscreen'
        process = subprocess.Popen([str(EXE), '--demo'], cwd=neutral, env=environment,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + ALIVE_SECONDS
        while time.time() < deadline and process.poll() is None:
            time.sleep(0.5)
        alive = process.poll() is None
        if alive:
            process.kill()
        process.wait()
        after = {item.name for item in Path(neutral).iterdir()}
        report = {
            'executable': str(EXE),
            'working_directory': neutral,
            'status': 'running' if alive else 'exited_early',
            'exit_code': process.returncode,
            'files_created_in_working_directory': sorted(after - before),
            'verdict': 'ok' if alive and not (after - before) else 'needs_review',
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['verdict'] == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())
