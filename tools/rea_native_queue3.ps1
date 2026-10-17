# Reordered native queue: the one-pass string sweep goes FIRST.
# Rationale: one program load covers every RPC literal (address-level evidence for
# all 138 interfaces), which is far better value per 10-13 minute invocation than
# any single-interface trace. Auth-cluster decompiles come next, then traces.
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
$traceDir = "$ws\analysis\rea-traces"
New-Item -ItemType Directory -Force -Path $outDir, $traceDir, $env:TEMP, $env:APPDATA, $env:LOCALAPPDATA | Out-Null
$log = "$ws\analysis\rea-native\queue3.log"
"queue3 start $(Get-Date -Format o)" | Set-Content -Encoding UTF8 $log

# 1) one-pass sweep over every RPC literal
$sw = [Diagnostics.Stopwatch]::StartNew()
& npx -y rea-agents@6.5.0 search $exe --kind strings --mode regex `
    --pattern '[a-z][a-z0-9_]*\.[a-z0-9_]+:[A-Za-z][A-Za-z0-9_]*' --format json --full-output *> "$outDir\string-sweep.json"
"sweep exit=$LASTEXITCODE bytes=$((Get-Item "$outDir\string-sweep.json" -ErrorAction SilentlyContinue).Length) seconds=$([int]$sw.Elapsed.TotalSeconds)" | Add-Content -Encoding UTF8 $log

# 2) auth cluster decompiles
$decompiles = @(
  @{ addr = '0x140cf74f0'; name = 'auth_token_field' },
  @{ addr = '0x140cff190'; name = 'auth_device_bind' },
  @{ addr = '0x140cfca00'; name = 'auth_login_state' },
  @{ addr = '0x140d08140'; name = 'auth_token_after_a' },
  @{ addr = '0x140d08fd0'; name = 'auth_token_after_b' }
)
foreach ($f in $decompiles) {
  $file = Join-Path $outDir "$($f.name).json"
  $sw = [Diagnostics.Stopwatch]::StartNew()
  & npx -y rea-agents@6.5.0 decompile $exe --procedure $f.addr --format json --full-output *> $file
  "decompile $($f.addr) $($f.name) exit=$LASTEXITCODE bytes=$((Get-Item $file -ErrorAction SilentlyContinue).Length) seconds=$([int]$sw.Elapsed.TotalSeconds)" | Add-Content -Encoding UTF8 $log
}

# 3) interface traces, ordered by the project's focus areas
$targets = @(
  'mbox:compose', 'mbox:getMessageData', 'upload:directData', 'mbox:getAllFolders',
  'oab:get', 'pab:getContacts', 'mbox:recallMessage2', 'user:listDeliveryHistory',
  'mbox:searchMessages', 'mbox:updateMessageInfos', 'upload:prepare', 'oab:getHeadImageData'
)
foreach ($t in $targets) {
  $safe = ($t -replace '[:\\/]', '_')
  $sw = [Diagnostics.Stopwatch]::StartNew()
  & npx -y rea-agents@6.5.0 trace $exe --query $t --format json --full-output *> (Join-Path $traceDir "$safe.json")
  "trace $t exit=$LASTEXITCODE seconds=$([int]$sw.Elapsed.TotalSeconds)" | Add-Content -Encoding UTF8 $log
}
"queue3 done $(Get-Date -Format o)" | Add-Content -Encoding UTF8 $log
