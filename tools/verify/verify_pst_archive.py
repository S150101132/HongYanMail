"""Read-only check of an Outlook .pst as a local-archive source: scans it into a throwaway
catalog inside the repo, then reads a sample back in full and parses it. Writes only
counts, failure reasons and timings to analysis/pst-scan.json (never names or content).
Usage: python -m tools.verify.verify_pst_archive <file.pst> [sample]"""
import collections
import json
import shutil
import sys
import time
from pathlib import Path

from coremail_desktop.archive.catalog import ArchiveCatalog
from coremail_desktop.mime.parser import parse_mime


class Plain:  # The throwaway catalog's key needs no DPAPI.
    protect = unprotect = staticmethod(lambda data: data)


def main(path, sample=150):
    work = Path('.tools') / 'pst-verify'
    shutil.rmtree(work, ignore_errors=True)
    try:
        catalog = ArchiveCatalog(work / 'a.archive.db', Plain())
        started = time.time()
        result = catalog.scan('', pst=path)
        report = {'scan_s': round(time.time() - started, 1), 'messages': result['messages'],
                  'failed': dict(collections.Counter(f['reason'] for f in result['failed'])),
                  'other_items': catalog.packs['outlook.pst'].get('other') if 'outlook.pst' in catalog.packs else None,
                  'encrypted': sum(r['encrypted'] for r in catalog.rows.values()),
                  'with_text': sum(bool(r['text']) for r in catalog.rows.values()),
                  'with_attachments': sum(bool(r['attachments']) for r in catalog.rows.values()),
                  'years': dict(sorted(collections.Counter(r['date'][:4] for r in catalog.rows.values()).items()))}
        started = time.time()
        rescan = catalog.scan('', pst=path)
        report['rescan_s'], report['rescan_skipped'] = round(time.time() - started, 2), rescan['skipped']
        rows = sorted(catalog.rows)
        picked = rows[::max(1, len(rows) // sample)][:sample]
        read = collections.Counter()
        times = []
        for mid in picked:
            started = time.time()
            try:
                doc = parse_mime(catalog.raw('', mid, pst=path), mid)
                read['ok'] += 1
                read['html' if doc.html else 'text' if doc.text else 'empty:' + doc.encryption_state] += 1
                read['attachments'] += len(doc.attachments)
            except Exception as error:
                read['error:' + getattr(error, 'code', type(error).__name__)] += 1
            times.append(time.time() - started)
        report['read'] = dict(read)
        report['read_ms_avg'] = round(1000 * sum(times) / max(1, len(times)))
        report['read_ms_max'] = round(1000 * max(times or [0]))
        catalog.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    Path('analysis').mkdir(exist_ok=True)
    Path('analysis/pst-scan.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 150)
