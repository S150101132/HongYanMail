"""Sample the packaged build over a sustained run to look for unbounded growth.

The design document asks for a startup and idle-memory baseline and warns against
claiming lightness without measurement. A short idle sample cannot distinguish a
settling process from a leak, so this holds the application open and samples the
whole process tree periodically, then reports the trend.

Offscreen, demo mode, synthetic data only.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / 'dist' / 'Hongyan' / 'Hongyan.exe'
REPORT = ROOT / 'analysis' / 'stability-baseline.json'
DURATION_SECONDS = int(os.environ.get('COREMAIL_STABILITY_SECONDS', '600'))
INTERVAL_SECONDS = 30
GROWTH_TOLERANCE = 0.15


def sample_tree():
    command = ['powershell', '-NoProfile', '-Command',
               "Get-CimInstance Win32_Process | Where-Object { $_.Name -like '*Hongyan*' -or $_.Name -like '*QtWebEngine*' } | "
               "Select-Object ProcessId,Name,WorkingSetSize,PrivatePageCount | ConvertTo-Json -Compress"]
    output = subprocess.run(command, capture_output=True, text=True).stdout.strip()
    if not output:
        return []
    parsed = json.loads(output)
    rows = parsed if isinstance(parsed, list) else [parsed]
    working_set = sum(int(row.get('WorkingSetSize') or 0) for row in rows)
    private = sum(int(row.get('PrivatePageCount') or 0) for row in rows)
    return {'processes': len(rows),
            'working_set_mb': round(working_set / 1024 / 1024, 1),
            'private_mb': round(private / 1024 / 1024, 1)}


def main():
    if not EXE.is_file():
        print(json.dumps({'status': 'missing', 'executable': str(EXE)}, ensure_ascii=False))
        return 1
    environment = dict(os.environ)
    environment['QT_QPA_PLATFORM'] = 'offscreen'
    process = subprocess.Popen([str(EXE), '--demo'], cwd=str(EXE.parent), env=environment,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    samples, started = [], time.perf_counter()
    try:
        while time.perf_counter() - started < DURATION_SECONDS:
            time.sleep(INTERVAL_SECONDS)
            if process.poll() is not None:
                break
            measurement = sample_tree()
            measurement['elapsed_seconds'] = round(time.perf_counter() - started, 1)
            samples.append(measurement)
            print(json.dumps(measurement, ensure_ascii=False), flush=True)
    finally:
        alive = process.poll() is None
        process.kill()
        process.wait()

    growth = None
    if len(samples) >= 6:
        third = max(1, len(samples) // 3)
        early = sum(item['working_set_mb'] for item in samples[:third]) / third
        late = sum(item['working_set_mb'] for item in samples[-third:]) / third
        growth = round((late - early) / early, 4) if early else None
    report = {
        'measured_at': '2026-10-15',
        'executable': str(EXE),
        'duration_seconds': round(time.perf_counter() - started, 1),
        'interval_seconds': INTERVAL_SECONDS,
        'samples': samples,
        'stayed_alive_full_run': alive,
        'working_set_growth_ratio': growth,
        'growth_tolerance': GROWTH_TOLERANCE,
        'verdict': ('ok' if alive and growth is not None and growth <= GROWTH_TOLERANCE else 'needs_review'),
        'note': 'Demo mode with no network and no user interaction; this is a leak guard, not a workload profile.',
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in
                      ('duration_seconds', 'stayed_alive_full_run', 'working_set_growth_ratio', 'verdict')},
                     ensure_ascii=False))
    return 0 if report['verdict'] == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())
