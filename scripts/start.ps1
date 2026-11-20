param([int]$Port = 8765, [switch]$Lan)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$venvPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw 'Please create .venv with CPython 3.12+ and install requirements.txt first. See README.md.'
}
$listenAddress = if ($Lan) { '0.0.0.0' } else { '127.0.0.1' }
Write-Host "Tongxi: http://127.0.0.1:$Port"
& $venvPython -m uvicorn tongxi.main:app --host $listenAddress --port $Port --no-access-log
exit $LASTEXITCODE
