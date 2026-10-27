"""Shape of an Outlook .pst (read-only): folder depth and counts, which bodies and headers
messages carry, message classes, attachment counts and timing. No names, subjects,
addresses or content are written. Usage: python -m tools.probes.probe_pst_shape <file.pst> [sample]"""
import collections
import json
import sys
import time
from pathlib import Path

import pypff

PR_MESSAGE_CLASS = 0x001A


def message_class(message):
    for record in message.record_sets:
        entry = record.get_entry_by_type(PR_MESSAGE_CLASS)
        if entry is not None:
            try:
                return entry.data_as_string or ''
            except Exception:
                return '?'
    return ''


def main(path, sample=300):
    started = time.time()
    pst = pypff.file()
    pst.open_file_object(open(path, 'rb'))  # libpff's own open mangles 'D:/…' paths.
    shape = {'size': pst.size, 'encryption': pst.encryption_type, 'opened_s': round(time.time() - started, 2),
             'folders': 0, 'messages': 0, 'max_depth': 0, 'folder_sizes': [], 'sample': {}}
    classes, bodies, counts = collections.Counter(), collections.Counter(), collections.Counter()
    sampled = []

    def walk(folder, depth):
        shape['folders'] += 1
        shape['max_depth'] = max(shape['max_depth'], depth)
        n = folder.number_of_sub_messages
        shape['messages'] += n
        if n:
            shape['folder_sizes'].append(n)
        for i in range(n):
            if len(sampled) >= sample or i % max(1, n // 20):
                continue
            t = time.time()
            try:
                m = folder.get_sub_message(i)
                classes[message_class(m)] += 1
                bodies['headers' if m.transport_headers else 'no_headers'] += 1
                bodies['html' if m.html_body else 'plain' if m.plain_text_body else 'rtf' if m.rtf_body else 'none'] += 1
                counts['attachments'] += m.number_of_attachments
                counts['recipients_sets'] += m.recipients.number_of_sub_items if m.recipients else 0
                sampled.append(time.time() - t)
            except Exception as error:
                counts['error:' + type(error).__name__] += 1
        for i in range(folder.number_of_sub_folders):
            walk(folder.get_sub_folder(i), depth + 1)

    walk(pst.root_folder, 0)
    shape['folder_sizes'] = sorted(shape['folder_sizes'], reverse=True)[:20]
    shape['sample'] = {'n': len(sampled), 'avg_ms': round(1000 * sum(sampled) / max(1, len(sampled)), 1),
                       'classes': dict(classes), 'bodies': dict(bodies), 'counts': dict(counts)}
    shape['total_s'] = round(time.time() - started, 1)
    out = Path('analysis') / 'pst_shape.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(shape, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(shape, ensure_ascii=False))


if __name__ == '__main__':
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 300)
