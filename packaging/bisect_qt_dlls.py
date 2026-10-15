"""Bisect which Qt DLLs the real application can do without.

Copies the known-good bundle once, then removes candidate Qt DLLs one at a time
and starts the real GUI entry point. A candidate is removable when the process is
still alive after the startup window; otherwise the DLL is restored. Results are
written to analysis/bundle-trim-experiment.json.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOOD = ROOT / 'dist' / '_previous'
SANDBOX = ROOT / 'runtime' / '_bisect'
REPORT = ROOT / 'analysis' / 'bundle-trim-experiment.json'
# The packaged entry point is windowed, so a failed import shows a dialog and the
# process stays alive. A per-launch log written from a PyInstaller runtime hook
# makes the verdict independent of the process staying up.
HOOK = ROOT / 'runtime' / '_launch_hook.py'
HOOK_TEMPLATE = '''import os, sys, traceback
LOG = os.environ.get('COREMAIL_LAUNCH_LOG')
if LOG:
    try:
        with open(LOG, 'w', encoding='utf-8') as stream:
            stream.write('hook reached\\n')
            stream.flush()
            try:
                from PySide6.QtWebEngineCore import QWebEngineProfile  # noqa: F401
                from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
                from coremail_desktop.reader.view import ReaderView  # noqa: F401
                stream.write('ALL OK\\n')
            except BaseException:
                stream.write(traceback.format_exc())
            stream.flush()
    except OSError:
        pass
'''

CANDIDATES = [
    'Qt6Charts.dll', 'Qt6ChartsQml.dll', 'Qt6DataVisualization.dll', 'Qt6DataVisualizationQml.dll',
    'Qt6Graphs.dll', 'Qt6Quick3D.dll', 'Qt6Quick3DRuntimeRender.dll', 'Qt6Quick3DPhysics.dll',
    'Qt6Quick3DAssetImport.dll', 'Qt6Quick3DAssetUtils.dll', 'Qt6Quick3DEffects.dll',
    'Qt6Quick3DHelpers.dll', 'Qt6Quick3DHelpersImpl.dll', 'Qt6Quick3DParticleEffects.dll',
    'Qt6Quick3DParticles.dll', 'Qt6Quick3DPhysicsHelpers.dll', 'Qt6Quick3DSpatialAudio.dll',
    'Qt6Quick3DUtils.dll', 'Qt6Quick3DXr.dll', 'Qt6Multimedia.dll', 'Qt6MultimediaQuick.dll',
    'Qt6Sql.dll', 'Qt6Test.dll', 'Qt6TextToSpeech.dll', 'Qt6SpatialAudio.dll',
    'Qt6VirtualKeyboard.dll', 'Qt6VirtualKeyboardQml.dll', 'Qt6VirtualKeyboardSettings.dll',
    'Qt6WebSockets.dll', 'Qt6RemoteObjects.dll', 'Qt6RemoteObjectsQml.dll', 'Qt6Scxml.dll',
    'Qt6ScxmlQml.dll', 'Qt6Sensors.dll', 'Qt6SensorsQuick.dll', 'Qt6SerialPort.dll',
    'Qt6Location.dll', 'Qt6Pdf.dll', 'Qt6PdfQuick.dll', 'Qt6ShaderTools.dll', 'Qt6OpenGL.dll',
    'Qt6OpenGLWidgets.dll', 'Qt6LabsAnimation.dll', 'Qt6LabsFolderListModel.dll',
    'Qt6LabsPlatform.dll', 'Qt6LabsQmlModels.dll', 'Qt6LabsSettings.dll', 'Qt6LabsSharedImage.dll',
    'Qt6LabsStyleKit.dll', 'Qt6LabsStyleKitImpl.dll', 'Qt6LabsSynchronizer.dll',
    'Qt6LabsWavefrontMesh.dll', 'opengl32sw.dll', 'QtOpenGL.pyd', 'QtQuickWidgets.pyd',
]


def prepare():
    app = SANDBOX / 'app'
    if not app.is_dir():
        if SANDBOX.exists():
            shutil.rmtree(SANDBOX)
        SANDBOX.mkdir(parents=True)
        print('copying known-good bundle once...', flush=True)
        shutil.copytree(GOOD, app)
    # Drop in the console diagnostic so the verdict does not depend on a windowed
    # process staying alive.
    probe = ROOT / 'dist_probe' / 'CoremailEntryProbe' / 'CoremailEntryProbe.exe'
    if probe.is_file():
        shutil.copy2(probe, app / 'CoremailEntryProbe.exe')
    return app


def survives(app):
    """Run the console diagnostic inside this bundle and read its verdict."""
    internal = app / '_internal'
    trace = ROOT / 'runtime' / 'trace.log'
    trace.unlink(missing_ok=True)
    environment = dict(os.environ)
    environment['QT_QPA_PLATFORM'] = 'offscreen'
    environment['COREMAIL_PROBE_LOG'] = str(trace)
    executable = app / 'CoremailEntryProbe.exe'
    if not executable.exists():
        return False, 'diagnostic executable missing'
    process = subprocess.Popen([str(executable)], cwd=str(app), env=environment,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 60
    while time.time() < deadline:
        if process.poll() is not None:
            break
        time.sleep(0.4)
    if process.poll() is None:
        process.kill()
        process.wait()
    text = trace.read_text(encoding='utf-8', errors='replace') if trace.exists() else ''
    if 'ALL OK' in text:
        return True, 'ok'
    return False, (text.strip().splitlines()[-1] if text.strip() else 'no trace')


if __name__ == '__main__':
    app = prepare()
    side = app / '_internal' / 'PySide6'
    removable, required = [], {}
    for name in CANDIDATES:
        target = side / name
        if not target.exists():
            continue
        backup = side / (name + '.bak')
        target.rename(backup)
        ok, code = survives(app)
        if ok:
            removable.append(name)
            backup.unlink()
            print(f'REMOVABLE  {name}', flush=True)
        else:
            backup.rename(target)
            required[name] = code
            print(f'REQUIRED   {name} (exit {code})', flush=True)
        REPORT.write_text(json.dumps({'removable': removable, 'required': required},
                                     ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'removable': removable, 'required': required}, ensure_ascii=False, indent=2))
