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

& $python -u packaging/smoke_check.py
if ($LASTEXITCODE -ne 0) { throw 'Packaged application exited before the startup window elapsed.' }

& $python -u packaging/verify_neutral_directory.py
if ($LASTEXITCODE -ne 0) { throw 'Packaged application failed to start from a neutral working directory.' }

& $python -u packaging/verify_scaling.py
if ($LASTEXITCODE -ne 0) { throw 'Shell layout failed at one of the required display scale factors.' }

# Audit after every launch check, so nothing a demo run left in dist can ship.
& $python -u packaging/audit_distribution.py
if ($LASTEXITCODE -ne 0) { throw 'Distribution audit found personal or research data.' }

Write-Output 'Build verified: WebEngine reader, startup smoke check, neutral-directory launch, display scaling and privacy audit all passed.'

# Per-user installer (Inno Setup 6, packaging/hongyan.iss). Optional: skipped with a
# note when ISCC.exe is not installed. Set $env:ISCC to point at a specific copy.
$iscc = @($env:ISCC,
          (Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source),
          (Join-Path $taskRoot '.tools\InnoSetup\ISCC.exe'),
          (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
          (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
          (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')) |
        Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if ($iscc) {
    $version = (Select-String -LiteralPath (Join-Path $taskRoot 'pyproject.toml') -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
    & $iscc /Q "/DAppVersion=$version" (Join-Path $taskRoot 'packaging\hongyan.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
    # Machines that forbid writing %TEMP% need this wrapper to run the installer.
    Copy-Item -LiteralPath (Join-Path $taskRoot 'packaging\Install-Hongyan.cmd') -Destination (Join-Path $taskRoot 'dist') -Force
    Write-Output "Installer: dist\Hongyan-Setup-$version.exe (run dist\Install-Hongyan.cmd on locked-down machines)"
}
else {
    Write-Warning 'Inno Setup 6 (ISCC.exe) not found; installer not built. See packaging/hongyan.iss.'
}
