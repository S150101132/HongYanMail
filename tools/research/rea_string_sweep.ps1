# Return the string address of every Coremail RPC literal in one program load.
# One CLI invocation covers all 138 interfaces, which is far cheaper than 138 traces.
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
$out = "$ws\analysis\rea-native\string-sweep.json"
$log = "$ws\analysis\rea-native\queue3.log"
"string sweep start $(Get-Date -Format o)" | Set-Content -Encoding UTF8 $log

$pattern = '[a-z][a-z0-9_]*\.[a-z0-9_]+:[A-Za-z][A-Za-z0-9_]*'
$sw = [Diagnostics.Stopwatch]::StartNew()
& npx -y rea-agents@6.5.0 search $exe --kind strings --mode regex --pattern $pattern --format json --full-output *> $out
"search exit=$LASTEXITCODE bytes=$((Get-Item $out -ErrorAction SilentlyContinue).Length) seconds=$([int]$sw.Elapsed.TotalSeconds)" | Add-Content -Encoding UTF8 $log
"string sweep done $(Get-Date -Format o)" | Add-Content -Encoding UTF8 $log
