$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $taskRoot
$python = Join-Path $taskRoot '.venv\Scripts\python.exe'
& $python -m tools.build_brand_icon
if ($LASTEXITCODE -ne 0) { throw 'Hongyan icon generation failed.' }

# Native tools installed by the host can prepend an incompatible icuuc.dll to
# PATH. Qt expects the Windows ICU ABI; do not collect a Poppler/LibreOffice ICU.
# Restore the caller's environment after the build, and invalidate old analysis
# caches that may already contain the wrong native-library source path.
$taskOriginalPath = $env:PATH
try {
    $env:PATH = (Join-Path $taskRoot '.venv\Scripts') + ';' + (Join-Path $env:WINDIR 'System32') + ';' + $env:WINDIR
    & $python -m PyInstaller --clean --noconfirm packaging/desktop.spec
    $taskBuildExit = $LASTEXITCODE
}
finally {
    $env:PATH = $taskOriginalPath
}
if ($taskBuildExit -ne 0) { throw 'Desktop packaging failed.' }

# A trimmed bundle can start and still fail to load Qt WebEngine, so verify the
# reader and the privacy boundary before calling the build good.
& $python -u packaging/verify_webengine.py
if ($LASTEXITCODE -ne 0) { throw 'Packaged Qt WebEngine reader failed to initialize.' }

& $python -u packaging/audit_distribution.py
if ($LASTEXITCODE -ne 0) { throw 'Distribution audit found personal or research data.' }

& $python -u packaging/smoke_check.py
if ($LASTEXITCODE -ne 0) { throw 'Packaged application exited before the startup window elapsed.' }

& $python -u packaging/verify_neutral_directory.py
if ($LASTEXITCODE -ne 0) { throw 'Packaged application failed to start from a neutral working directory.' }

& $python -u packaging/verify_scaling.py
if ($LASTEXITCODE -ne 0) { throw 'Shell layout failed at one of the required display scale factors.' }

Write-Output 'Build verified: WebEngine reader, privacy audit, startup smoke check, neutral-directory launch and display scaling all passed.'
