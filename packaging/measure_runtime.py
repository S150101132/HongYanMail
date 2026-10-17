"""Measure packaged startup time and idle memory for the acceptance record.

Starts the shipped executable, waits until it is up, then samples the process
tree (application, Qt WebEngine renderer, GPU process) for working set and
private bytes. Single machine, not a clean-user baseline.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE_NAME = 'Hongyan.exe'
REPORT = ROOT / 'analysis' / 'runtime-baseline.json'
SAMPLE_SECONDS = 8
STEP_SECONDS = 2


def process_table():
    command = ['powershell', '-NoProfile', '-Command',
               "Get-CimInstance Win32_Process | Where-Object { $_.Name -like '*Hongyan*' -or $_.Name -like '*QtWebEngine*' -or $_.Name -like '*Qt*Process*' } | "
               "Select-Object ProcessId,ParentProcessId,Name,WorkingSetSize,PrivatePageCount | ConvertTo-Json -Compress"]
    output = subprocess.run(command, capture_output=True, text=True).stdout.strip()
    if not output:
        return []
    parsed = json.loads(output)
    return parsed if isinstance(parsed, list) else [parsed]


def wait_until_memory_settles(process, timeout_seconds=60):
    """Approximate readiness: memory stops growing for three consecutive samples.

    Reported as an approximation because the packaged entry point exposes no
    readiness signal; it is not claimed to be first-paint time.
    """
    previous, stable, started = None, 0, time.perf_counter()
    while time.perf_counter() - started < timeout_seconds:
        if process.poll() is not None:
            return None
        rows = process_table()
        current = sum(int(row.get('WorkingSetSize') or 0) for row in rows)
        if previous is not None and abs(current - previous) < 2 * 1024 * 1024:
            stable += 1
            if stable >= 3:
                return round(time.perf_counter() - started, 2)
        else:
            stable = 0
        previous = current
        time.sleep(1)
    return None


def main():
    executable = ROOT / 'dist' / 'Hongyan' / EXE_NAME
    if not executable.is_file():
        print(json.dumps({'status': 'missing', 'executable': str(executable)}, ensure_ascii=False))
        return 1
    environment = dict(os.environ)
    environment['QT_QPA_PLATFORM'] = 'offscreen'
    process = subprocess.Popen([str(executable), '--demo'], cwd=str(executable.parent), env=environment,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    settle_seconds = wait_until_memory_settles(process)
    samples = []
    for _ in range(SAMPLE_SECONDS // STEP_SECONDS):
        rows = process_table()
        total_ws = sum(int(row.get('WorkingSetSize') or 0) for row in rows)
        total_private = sum(int(row.get('PrivatePageCount') or 0) for row in rows)
        samples.append({'processes': len(rows),
                        'working_set_mb': round(total_ws / 1024 / 1024, 1),
                        'private_mb': round(total_private / 1024 / 1024, 1),
                        'names': sorted({row['Name'] for row in rows})})
        time.sleep(STEP_SECONDS)
    alive = process.poll() is None
    process.kill()
    process.wait()
    report = {
        'measured_at': '2026-10-15',
        'executable': str(executable),
        'arguments': '--demo',
        'machine_note': 'Single development machine with offscreen platform; not a clean-user-profile baseline.',
        'startup_approximation_seconds': settle_seconds,
        'startup_method': 'time until total process working set stopped growing for three samples',
        'stayed_alive': alive,
        'samples': samples,
        'idle_working_set_mb': samples[-1]['working_set_mb'] if samples else None,
        'idle_private_mb': samples[-1]['private_mb'] if samples else None,
        'note': 'Qt WebEngine renderer children are included when present.',
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
