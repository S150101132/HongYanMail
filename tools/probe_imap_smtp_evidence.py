"""What are the Imap/Pop3/Smtp mentions in the client logs?

The objective requires separating standard IMAP/SMTP from the Coremail private
protocol by evidence, not by the presence of library strings. This probe reports
the redacted shape of every log line mentioning those names, so we can tell a
protocol attempt from a UI label or a stored configuration value.
"""
from __future__ import annotations

import collections
import json
import os
import re

LOGS = r"C:\Users\wangbiao\AppData\Roaming\Cm\CMClient\logs"
OUT = r"D:\workspace\redocoreemail\analysis\imap-smtp-evidence.json"

RE_HIT = re.compile(r"(?i)(imap4?rev1?|imap|pop3|smtp|ews)")
RE_WORD = re.compile(r"(?i)\b(imap4?rev1?|imap|pop3|smtp|ews)\b")
RE_BLOB = re.compile(r"^[A-Za-z0-9+/=_\-]{60,}$")
RE_TOK = re.compile(r"[A-Za-z0-9+/=_\-]{16,}")
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
RE_TS = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}[.,]?\d*\s*")

shapes = collections.Counter()
word_hits = collections.Counter()
by_word = collections.Counter()
per_file = collections.Counter()
examples = {}

for name in sorted(os.listdir(LOGS)):
    path = os.path.join(LOGS, name)
    if not os.path.isfile(path):
        continue
    text = open(path, "rb").read().decode("utf-8", "ignore")
    for line in text.splitlines():
        if not RE_HIT.search(line):
            continue
        cleaned = RE_TS.sub("", line)
        cleaned = RE_EMAIL.sub("<email>", cleaned)
        cleaned = RE_TOK.sub("<tok>", cleaned)
        # collapse digits so that ids/timestamps do not multiply the shapes
        shape = re.sub(r"\d+", "N", cleaned)[:180]
        shapes[shape] += 1
        per_file[name] += 1
        if RE_WORD.search(cleaned):
            word_hits[shape] += 1
        for word in set(w.lower() for w in RE_HIT.findall(line)):
            by_word[word] += 1
        examples.setdefault(shape, cleaned[:200])

report = {
    "scope": "log lines mentioning imap/pop3/smtp/ews; redacted shapes only",
    "lines_matched": sum(shapes.values()),
    "by_word": dict(by_word.most_common()),
    "by_file": dict(per_file.most_common(10)),
    "shapes": [{"shape": s, "count": c} for s, c in shapes.most_common(25)],
    "word_boundary_shapes": [{"shape": s, "count": c} for s, c in word_hits.most_common(25)],
}
json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print("substring-matched lines:", sum(shapes.values()), "words:", dict(by_word.most_common()))
print("lines with a real word boundary:", sum(word_hits.values()))
print("\n--- top shapes ---")
for s, c in shapes.most_common(25):
    print(f"{c:5}  {s}")
print("\nwritten:", OUT)
