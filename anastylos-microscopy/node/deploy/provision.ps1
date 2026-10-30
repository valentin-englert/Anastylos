param(
    [string]$PiHost = 'anastylos.local',
    [string]$HostKeyAlias = '',
    [string]$PiUser = 'anastylos',
    [string]$ArtifactRoot = (Join-Path $env:USERPROFILE 'Documents\Anastylos-artifacts\microscopy')
)

$ErrorActionPreference = 'Stop'

function Copy-SourceFiles {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string[]]$Extensions
    )
    $sourcePath = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $Source).Path).TrimEnd(
        [char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar))
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Get-ChildItem -LiteralPath $sourcePath -Recurse -File |
        Where-Object {
            $_.Extension -in $Extensions -and
            $_.FullName -notmatch '[\\/](\.git|__pycache__|\.pytest_cache)[\\/]'
        } |
        ForEach-Object {
            $relative = $_.FullName.Substring($sourcePath.Length).TrimStart(
                [char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar))
            $target = Join-Path $Destination $relative
            $parent = Split-Path -Parent $target
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $target
        }
}

if ($PiUser -notmatch '^[a-z_][a-z0-9_-]{0,31}$') {
    throw 'PiUser must be a simple Linux account name.'
}
if ([string]::IsNullOrWhiteSpace($HostKeyAlias)) {
    $HostKeyAlias = $PiHost
}
if ($HostKeyAlias -match '[\s,]') {
    throw 'HostKeyAlias must not contain whitespace or commas.'
}
foreach ($toolName in @('ssh.exe', 'scp.exe', 'tar.exe')) {
    if (-not (Get-Command $toolName -ErrorAction SilentlyContinue)) {
        throw "Required Windows tool is missing: $toolName"
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$destination = "$PiUser@$PiHost"
$sshOptions = @('-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
    '-o', 'ConnectTimeout=8', '-o', "HostKeyAlias=$HostKeyAlias")
$remoteInfo = & ssh.exe @sshOptions $destination '. /etc/os-release; echo "os=$PRETTY_NAME"; echo "arch=$(uname -m)"; echo "user=$(id -un)"'
if ($LASTEXITCODE -ne 0) {
    throw 'SSH preflight failed. Configure the Pi host key and authorized key first.'
}
if (($remoteInfo -join "`n") -notmatch '(?m)^arch=(armv7l|aarch64)$' -or
    ($remoteInfo -join "`n") -notmatch '(?im)^os=.*(raspbian|raspberry|debian)') {
    throw "Unsupported Raspberry Pi OS/architecture:`n$($remoteInfo -join "`n")"
}

Write-Host "Target $destination`n$($remoteInfo -join "`n")"
Write-Host 'This provisioning action installs missing Pi OS packages and enables one systemd service.'
$approval = Read-Host 'Continue with provisioning? Type Y to proceed'
if ($approval -notin @('Y', 'y')) {
    throw 'Provisioning cancelled before any remote changes.'
}

New-Item -ItemType Directory -Force -Path $ArtifactRoot | Out-Null
$bundleId = [guid]::NewGuid().ToString('N')
$bundleStage = Join-Path $ArtifactRoot "staging-$bundleId"
New-Item -ItemType Directory -Path $bundleStage | Out-Null
try {
    Copy-SourceFiles (Join-Path $repoRoot 'anastylos-microscopy\node\src') `
        (Join-Path $bundleStage 'node\src') @('.py')
    New-Item -ItemType Directory -Force -Path (Join-Path $bundleStage 'node\assets') | Out-Null
    Copy-Item -LiteralPath (Join-Path $repoRoot 'anastylos-microscopy\node\assets\anastylos_logo.png') `
        -Destination (Join-Path $bundleStage 'node\assets\anastylos_logo.png')
    Copy-SourceFiles (Join-Path $repoRoot 'anastylos-microscopy\src') `
        (Join-Path $bundleStage 'src') @('.py')
    Copy-SourceFiles (Join-Path $repoRoot 'anastylos-microscopy\schemas') `
        (Join-Path $bundleStage 'schemas') @('.json')
    New-Item -ItemType Directory -Force -Path (Join-Path $bundleStage 'deploy') | Out-Null
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'anastylos-mic-node.service') `
        -Destination (Join-Path $bundleStage 'deploy\anastylos-mic-node.service')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'install-pi.sh') `
        -Destination (Join-Path $bundleStage 'deploy\install-pi.sh')

    $archive = Join-Path $ArtifactRoot "anastylos-mic-node-$bundleId.tar.gz"
    & tar.exe -czf $archive -C $bundleStage .
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not create the external deployment archive.'
    }
    $releaseId = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.Substring(0, 12).ToLowerInvariant()
    $remoteStage = "/tmp/anastylos-mic-node-$bundleId"
    $remoteArchive = "$remoteStage.bundle.tar.gz"
    & ssh.exe @sshOptions $destination "mkdir -m 700 -p '$remoteStage'"
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not create the private temporary staging directory on the Pi.'
    }
    & scp.exe @sshOptions $archive "$destination`:$remoteArchive"
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not transfer the deployment archive to the Pi.'
    }

    $remoteCommand = "tar -xzf '$remoteArchive' -C '$remoteStage' && sudo bash '$remoteStage/deploy/install-pi.sh' --user '$PiUser' --stage '$remoteStage' --release-id '$releaseId' && rm -f '$remoteArchive'"
    & ssh.exe -t @sshOptions $destination $remoteCommand
    if ($LASTEXITCODE -ne 0) {
        throw "Pi provisioning failed. Inspect the output above; local bundle retained at $archive"
    }
    Write-Host "Provisioning completed. Archive: $archive"
    Write-Host "On the Pi, diagnostic access is available with: sudo -u $PiUser /usr/local/bin/anastylos-mic-node status"
}
finally {
    if (Test-Path -LiteralPath $bundleStage) {
        Remove-Item -LiteralPath $bundleStage -Recurse -Force
    }
}
