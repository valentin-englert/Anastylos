# One-click start of the Anastylos desktop page. No terminal needed: double-click Start-Anastylos.bat.
# Settings (optional environment variables): ANASTYLOS_WORKSPACE (session folder), ANASTYLOS_PORT (default 8071).
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$port = if ($env:ANASTYLOS_PORT) { $env:ANASTYLOS_PORT } else { "8071" }
$workspace = if ($env:ANASTYLOS_WORKSPACE) { $env:ANASTYLOS_WORKSPACE } else { "C:\ANASTYLOS\data\session-ui-demo" }
$url = "http://127.0.0.1:$port/"
$state = Join-Path $env:LOCALAPPDATA "Anastylos"
New-Item -ItemType Directory -Force $state | Out-Null

function Show-Problem([string]$text) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show($text, "Anastylos", "OK", "Warning") | Out-Null
    exit 1
}
function Test-Up { try { Invoke-WebRequest -UseBasicParsing "${url}api/sessions" -TimeoutSec 2 | Out-Null; $true } catch { $false } }

if (-not (Test-Path $workspace)) {
    Show-Problem "Anastylos cannot find its session folder:`n$workspace`n`nSet the variable ANASTYLOS_WORKSPACE to the right folder, then start again."
}
if (-not (Test-Up)) {
    $python = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) { $found = Get-Command python -ErrorAction SilentlyContinue; $python = if ($found) { $found.Source } else { $null } }
    if (-not $python) { Show-Problem "Python was not found. Please install Python 3.12 and start again." }
    $server = Join-Path $root "tools\session-ui\server.py"
    $process = Start-Process -FilePath $python -WindowStyle Hidden -PassThru `
        -ArgumentList @("-B", "`"$server`"", "--workspace-root", "`"$workspace`"", "--port", $port) `
        -RedirectStandardError (Join-Path $state "ui.log") -RedirectStandardOutput (Join-Path $state "ui.out.log")
    Set-Content -Path (Join-Path $state "ui.pid") -Value $process.Id
    $receiver = Join-Path $PSScriptRoot "receiver.cmd"          # optional: the phone receiver, added by the capture lane
    if (Test-Path $receiver) { Start-Process -FilePath $receiver -WindowStyle Hidden }
    for ($i = 0; $i -lt 40 -and -not (Test-Up); $i++) { Start-Sleep -Milliseconds 400 }
    if (-not (Test-Up)) { Show-Problem "Anastylos did not start. Details: $state\ui.log" }
}
Start-Process $url
