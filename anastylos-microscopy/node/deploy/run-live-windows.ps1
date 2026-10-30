param(
    [string]$PiHost = 'anastylos.local',
    [string]$HostKeyAlias = 'anastylos.local'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'The existing Anastylos virtual environment is missing.'
}
$env:ANASTYLOS_ROOT = $repoRoot
$env:PYTHONPATH = (Join-Path $repoRoot 'anastylos-microscopy\node\src') + ';' +
    (Join-Path $repoRoot 'anastylos-microscopy\src')
& $python -m anastylos_mic_node.live_analysis --host $PiHost --host-key-alias $HostKeyAlias
exit $LASTEXITCODE
