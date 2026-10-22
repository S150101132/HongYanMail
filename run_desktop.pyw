"""Start 鸿雁 without a console window: the desktop shortcut runs the installed
pythonw.exe on this file (see run_desktop.cmd for why not the .venv copy)."""
import os, sys
from pathlib import Path
root=Path(__file__).resolve().parent
site=root / '.venv' / 'Lib' / 'site-packages'
if site.is_dir() and str(site) not in sys.path:
    sys.path.insert(0,str(site))
    # The sandbox relaunch starts `python -m coremail_desktop` and needs the same packages.
    os.environ['PYTHONPATH']=os.pathsep.join(filter(None,[str(site),os.environ.get('PYTHONPATH')]))
sys.path.insert(0,str(root)); os.chdir(root)
from coremail_desktop.bootstrap import main
raise SystemExit(main())
