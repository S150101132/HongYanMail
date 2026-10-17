"""Read-only structural inspection; does not execute the client or read mail bodies."""
from pathlib import Path
import collections, hashlib, json, re
import pefile

ROOT = Path(r'C:\Program Files\cmclient')
DATA = Path(r'C:\Users\wangbiao\AppData\Roaming\Cm\CMClient')
OUT = Path(__file__).resolve().parents[2] / 'analysis'
OUT.mkdir(exist_ok=True)
result = {'program_root': str(ROOT), 'data_root': str(DATA), 'binaries': [], 'web_modules': {}, 'bridge_commands': {}, 'data_structure': {}}
keywords = ['sqlite','wxwidgets','wxwindow','imap','smtp','pop3','coremail','openssl','gmssl','libcurl','libcef','cryptopp','sqlcipher','sqlitemc','boost','inno setup']
for name in ['CMClient.exe', 'CMCefApp.exe', 'libcef.dll', 'gmssl.dll', 'toolkits/sqlite3mc_shell.exe']:
    p = ROOT / name
    raw = p.read_bytes()
    pe = pefile.PE(data=raw, fast_load=True)
    pe.parse_data_directories(directories=[1,14])
    ascii_strings = [m.group().decode('ascii') for m in re.finditer(rb'[\x20-\x7e]{5,}', raw)]
    unicode_strings = [m.group().decode('utf-16le') for m in re.finditer(rb'(?:[\x20-\x7e]\x00){5,}', raw)]
    strings = ascii_strings + unicode_strings
    matches = {k:sum(k in s.lower() for s in strings) for k in keywords}
    item = {'file': name, 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'machine': hex(pe.FILE_HEADER.Machine), 'imports': [e.dll.decode(errors='replace') for e in getattr(pe, 'DIRECTORY_ENTRY_IMPORT',[])], 'clr_header_present': bool(pe.OPTIONAL_HEADER.DATA_DIRECTORY[14].VirtualAddress), 'keyword_string_counts': {k:v for k,v in matches.items() if v}}
    result['binaries'].append(item)
    pe.close()
for directory in sorted((ROOT/'app/template').iterdir()):
    if directory.is_dir():
        files = [p for p in directory.rglob('*') if p.is_file()]
        result['web_modules'][directory.name] = {'files': len(files), 'bytes':sum(p.stat().st_size for p in files), 'extensions': dict(collections.Counter(p.suffix.lower() or '[none]' for p in files))}
for p in (ROOT/'app').rglob('*.js'):
    if 'vendor' in p.parts:
        continue
    text = p.read_text(encoding='utf-8', errors='replace')
    commands = sorted(set(re.findall(r'\bcallPC\(\s*[\"\x27]([^\"\x27]+)[\"\x27]',text)))
    if commands:
        result['bridge_commands'][str(p.relative_to(ROOT))] = commands
# Only top-level structure and aggregate extension counts; no account IDs or message filenames.
for directory in DATA.iterdir():
    if directory.is_dir():
        files = [p for p in directory.rglob('*') if p.is_file()]
        result['data_structure'][directory.name] = {'files':len(files), 'bytes':sum(p.stat().st_size for p in files), 'extensions':dict(collections.Counter(p.suffix.lower() or '[none]' for p in files))}
# Identify only non-content database candidates by short signatures.
db_candidates=[]
for p in DATA.rglob('*'):
    if not p.is_file() or p.suffix.lower() in {'.html','.htm','.eml','.msg','.att','.enc','.log','.png','.jpg','.xlsx','.docx','.pdf'}:
        continue
    try:
        with p.open('rb') as handle:
            head = handle.read(16)
        if head.startswith(b'SQLite format 3\x00'):
            db_candidates.append({'id': len(db_candidates)+1, 'bytes':p.stat().st_size, 'format':'SQLite 3', 'top_level':p.relative_to(DATA).parts[0]})
    except OSError:
        pass
result['sqlite_header_matches'] = db_candidates
(OUT/'structure.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2))
