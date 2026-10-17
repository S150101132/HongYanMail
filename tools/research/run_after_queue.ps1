# Chain the string sweep after the running native queue so the two never contend
# for the same machine. Waits for the queue's completion marker, then sweeps.
$ws = 'D:\workspace\redocoreemail'
$queueLog = "$ws\analysis\rea-native\queue2.log"
$chainLog = "$ws\analysis\rea-native\chain.log"
"chain start $(Get-Date -Format o)" | Set-Content -Encoding UTF8 $chainLog

$deadline = (Get-Date).AddHours(6)
while ((Get-Date) -lt $deadline) {
  if ((Test-Path $queueLog) -and (Select-String -Path $queueLog -Pattern 'queue2 done' -Quiet)) {
    "queue2 finished, starting string sweep $(Get-Date -Format o)" | Add-Content -Encoding UTF8 $chainLog
    break
  }
  Start-Sleep -Seconds 30
}
if ((Get-Date) -ge $deadline) {
  "chain timeout before queue2 finished $(Get-Date -Format o)" | Add-Content -Encoding UTF8 $chainLog
  return
}
pwsh -NoProfile -File "$ws\tools\rea_string_sweep.ps1"
"chain done $(Get-Date -Format o)" | Add-Content -Encoding UTF8 $chainLog
