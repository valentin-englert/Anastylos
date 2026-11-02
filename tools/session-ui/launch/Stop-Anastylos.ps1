# One-click stop of the Anastylos desktop page (the one on ANASTYLOS_PORT, default 8071).
$port = if ($env:ANASTYLOS_PORT) { $env:ANASTYLOS_PORT } else { "8071" }
$state = Join-Path $env:LOCALAPPDATA "Anastylos"
$pidFile = Join-Path $state "ui.pid"
$stopped = 0
if (Test-Path $pidFile) {
    $id = [int](Get-Content $pidFile)
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
    if ($p -and $p.CommandLine -match "session-ui.server\.py") { Stop-Process -Id $id -Force; $stopped++ }
    Remove-Item $pidFile -Force
}
# a hand-started copy on the same port; other Anastylos servers on other ports are left alone
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "session-ui.server\.py" -and $_.CommandLine -match "--port $port" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $stopped++ }
Add-Type -AssemblyName PresentationFramework
$text = if ($stopped) { "Anastylos was stopped." } else { "Anastylos was not running." }
[System.Windows.MessageBox]::Show($text, "Anastylos", "OK", "Information") | Out-Null
