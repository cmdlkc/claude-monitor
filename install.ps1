# Claude Monitor one-line installer. In PowerShell:
#   irm https://raw.githubusercontent.com/cdilekci/claude-monitor/main/install.ps1 | iex
# Downloads the latest release, then lets Claude Monitor.exe install itself to
# %LOCALAPPDATA%\Programs\Claude Monitor (hooks, status line, start at login).
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # the progress bar makes downloads very slow in PowerShell 5
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$zip = 'https://github.com/cdilekci/claude-monitor/releases/latest/download/Claude-Monitor.zip'
$tmp = Join-Path $env:TEMP 'claude-monitor-setup'
if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
New-Item -ItemType Directory $tmp | Out-Null

Write-Host 'Downloading Claude Monitor...'
Invoke-WebRequest $zip -OutFile "$tmp\Claude-Monitor.zip" -UseBasicParsing
Expand-Archive "$tmp\Claude-Monitor.zip" $tmp -Force
Write-Host 'Installing...'
Start-Process "$tmp\Claude Monitor\Claude Monitor.exe" -ArgumentList 'install'
Write-Host 'Done. A message box confirms the install; the overlay opens at the top right of the screen.'
