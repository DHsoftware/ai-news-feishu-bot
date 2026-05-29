$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Host "Start daily AI news run in: $ProjectRoot"

try {
    Write-Host "Running git pull..."
    git pull
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "git pull failed (exit code $LASTEXITCODE). Continue with local JSON cache."
    } else {
        Write-Host "git pull succeeded."
    }
} catch {
    Write-Warning "git pull failed with exception. Continue with local JSON cache. Error: $($_.Exception.Message)"
}

python scripts\daily_ai_news.py
$ScriptExitCode = $LASTEXITCODE

if ($ScriptExitCode -ne 0) {
    Write-Error "daily_ai_news.py failed with exit code $ScriptExitCode"
}

exit $ScriptExitCode
