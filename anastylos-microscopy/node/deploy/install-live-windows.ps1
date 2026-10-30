param(
    [string]$PiHost = 'anastylos.local',
    [string]$HostKeyAlias = 'anastylos.local',
    [switch]$Update
)

$ErrorActionPreference = 'Stop'
if ($PiHost -notmatch '^[A-Za-z0-9][A-Za-z0-9.-]*$' -or
    $HostKeyAlias -notmatch '^[A-Za-z0-9][A-Za-z0-9.-]*$') {
    throw 'Invalid Pi host or host key alias.'
}
$taskName = 'ANASTYLOS Microscopy Live'
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and $existing.Description -ne 'ANASTYLOS live microscope reader') {
    throw "A different scheduled task named '$taskName' exists; inspect it before changing it."
}
$launcher = Join-Path $PSScriptRoot 'live-windows.py'
$python = Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf) -or
    -not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Live launcher or Anastylos virtual environment is missing.'
}
$basePrefix = & $python -c 'import sys; print(sys.base_prefix)'
if ($LASTEXITCODE -ne 0) { throw 'Could not determine the virtual environment Python runtime.' }
$pythonw = Join-Path $basePrefix 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) { throw 'Base pythonw.exe is missing.' }
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
$shell = New-Object -ComObject WScript.Shell
foreach ($item in @(
    @{ Name = 'ANASTYLOS Mikroskop starten'; Arguments = "`"$launcher`" --host $PiHost --host-key-alias $HostKeyAlias" },
    @{ Name = 'ANASTYLOS Mikroskop stoppen'; Arguments = "`"$launcher`" --stop" }
)) {
    $link = $shell.CreateShortcut((Join-Path $desktop ($item.Name + '.lnk')))
    $link.TargetPath = $pythonw
    $link.Arguments = $item.Arguments
    $link.WorkingDirectory = $PSScriptRoot
    $link.Save()
}
if ($existing) { Disable-ScheduledTask -TaskName $taskName | Out-Null }
Write-Host 'Desktop shortcuts installed. The live worker will not start at logon.'
