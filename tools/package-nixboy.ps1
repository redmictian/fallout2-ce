param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$Destination,
    [string]$LiveProbe
)
$ErrorActionPreference = 'Stop'
$sourceDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$executablePath = (Resolve-Path -LiteralPath $Executable).Path
$destinationPath = [IO.Path]::GetFullPath($Destination)
if (Test-Path -LiteralPath $destinationPath) {
    throw 'Use a new package destination; existing files are never overwritten.'
}
$sourceCommit = (& git -C $sourceDir rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot determine source revision.' }
$sourceStatus = & git -C $sourceDir status --porcelain
if ($sourceStatus) { throw 'Commit the reviewed source before packaging.' }
New-Item -ItemType Directory -Path $destinationPath | Out-Null
Copy-Item -LiteralPath $executablePath -Destination (Join-Path $destinationPath 'fallout2-ce.exe')

# Explicit source allowlist: no game assets, saves, runtime config or tokens.
$files = @(
    'LICENSE.md', 'nixboy.ini.example',
    'tools/nixboy/README.md', 'tools/nixboy/TRAINING.md', 'tools/nixboy/protocol.md', 'tools/nixboy/.dockerignore',
    'tools/nixboy/requirements.txt', 'tools/nixboy/bridge.py',
    'tools/nixboy/protocol.py', 'tools/nixboy/training.py',
    'tools/nixboy/challenge_content.py', 'tools/nixboy/challenge_env.py',
    'tools/nixboy/smoke_docker.py',
    'tools/nixboy/smoke_windows_wsl.py',
    'tools/nixboy/container/Dockerfile', 'tools/nixboy/container/control.py',
    'tools/nixboy/container/nixboy', 'tools/nixboy/container/shell.py',
    'tools/nixboy/container/Dockerfile.training', 'tools/nixboy/container/Dockerfile.sshd',
    'tools/nixboy/container/sshd_config', 'tools/nixboy/container/sshd_start.py',
    'third_party/libvterm/LICENSE', 'third_party/libvterm/README.nixboy.md',
    'third_party/fpattern/LICENSE', 'third_party/sdl2/LICENSE', 'third_party/zlib/LICENSE'
)
# Content is authored data only; runtime attempts, credentials and test solvers
# are not in this directory or part of the redistributable package.
$contentRoot = Join-Path $sourceDir 'tools/nixboy/content'
foreach ($contentFile in Get-ChildItem -LiteralPath $contentRoot -Recurse -File) {
    if ($contentFile.Extension -notin @('.yaml', '.yml', '.md', '.txt', '.json')) {
        throw "Unexpected file in authored training content: $($contentFile.Name)"
    }
    $files += $contentFile.FullName.Substring($sourceDir.Length + 1).Replace('\', '/')
}
foreach ($relativePath in $files) {
    $targetPath = Join-Path $destinationPath $relativePath
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $targetPath) | Out-Null
    Copy-Item -LiteralPath (Join-Path $sourceDir $relativePath) -Destination $targetPath
}
$notice = @"
NIX-BOY TRAINING - modified Fallout 2 Community Edition
Source branch: mod/nix-boy
Source commit: $sourceCommit

This copy has been modified to add Nix-Boy and a Windows build portability fix.
See LICENSE.md for the engine's terms and third_party/*/LICENSE for dependencies.

Read tools/nixboy/README.md before setup. Docker and a local Python bridge are
required for Linux. Enable nixboy.ini in a separate owned-data test installation.
No game assets, saves, runtime configuration or authentication tokens are included.

Windows x64, live WSL Docker, in-game training, earned skill improvement and
save/reload were checked in a disposable owned-data installation. Unarmed and
Science each have two levels with five challenge options per level.
Read tools/nixboy/TRAINING.md for setup, controls and current limits.

For fast disposable tests, --debug-new-game=narg starts a fresh predetermined
character without menus or MVE movies. --debug-no-movies keeps normal menus.
Neither option saves or loads automatically. Never test in your clean game copy.
"@
Set-Content -LiteralPath (Join-Path $destinationPath 'READ-ME.txt') -Value $notice -Encoding utf8
$checksum = (Get-FileHash -LiteralPath (Join-Path $destinationPath 'fallout2-ce.exe') -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $destinationPath 'SHA256.txt') -Value "$checksum  fallout2-ce.exe" -Encoding ascii
if ($LiveProbe) {
    $probePath = (Resolve-Path -LiteralPath $LiveProbe).Path
    Copy-Item -LiteralPath $probePath -Destination (Join-Path $destinationPath 'nixboy-live-probe.exe')
    $probeChecksum = (Get-FileHash -LiteralPath $probePath -Algorithm SHA256).Hash.ToLowerInvariant()
    Add-Content -LiteralPath (Join-Path $destinationPath 'SHA256.txt') -Value "$probeChecksum  nixboy-live-probe.exe" -Encoding ascii
}
$archivePath = "$destinationPath.zip"
if (Test-Path -LiteralPath $archivePath) { throw 'Archive already exists.' }
Compress-Archive -LiteralPath $destinationPath -DestinationPath $archivePath
Write-Output $archivePath
