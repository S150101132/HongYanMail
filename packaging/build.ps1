$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $taskRoot
$python = Join-Path $taskRoot '.venv\Scripts\python.exe'

& $python -m PyInstaller --noconfirm packaging/desktop.spec
if ($LASTEXITCODE -ne 0) { throw 'Desktop packaging failed.' }

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
