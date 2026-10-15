"""Verify the shell at 100/125/150/200 percent display scaling.

The design document requires a usable layout at these scale factors. Each factor
is measured in its own process, because QT_SCALE_FACTOR is only honoured when it
is set before QApplication is created.

Offscreen geometry assertions only. A visual inspection on a real scaled display,
and text clipping inside labels, remain unmeasured and are recorded as such.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SINGLE = ROOT / 'packaging' / 'scaling_single.py'
REPORT = ROOT / 'analysis' / 'scaling-baseline.json'
FACTORS = (1.0, 1.25, 1.5, 2.0)


def measure(factor):
    environment = dict(os.environ)
    environment['QT_QPA_PLATFORM'] = 'offscreen'
    environment['QT_SCALE_FACTOR'] = str(factor)
    completed = subprocess.run([sys.executable, '-u', str(SINGLE), str(factor)],
                               cwd=str(ROOT), env=environment, capture_output=True, text=True, timeout=180)
    for line in reversed(completed.stdout.splitlines()):
        if line.strip().startswith('{'):
            return json.loads(line)
    return {'scale_factor': factor, 'error': 'no measurement',
            'stderr_tail': completed.stderr.strip().splitlines()[-3:]}


def main():
    results = [measure(factor) for factor in FACTORS]
    ok = all(item.get('minimum_holds') and item.get('all_panes_visible')
             and item.get('all_buttons_usable') and item.get('toolbar_present')
             for item in results)
    report = {
        'measured_at': '2026-10-15',
        'method': 'offscreen Qt, one process per QT_SCALE_FACTOR; geometry assertions, not visual inspection',
        'results': results,
        'all_checks_passed': ok,
        'not_measured': [
            'visual inspection on a real 125/150/200 percent display',
            'text clipping or truncation inside labels and buttons',
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    for item in results:
        print('factor %-5s applied_ratio %-5s window %-11s panes_visible=%s buttons_ok=%s toolbar=%s'
              % (item.get('scale_factor'), item.get('applied_ratio'), item.get('window_size'),
                 item.get('all_panes_visible'), item.get('all_buttons_usable'), item.get('toolbar_present')))
    print(json.dumps({'all_checks_passed': ok}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
