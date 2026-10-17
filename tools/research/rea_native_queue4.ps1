# Restart the native queue from the auth-cluster decompiles (the address sweep is done
# locally and validated, so it is no longer needed here).
$ErrorActionPreference = 'Continue'
$ws = 'D:\workspace\redocoreemail'
$env:GHIDRA_INSTALL_DIR = "$ws\.tools\ghidra_12.1.4_PUBLIC"
$env:JAVA_HOME = 'D:\Program Files\Java\jdk-21.0.9'
$env:REA_ANALYSIS_PROVIDER = 'ghidra'
$env:REA_GHIDRA_STARTUP_TIMEOUT_MS = '3600000'
$env:REA_GHIDRA_DISABLED_ANALYZERS = 'Decompiler Parameter ID,Aggressive Instruction Finder,Non-Returning Functions - Discovered,Non-Returning Functions - Known,Decompiler Switch Analysis,Embedded Media'
$env:npm_config_cache = "$ws\.npm-cache"
$env:TEMP = "$ws\.rea-tmp"; $env:TMP = $env:TEMP
$env:APPDATA = "$ws\.tools\appdata"; $env:LOCALAPPDATA = "$ws\.tools\localappdata"

$exe = 'C:\Program Files\cmclient\CMClient.exe'
$outDir = "$ws\analysis\rea-native"
$log = "$ws\analysis\rea-native\queue4.log"
"queue4 start $(Get-Date -Format o)" | Set-Content -Encoding UTF8 $log

# largest auth functions first: they carry the login/device-binding/2FA logic
$decompiles = @(
  @{ addr = '0x140cff190'; name = 'auth_device_bind' },
  @{ addr = '0x140cfca00'; name = 'auth_login_state' },
  @{ addr = '0x140d08140'; name = 'auth_token_after_a' },
  @{ addr = '0x140d08fd0'; name = 'auth_token_after_b' },
  @{ addr = '0x140cf74f0'; name = 'auth_token_field' }
)
foreach ($f in $decompiles) {
  $file = Join-Path $outDir "$($f.name).json"
  $sw = [Diagnostics.Stopwatch]::StartNew()
  & npx -y rea-agents@6.5.0 decompile $exe --procedure $f.addr --format json --full-output *> $file
  "decompile $($f.addr) $($f.name) exit=$LASTEXITCODE bytes=$((Get-Item $file -ErrorAction SilentlyContinue).Length) seconds=$([int]$sw.Elapsed.TotalSeconds)" | Add-Content -Encoding UTF8 $log
}
"queue4 done $(Get-Date -Format o)" | Add-Content -Encoding UTF8 $log
