# Batch REA trace_feature runs for the interfaces that matter most.
# One CLI invocation per interface; each run imports the target (about 3 minutes).
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
$outDir = "$ws\analysis\rea-traces"
New-Item -ItemType Directory -Force -Path $outDir, $env:TEMP, $env:APPDATA, $env:LOCALAPPDATA | Out-Null

$targets = @(
  'user:login', 'user:getAttrs', 'user:getSignatures', 'user:getTempSession',
  'mbox:getAllFolders', 'mbox:compose', 'mbox:getMessageData', 'mbox:searchMessages',
  'mbox:updateMessageInfos', 'mbox:deleteMessages', 'upload:prepare', 'upload:directData',
  'oab:getHeadImageData', 'pab:getAllGroups', 'xnf:getConfig', 'cert:listPublicKey',
  'global:verify', 'cim.user:getLunkrMailData'
)

$log = "$ws\analysis\rea-traces\progress.log"
"start $(Get-Date -Format o) targets=$($targets.Count)" | Set-Content -Encoding UTF8 $log
foreach ($t in $targets) {
  $safe = ($t -replace '[:\\/]', '_')
  $file = Join-Path $outDir "$safe.json"
  $sw = [Diagnostics.Stopwatch]::StartNew()
  & npx -y rea-agents@6.5.0 trace $exe --query $t --format json --full-output *> $file
  $code = $LASTEXITCODE
  $size = (Get-Item $file -ErrorAction SilentlyContinue).Length
  "$t exit=$code bytes=$size seconds=$([int]$sw.Elapsed.TotalSeconds)" | Add-Content -Encoding UTF8 $log
}
"done $(Get-Date -Format o)" | Add-Content -Encoding UTF8 $log
