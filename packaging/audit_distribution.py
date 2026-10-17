"""Distribution audit: the trial build must carry no personal or research data.

Checks the packaged onedir output for certificate material, mail samples,
account/session artifacts and reverse-engineering tooling, and reports the
bundle size and file count for the acceptance record.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / 'dist' / 'Hongyan'
REPORT = ROOT / 'analysis' / 'distribution-audit.json'

FORBIDDEN_SUFFIXES = {'.pfx', '.p12', '.key', '.pem', '.eml', '.msg', '.edbx2', '.cmpack'}
FORBIDDEN_NAME_PARTS = ('ghidra', 'rea-javascript', 'rea-mcp', 'native-auth', 'inspect_client',
                        'inspect_storage', 'coremail_desktop_preview.json')
FORBIDDEN_DIRS = ('analysis', 'probe', 'tests', 'docs', '.git', '.superpowers', '.tools', '.rea-tmp')


def audit():
    if not DIST.is_dir():
        return {'status': 'missing', 'dist': str(DIST)}
    files = [path for path in DIST.rglob('*') if path.is_file()]
    size = sum(path.stat().st_size for path in files)
    suffix_hits = sorted(str(path.relative_to(DIST)) for path in files
                         if path.suffix.lower() in FORBIDDEN_SUFFIXES)
    name_hits = sorted(str(path.relative_to(DIST)) for path in files
                       if any(part in path.name.lower() for part in FORBIDDEN_NAME_PARTS))
    dir_hits = sorted({path.name for path in DIST.rglob('*')
                       if path.is_dir() and path.name in FORBIDDEN_DIRS})
    return {
        'status': 'ok' if not (suffix_hits or name_hits or dir_hits) else 'failed',
        'dist': str(DIST),
        'file_count': len(files),
        'size_bytes': size,
        'size_mb': round(size / 1024 / 1024, 1),
        'forbidden_suffix_hits': suffix_hits[:20],
        'forbidden_name_hits': name_hits[:20],
        'forbidden_dir_hits': dir_hits,
        'checked_suffixes': sorted(FORBIDDEN_SUFFIXES),
        'note': 'Qt-shipped resources such as qml/designer images are expected and not personal data.',
    }


if __name__ == '__main__':
    result = audit()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get('status') == 'ok' else 1)
