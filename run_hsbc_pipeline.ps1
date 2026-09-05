$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

$logFile = Join-Path $repoRoot 'hsbc_pipeline.log'
$timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

"[$timestamp] Starting HSBC statement sync" | Out-File -Append -Encoding utf8 $logFile

try {
    python .\email_attachments_to_dropbox.py 2>&1 | Tee-Object -FilePath $logFile -Append
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    "[$timestamp] HSBC statement sync completed successfully" | Out-File -Append -Encoding utf8 $logFile
}
catch {
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    "[$timestamp] HSBC statement sync failed: $($_.Exception.Message)" | Out-File -Append -Encoding utf8 $logFile
    throw
}
