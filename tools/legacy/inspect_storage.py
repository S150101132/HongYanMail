"""Read storage layout and format signatures, never message contents or secrets."""
from pathlib import Path
from collections import Counter
import json, sqlite3

root = Path(r'D:\work\email\wangbiao@inspur.com')
out = Path(__file__).resolve().parents[2] / 'analysis'
result = {'root': str(root), 'directories': {}, 'database_candidates': [], 'format_samples': []}
for child in sorted(root.iterdir()):
    if child.is_dir():
        files = [p for p in child.rglob('*') if p.is_file()]
        result['directories'][child.name] = {'files': len(files), 'bytes':sum(p.stat().st_size for p in files), 'extensions':dict(Counter(p.suffix or '[none]' for p in files))}
for p in sorted(root.glob('*.edbx2')):
    with p.open('rb') as f:
        header = f.read(32)
    item = {'name':p.name, 'bytes':p.stat().st_size, 'first_16_hex':header[:16].hex(), 'sqlite_plaintext_header':header.startswith(b'SQLite format 3\x00'), 'size_divisible_by_4096':p.stat().st_size % 4096 == 0}
    if item['sqlite_plaintext_header']:
        try:
            con = sqlite3.connect(p.as_uri()+'?mode=ro', uri=True, timeout=1)
            con.execute('PRAGMA query_only=ON')
            item['schema'] = [{'type':r[0], 'name':r[1], 'sql':r[2]} for r in con.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")]
            con.close()
        except sqlite3.Error as exc:
            item['schema_read_error'] = str(exc)
    result['database_candidates'].append(item)
for directory in ['mails','archmails','search','backup','backup_second']:
    files = sorted(p for p in (root/directory).rglob('*') if p.is_file())
    byext = {}
    for p in files:
        byext.setdefault(p.suffix or '[none]', []).append(p)
    for ext, group in byext.items():
        for p in group[:2]:
            with p.open('rb') as f:
                header = f.read(16)
            result['format_samples'].append({'directory':directory,'extension':ext,'sample_number':len(result['format_samples'])+1,'bytes':p.stat().st_size,'first_16_hex':header.hex(),'sqlite_header':header.startswith(b'SQLite format 3\x00'),'gzip_header':header.startswith(b'\x1f\x8b'),'zip_header':header.startswith(b'PK\x03\x04')})
out.mkdir(exist_ok=True)
(out/'storage-structure.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2))
