param([int]$Port = 8765, [switch]$Lan)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$venvPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw 'Please create .venv with CPython 3.12+ and install requirements.txt first. See README.md.'
}
$listenAddress = if ($Lan) { '0.0.0.0' } else { '127.0.0.1' }
$funasrPython = if ($env:TONGXI_FUNASR_PYTHON) { $env:TONGXI_FUNASR_PYTHON } else { 'D:\challenge_AI\SameSeat\03_语音评测\ASR接入测试\venvs\funasr\Scripts\python.exe' }
$funasrServer = if ($env:TONGXI_FUNASR_SERVER) { $env:TONGXI_FUNASR_SERVER } else { 'D:\challenge_AI\SameSeat\03_语音评测\ASR接入测试\sources\FunASR\runtime\python\websocket\funasr_wss_server.py' }
$funasrCache = if ($env:TONGXI_FUNASR_CACHE) { $env:TONGXI_FUNASR_CACHE } else { 'D:\challenge_AI\SameSeat\03_语音评测\ASR接入测试\cache\modelscope' }
$funasrUp = $false
try {
    $probe = New-Object System.Net.Sockets.TcpClient
    $probe.Connect('127.0.0.1', 10095)
    $probe.Close()
    $funasrUp = $true
} catch { $funasrUp = $false }
if ($funasrUp) {
    Write-Host 'FunASR already listening on 127.0.0.1:10095'
} elseif ((Test-Path -LiteralPath $funasrPython) -and (Test-Path -LiteralPath $funasrServer)) {
    $env:MODELSCOPE_CACHE = $funasrCache
    Start-Process -FilePath $funasrPython -ArgumentList @($funasrServer, '--host', '127.0.0.1', '--port', '10095', '--ngpu', '1', '--device', 'cuda', '--ncpu', '4') -WorkingDirectory (Split-Path -Parent $funasrServer) -WindowStyle Minimized
    Write-Host 'FunASR starting on 127.0.0.1:10095'
} else {
    Write-Host 'FunASR runtime was not found. Transcription stays unavailable until 127.0.0.1:10095 is running.'
}
Write-Host "Tongxi: http://127.0.0.1:$Port"
& $venvPython -m uvicorn tongxi.main:app --host $listenAddress --port $Port --no-access-log
exit $LASTEXITCODE
