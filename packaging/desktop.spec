# Only application sources and runtime dependencies; no research or account files.
from pathlib import Path
root = Path(SPECPATH).parent
a = Analysis([str(root / 'packaging' / 'entry.py')], pathex=[str(root)], binaries=[], datas=[], hiddenimports=['PySide6.QtWebEngineWidgets','PySide6.QtWebEngineCore'], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['tkinter'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,[],exclude_binaries=True,name='CoremailDesktopPreview',debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False)
coll = COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='CoremailDesktopPreview')
