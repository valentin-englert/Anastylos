# One-click start of the Anastylos desktop page. No terminal needed: double-click Start-Anastylos.bat.
# With a stack config (ANASTYLOS_STACK_CONFIG, default C:\ANASTYLOS\stack-config.json) it starts session UI and phone
# receiver through tools/demo/start_stack.py: one code state, backup, launch record. Without one, only the page, using
# ANASTYLOS_WORKSPACE (session folder) and ANASTYLOS_PORT (default 8071).
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
$stackConfig = if ($env:ANASTYLOS_STACK_CONFIG) { $env:ANASTYLOS_STACK_CONFIG } else { "C:\ANASTYLOS\stack-config.json" }
$useStack = Test-Path $stackConfig
if ($useStack) {
    $port = (Get-Content $stackConfig -Raw | ConvertFrom-Json).session_ui_port
    $url = "http://127.0.0.1:$port/"
}
function Test-Up { try { Invoke-WebRequest -UseBasicParsing "${url}api/sessions" -TimeoutSec 2 | Out-Null; $true } catch { $false } }

if ($useStack -and -not (Test-Up)) {
    $python = (Get-Content $stackConfig -Raw | ConvertFrom-Json).python
    $log = Join-Path $state "stack-start.log"
    # A dirty tree is allowed here and stated in the launch record, so a stray file cannot block the everyday start.
    & $python -B (Join-Path $root "tools\demo\start_stack.py") --config $stackConfig --allow-dirty *> $log
    if ($LASTEXITCODE -ne 0) { Show-Problem "Anastylos did not start. Details: $log" }
}
elseif (-not $useStack -and -not (Test-Path $workspace)) {
    Show-Problem "Anastylos cannot find its session folder:`n$workspace`n`nSet the variable ANASTYLOS_WORKSPACE to the right folder, then start again."
}
if (-not $useStack -and -not (Test-Up)) {
    $python = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) { $found = Get-Command python -ErrorAction SilentlyContinue; $python = if ($found) { $found.Source } else { $null } }
    if (-not $python) { Show-Problem "Python was not found. Please install Python 3.12 and start again." }
    $server = Join-Path $root "tools\session-ui\server.py"
    $process = Start-Process -FilePath $python -WindowStyle Hidden -PassThru `
        -ArgumentList @("-B", "`"$server`"", "--workspace-root", "`"$workspace`"", "--port", $port) `
        -RedirectStandardError (Join-Path $state "ui.log") -RedirectStandardOutput (Join-Path $state "ui.out.log")
    Set-Content -Path (Join-Path $state "ui.pid") -Value $process.Id
    for ($i = 0; $i -lt 40 -and -not (Test-Up); $i++) { Start-Sleep -Milliseconds 400 }
    if (-not (Test-Up)) { Show-Problem "Anastylos did not start. Details: $state\ui.log" }
}
Start-Process $url
