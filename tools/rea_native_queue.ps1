# Priority queue for REA native work on CMClient.exe.
# Phase A: decompile the login state machine and the shared RPC transport candidates.
# Phase B: locate the referencing functions of the remaining high-value interfaces.
$ErrorActionPreference = 'Continue'
$ws = 'D:\workspace\redocoreemail'
$env:GHIDRA_INSTALL_DIR = "$ws\.tools\ghidra_12.1.4_PUBLIC"
$env:JAVA_HOME = 'D:\Program Files\Java\jdk-21.0.9'
$env:REA_ANALYSIS_PROVIDER = 'ghidra'
$env:REA_GHIDRA_STARTUP_TIMEOUT_MS = '3600000'
$env:REA_GHIDRA_DISABLED_ANALYZERS = 'Decompiler Parameter ID,Aggressive Instruction Finder,Non-Returning Functions - Discovered,Non-Returning Functions - Known,Decompiler Switch Analysis'
$env:npm_config_cache = "$ws\.npm-cache"
$env:TEMP = "$ws\.rea-tmp"; $env:TMP = $env:TEMP
$env:APPDATA = "$ws\.tools\appdata"; $env:LOCALAPPDATA = "$ws\.tools\localappdata"

$exe = 'C:\Program Files\cmclient\CMClient.exe'
$outDir = "$ws\analysis\rea-native"
$traceDir = "$ws\analysis\rea-traces"
New-Item -ItemType Directory -Force -Path $outDir, $traceDir, $env:TEMP, $env:APPDATA, $env:LOCALAPPDATA | Out-Null
$log = "$ws\analysis\rea-native\queue.log"
"queue start $(Get-Date -Format o)" | Set-Content -Encoding UTF8 $log

$functions = @(
  @{ addr = '0x140cfca00'; name = 'login_state_machine'; mode = 'function' },
  @{ addr = '0x140ce70a0'; name = 'rpc_transport_a';     mode = 'decompile' },
  @{ addr = '0x140d06190'; name = 'rpc_transport_b';     mode = 'decompile' }
)
foreach ($f in $functions) {
  $file = Join-Path $outDir "$($f.name).json"
  $sw = [Diagnostics.Stopwatch]::StartNew()
  & npx -y rea-agents@6.5.0 $f.mode $exe --procedure $f.addr --format json --full-output *> $file
  "$($f.mode) $($f.addr) $($f.name) exit=$LASTEXITCODE bytes=$((Get-Item $file -ErrorAction SilentlyContinue).Length) seconds=$([int]$sw.Elapsed.TotalSeconds)" | Add-Content -Encoding UTF8 $log
}

$targets = @(
  'mbox:getAllFolders', 'mbox:compose', 'mbox:getMessageData', 'mbox:searchMessages',
  'mbox:updateMessageInfos', 'mbox:deleteMessages', 'upload:prepare', 'upload:directData',
  'oab:getHeadImageData', 'pab:getAllGroups', 'xnf:getConfig', 'cert:listPublicKey'
)
foreach ($t in $targets) {
  $safe = ($t -replace '[:\\/]', '_')
  $file = Join-Path $traceDir "$safe.json"
  $sw = [Diagnostics.Stopwatch]::StartNew()
  & npx -y rea-agents@6.5.0 trace $exe --query $t --format json --full-output *> $file
  "trace $t exit=$LASTEXITCODE bytes=$((Get-Item $file -ErrorAction SilentlyContinue).Length) seconds=$([int]$sw.Elapsed.TotalSeconds)" | Add-Content -Encoding UTF8 $log
}
"queue done $(Get-Date -Format o)" | Add-Content -Encoding UTF8 $log
