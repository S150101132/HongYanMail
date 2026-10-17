"""Where the packaged-build checks keep the demo data: outside dist, removed afterwards.

The frozen demo stores synthetic data under its data root. Left to choose on its
own, that can be HongyanData beside the executable (on machines where the user
profile is not writable), which would ship demo key files in the bundle. The
checks kill the process, so the app's own exit cleanup does not run either.
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERIFY_DATA = ROOT / 'build' / 'verify-data'


def isolate(environment):
    """Point HONGYAN_DATA_DIR at the scratch folder; returns the environment."""
    VERIFY_DATA.mkdir(parents=True, exist_ok=True)
    environment['HONGYAN_DATA_DIR'] = str(VERIFY_DATA)
    # Where Chromium's sandbox cannot start, the app relaunches itself without it and
    # the first process exits. The checks track one process, so start in that mode.
    environment['QTWEBENGINE_DISABLE_SANDBOX'] = '1'
    return environment


def clean():
    shutil.rmtree(VERIFY_DATA, ignore_errors=True)
    # Chromium appends its warnings to debug.log beside the executable.
    (ROOT / 'dist' / 'Hongyan' / 'debug.log').unlink(missing_ok=True)
