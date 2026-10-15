# Diagnostic twin of desktop.spec: identical dependency trimming, but it builds
# the console entry point packaging/entry_probe.py so a missing DLL reports a
# traceback instead of failing silently inside a windowed process.
#
# packaging/bisect_qt_dlls.py uses this spec when re-verifying file exclusions.
# It is not shipped to users.
#
# The interface is Qt Widgets with an isolated Qt WebEngine reading pane. The
# excluded file list below is not guessed: packaging/bisect_qt_dlls.py removed
# each candidate from a known-good bundle and re-ran the console diagnostic, and
# every file listed here was proven unnecessary. The Qt6Qml/Qt6Quick/Positioning/
# WebChannel stack and the whole PySide6/qml tree are deliberately KEPT, because
# QtWebEngineCore needs them (an earlier narrower trim broke its DLL load).
#
# Re-verify after every dependency change with packaging/build.ps1, which runs the
# WebEngine, privacy and startup checks in sequence.
from pathlib import Path

from PyInstaller.building.datastruct import TOC

root = Path(SPECPATH).parent

EXCLUDED_MODULES = [
    'PySide6.QtQuick3D', 'PySide6.QtQuickControls2', 'PySide6.Qt3DCore', 'PySide6.Qt3DRender',
    'PySide6.Qt3DInput', 'PySide6.Qt3DLogic', 'PySide6.Qt3DAnimation', 'PySide6.Qt3DExtras',
    'PySide6.QtCharts', 'PySide6.QtGraphs', 'PySide6.QtDataVisualization',
    'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets', 'PySide6.QtDesigner',
    'PySide6.QtUiTools', 'PySide6.QtTest', 'PySide6.QtSql', 'PySide6.QtBluetooth',
    'PySide6.QtNfc', 'PySide6.QtSerialPort', 'PySide6.QtSerialBus', 'PySide6.QtRemoteObjects',
    'PySide6.QtScxml', 'PySide6.QtSensors', 'PySide6.QtSpatialAudio', 'PySide6.QtTextToSpeech',
    'PySide6.QtWebSockets', 'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets', 'PySide6.QtPdf',
    'PySide6.QtPdfWidgets', 'PySide6.QtNetworkAuth', 'PySide6.QtHelp', 'PySide6.QtLocation',
    'tkinter', 'unittest', 'pydoc', 'doctest', 'test',
]

# Proven-removable files (see analysis/bundle-trim-experiment.json).
PROVEN_REMOVABLE = [
    'opengl32sw.dll', 'QtOpenGL.pyd',
    'Qt6Charts.dll', 'Qt6ChartsQml.dll', 'Qt6DataVisualization.dll', 'Qt6DataVisualizationQml.dll',
    'Qt6Graphs.dll',
    'Qt6Quick3D.dll', 'Qt6Quick3DAssetImport.dll', 'Qt6Quick3DAssetUtils.dll', 'Qt6Quick3DEffects.dll',
    'Qt6Quick3DHelpers.dll', 'Qt6Quick3DHelpersImpl.dll', 'Qt6Quick3DParticleEffects.dll',
    'Qt6Quick3DParticles.dll', 'Qt6Quick3DPhysics.dll', 'Qt6Quick3DPhysicsHelpers.dll',
    'Qt6Quick3DRuntimeRender.dll', 'Qt6Quick3DSpatialAudio.dll', 'Qt6Quick3DUtils.dll',
    'Qt6Quick3DXr.dll',
    'Qt6LabsAnimation.dll', 'Qt6LabsFolderListModel.dll', 'Qt6LabsPlatform.dll',
    'Qt6LabsQmlModels.dll', 'Qt6LabsSettings.dll', 'Qt6LabsSharedImage.dll', 'Qt6LabsStyleKit.dll',
    'Qt6LabsStyleKitImpl.dll', 'Qt6LabsSynchronizer.dll', 'Qt6LabsWavefrontMesh.dll',
    'Qt6Location.dll', 'Qt6Multimedia.dll', 'Qt6MultimediaQuick.dll', 'Qt6OpenGL.dll',
    'Qt6OpenGLWidgets.dll', 'Qt6Pdf.dll', 'Qt6PdfQuick.dll', 'Qt6RemoteObjects.dll',
    'Qt6RemoteObjectsQml.dll', 'Qt6Scxml.dll', 'Qt6ScxmlQml.dll', 'Qt6Sensors.dll',
    'Qt6SensorsQuick.dll', 'Qt6SerialPort.dll', 'Qt6ShaderTools.dll', 'Qt6SpatialAudio.dll',
    'Qt6Sql.dll', 'Qt6Test.dll', 'Qt6TextToSpeech.dll', 'Qt6VirtualKeyboard.dll',
    'Qt6VirtualKeyboardQml.dll', 'Qt6VirtualKeyboardSettings.dll', 'Qt6WebSockets.dll',
]

a = Analysis(
    [str(root / 'packaging' / 'entry_probe.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[],
    hiddenimports=['PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineCore'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDED_MODULES,
    noarchive=False,
)

_REMOVABLE_NAMES = {name.casefold() for name in PROVEN_REMOVABLE}


def _keep(entry):
    target = entry[0].replace('\\', '/')
    if '/translations/' in target:
        return False
    return Path(target).name.casefold() not in _REMOVABLE_NAMES


a.binaries = TOC([entry for entry in a.binaries if _keep(entry)])
a.datas = TOC([entry for entry in a.datas if '/translations/' not in entry[0].replace('\\', '/')])

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='CoremailEntryProbe',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='CoremailEntryProbe')

