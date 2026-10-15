$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $taskRoot
& (Join-Path $taskRoot '.venv\Scripts\python.exe') -m PyInstaller --noconfirm packaging/desktop.spec
if ($LASTEXITCODE -ne 0) { throw 'Desktop packaging failed.' }
