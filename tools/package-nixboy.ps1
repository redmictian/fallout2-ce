param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$Destination,
    [Parameter(Mandatory = $true)][string]$WikiDirectory,
    [Parameter(Mandatory = $true)][string]$WebView2Loader,
    [string]$LiveProbe,
    [switch]$QemuPilot,
    [switch]$AssessmentPilot,
    [switch]$DeliveryPilot
)
$ErrorActionPreference = 'Stop'

function Get-PackageFiles([string]$Root) {
    foreach ($item in Get-ChildItem -LiteralPath $Root -Recurse -Force) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Package input must not contain symbolic links or junctions: $($item.FullName)"
        }
        if (-not $item.PSIsContainer) { $item }
    }
}

function Get-RelativeFile([string]$Root, [string]$RelativePath) {
    if (-not $RelativePath -or $RelativePath -match '[\\:?#]' -or $RelativePath.StartsWith('/') -or
        @($RelativePath.Split('/') | Where-Object { $_ -in @('', '.', '..') }).Count) {
        throw "Invalid package manifest path: $RelativePath"
    }
    $path = [IO.Path]::GetFullPath((Join-Path $Root $RelativePath))
    if (-not $path.StartsWith($Root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
        -not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Missing package manifest file: $RelativePath"
    }
    return $path
}

function Assert-ManifestHash([string]$Path, [string]$Expected) {
    if ($Expected -cnotmatch '^[a-f0-9]{64}$' -or
        (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Expected) {
        throw "Wiki export hash mismatch; regenerate the wiki: $Path"
    }
}

$sourceDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$executablePath = (Resolve-Path -LiteralPath $Executable).Path
$wikiPath = (Resolve-Path -LiteralPath $WikiDirectory).Path.TrimEnd('\', '/')
$loaderPath = (Resolve-Path -LiteralPath $WebView2Loader).Path
$destinationPath = [IO.Path]::GetFullPath($Destination)
if (Test-Path -LiteralPath $destinationPath) {
    throw 'Use a new package destination; existing files are never overwritten.'
}
$archivePath = "$destinationPath.zip"
if (Test-Path -LiteralPath $archivePath) { throw 'Archive already exists.' }

# An explicit export lets the build machine use its existing Python environment,
# including WSL. Players only need these static files and the WebView2 runtime.
$runtimeRoot = Join-Path $sourceDir 'tools/nixboy'
$wikiFiles = @(Get-PackageFiles $wikiPath)
$manifestPath = Join-Path $wikiPath 'wiki-export.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw 'WikiDirectory needs wiki-export.json; run wiki_pages.py --output NEW-DIRECTORY first.'
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.schema -ne 1 -or $null -eq $manifest.source_sha256 -or $null -eq $manifest.files_sha256) {
    throw 'Unsupported wiki export manifest.'
}
$sourceNames = @('challenge_content.py', 'wiki_content.py', 'wiki_pages.py')
$wikiSourceContent = Join-Path $runtimeRoot $(if ($DeliveryPilot) { 'content-delivery-test' } else { 'content' })
foreach ($item in Get-PackageFiles $wikiSourceContent) {
    $sourceNames += 'content/' + $item.FullName.Substring($wikiSourceContent.Length + 1).Replace('\', '/')
}
$sourceProperties = @($manifest.source_sha256.PSObject.Properties)
if (@(Compare-Object ($sourceNames | Sort-Object) ($sourceProperties.Name | Sort-Object)).Count) {
    throw 'Wiki source files changed; regenerate the wiki before packaging.'
}
foreach ($entry in $sourceProperties) {
    $sourcePath = if ($entry.Name.StartsWith('content/')) {
        Get-RelativeFile $wikiSourceContent $entry.Name.Substring(8)
    } else {
        Get-RelativeFile $runtimeRoot $entry.Name
    }
    Assert-ManifestHash $sourcePath $entry.Value
}
$exportProperties = @($manifest.files_sha256.PSObject.Properties)
$exportNames = @($wikiFiles | Where-Object { $_.FullName -ne $manifestPath } |
    ForEach-Object { $_.FullName.Substring($wikiPath.Length + 1).Replace('\', '/') })
if (-not $exportProperties.Count -or
    @(Compare-Object ($exportNames | Sort-Object) ($exportProperties.Name | Sort-Object)).Count -or
    @('index.html', 'wiki.css', 'navigation.js' | Where-Object { $_ -notin $exportNames }).Count) {
    throw 'Wiki export files changed or are incomplete; regenerate the wiki before packaging.'
}
foreach ($entry in $exportProperties) {
    Assert-ManifestHash (Get-RelativeFile $wikiPath $entry.Name) $entry.Value
}
$loaderHashPath = Join-Path $sourceDir 'third_party/webview2/loader.sha256'
$expectedLoader = (Get-Content -LiteralPath $loaderHashPath -Raw).Trim().Split(' ')[0]
if ((Get-FileHash -LiteralPath $loaderPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $expectedLoader) {
    throw 'WebView2Loader must be the pinned official x64 loader; see third_party/webview2/README.nixboy.md.'
}
$sourceCommit = (& git -C $sourceDir rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot determine source revision.' }
$modVersion = (Get-Content -LiteralPath (Join-Path $sourceDir 'nixboy.version') -Raw).Trim()
if ($modVersion -notmatch '^\d+\.\d+\.\d+$') { throw 'nixboy.version must contain one semantic release version.' }
$sourceBranch = (& git -C $sourceDir branch --show-current).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot determine source branch.' }
$sourceStatus = & git -C $sourceDir status --porcelain
if ($sourceStatus) { throw 'Commit the reviewed source before packaging.' }
New-Item -ItemType Directory -Path $destinationPath | Out-Null
Copy-Item -LiteralPath $executablePath -Destination (Join-Path $destinationPath 'fallout2-ce.exe')
Copy-Item -LiteralPath $loaderPath -Destination (Join-Path $destinationPath 'WebView2Loader.dll')

# Explicit source allowlist: no game assets, saves, runtime config or tokens.
$files = @(
    'LICENSE.md', 'nixboy.ini.example', 'nixboy.version',
    'third_party/departure-mono/README.nixboy.md', 'third_party/departure-mono/OFL.txt',
    'tools/nixboy/README.md', 'tools/nixboy/DEBUGGING.md', 'tools/nixboy/TRAINING.md', 'tools/nixboy/protocol.md', 'tools/nixboy/.dockerignore',
    'tools/nixboy/requirements.txt', 'tools/nixboy/bridge.py', 'tools/nixboy/QEMU.md',
    'tools/nixboy/protocol.py', 'tools/nixboy/training.py',
    'tools/nixboy/course_content.py', 'tools/nixboy/course_progress.py', 'tools/nixboy/education_records.py', 'tools/nixboy/COURSE.md',
    'tools/nixboy/perk_rewards.py', 'tools/nixboy/PERK_REWARDS.md', 'tools/nixboy/import_course_lessons.py',
    'tools/nixboy/NGINX.md',
    'tools/nixboy/nginx_env.py', 'tools/nixboy/nginx_profile.py', 'tools/nixboy/nginx_prepare.py', 'tools/nixboy/nginx_probe.py',
    'tools/nixboy/prepare_nginx_artifact.py', 'tools/nixboy/container/nginx/manifest.json',
    'tools/nixboy/challenge_content.py', 'tools/nixboy/challenge_env.py',
    'tools/nixboy/qemu_env.py', 'tools/nixboy/qemu_probe.py', 'tools/nixboy/qemu_artifacts.py', 'tools/nixboy/wsl_provider.py',
    'tools/start-nixboy-wsl.ps1',
    'tools/nixboy/wiki_content.py', 'tools/nixboy/wiki_pages.py',
    'tools/nixboy/smoke_docker.py',
    'tools/nixboy/smoke_windows_wsl.py',
    'tools/nixboy/container/Dockerfile', 'tools/nixboy/container/control.py',
    'tools/nixboy/container/nixboy', 'tools/nixboy/container/shell.py',
    'tools/nixboy/container/Dockerfile.training', 'tools/nixboy/container/Dockerfile.sshd',
    'tools/nixboy/container/sshd_config', 'tools/nixboy/container/sshd_start.py',
    'third_party/libvterm/LICENSE', 'third_party/libvterm/README.nixboy.md',
    'third_party/webview2/LICENSE.txt', 'third_party/webview2/NOTICE.txt',
    'third_party/webview2/README.nixboy.md', 'third_party/webview2/loader.sha256',
    'third_party/fpattern/LICENSE', 'third_party/sdl2/LICENSE', 'third_party/zlib/LICENSE'
)
foreach ($qemuName in @('Dockerfile','bt05-manifest.json','supervisor.py','control.py','serial_history.py')) {
    $relative = 'tools/nixboy/container/qemu/' + $qemuName
    $null = Get-RelativeFile $sourceDir $relative
    $files += $relative
}
if ($QemuPilot) {
    foreach ($pilotFile in Get-PackageFiles (Join-Path $runtimeRoot 'content-qemu-pilot')) {
        if ($pilotFile.Extension -notin @('.yaml', '.yml', '.md', '.json')) { throw 'Unexpected pilot catalog file.' }
        $files += $pilotFile.FullName.Substring($sourceDir.Length + 1).Replace('\', '/')
    }
}
if ($AssessmentPilot) {
    foreach ($pilotFile in Get-PackageFiles (Join-Path $runtimeRoot 'content-assessment-test')) {
        if ($pilotFile.Extension -notin @('.yaml', '.yml', '.md', '.json')) { throw 'Unexpected assessment catalog file.' }
        $files += $pilotFile.FullName.Substring($sourceDir.Length + 1).Replace('\', '/')
    }
}
if ($DeliveryPilot) {
    foreach ($pilotFile in Get-PackageFiles (Join-Path $runtimeRoot 'content-delivery-test')) {
        if ($pilotFile.Extension -notin @('.yaml', '.yml', '.md', '.json', '.html', '.css', '.svg', '.png', '.jpg', '.webp')) { throw 'Unexpected delivery catalog file.' }
        $files += $pilotFile.FullName.Substring($sourceDir.Length + 1).Replace('\', '/')
    }
}
# Content is authored data only; runtime attempts, credentials and test solvers
# are not in this directory or part of the redistributable package.
$contentRoot = Join-Path $sourceDir 'tools/nixboy/content'
foreach ($contentFile in Get-PackageFiles $contentRoot) {
    if ($contentFile.Extension -notin @('.yaml', '.yml', '.md', '.txt', '.json', '.html', '.css', '.js', '.svg', '.png', '.jpg', '.jpeg', '.webp')) {
        throw "Unexpected file in authored training content: $($contentFile.Name)"
    }
    $files += $contentFile.FullName.Substring($sourceDir.Length + 1).Replace('\', '/')
}
foreach ($relativePath in $files) {
    # Runtime files sit beside the game, without the source tools/ prefix.
    $packageRelativePath = if ($relativePath.StartsWith('tools/nixboy/')) {
        'nixboy/' + $relativePath.Substring('tools/nixboy/'.Length)
    } elseif ($relativePath -eq 'tools/start-nixboy-wsl.ps1') {
        'start-nixboy-wsl.ps1'
    } else { $relativePath }
    $targetPath = Join-Path $destinationPath $packageRelativePath
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $targetPath) | Out-Null
    Copy-Item -LiteralPath (Join-Path $sourceDir $relativePath) -Destination $targetPath
    if ($packageRelativePath.StartsWith('nixboy/') -and -not $packageRelativePath.StartsWith('nixboy/content/') -and
        $packageRelativePath.EndsWith('.md')) {
        # The operator guides retain working source links, one directory deeper.
        $markdown = Get-Content -LiteralPath $targetPath -Raw
        $markdown = $markdown.Replace('(../../nixboy.version)', '(../nixboy.version)')
        $markdown = $markdown.Replace('(../../nixboy.ini.example)', '(../nixboy.ini.example)')
        $markdown = $markdown.Replace('(../../third_party/', '(../third_party/')
        Set-Content -LiteralPath $targetPath -Value $markdown -Encoding utf8 -NoNewline
    }
}
foreach ($wikiFile in $wikiFiles) {
    if ($wikiFile.Extension -notin @('.json', '.html', '.css', '.js', '.svg', '.png', '.jpg', '.jpeg', '.webp')) {
        throw "Unexpected file in generated wiki: $($wikiFile.Name)"
    }
    $relativePath = $wikiFile.FullName.Substring($wikiPath.Length + 1)
    $targetPath = Join-Path (Join-Path $destinationPath 'nixboy/wiki') $relativePath
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $targetPath) | Out-Null
    Copy-Item -LiteralPath $wikiFile.FullName -Destination $targetPath
}
$notice = @"
nix-boy $modVersion - modified Fallout 2 Community Edition
Source branch: $sourceBranch
Source commit: $sourceCommit

This copy has been modified to add the nix-boy terminal, its challenge-based
skill progression, offline HTML wiki, perk training and a Windows build portability
fix. These modules share one mod version.
See LICENSE.md for the engine's terms and third_party/*/LICENSE for dependencies.
Departure Mono font data is covered by third_party/departure-mono/OFL.txt.

Read nixboy/README.md before setup. Docker and a local Python bridge are
required for the Linux terminal and challenges. Wiki reading uses the Windows
WebView2 runtime and needs no Python, Docker or running bridge. Enable nixboy.ini
in a separate owned-data test installation. Ctrl+W opens the wiki by default;
Escape closes it. Its generated home is nixboy/wiki/index.html.
No game assets, saves, runtime configuration or authentication tokens are included.

Editable catalog: nixboy/content/tracks.yaml and nixboy/content/challenges/.
Wiki lessons: nixboy/content/wiki/index.yaml, articles/ and assets/. Reading
references use stable article IDs in each challenge's optional wiki list.
Validate both catalogs with python nixboy/challenge_content.py. After authoring,
regenerate the HTML with python nixboy/wiki_pages.py --output PATH-TO-NEW-DIRECTORY.
Close the wiki, preserve local edits, then deploy the new export as nixboy/wiki/.
The initial catalog maps Unarmed to Filesystem and Science to Networking, each
with two current-skill bands and five challenge options per level. Skills and
levels can be extended in YAML using existing trusted task kinds. Restart the
bridge after editing; preserve earned level IDs and the separate state directory.
Each level may set reward_points from 1 to 300 (default 5). Previously earned
amounts remain fixed when catalog rewards are edited.
Release identity is recorded in nixboy.version and the
source commit above; see the mod workspace's acceptance record for actual checks.
Read nixboy/TRAINING.md for setup, controls and current limits.

The standard 0.7.0 perk module requires new characters and uses 1.2T saves;
ordinary 1.2R saves are incompatible. All 76 selectable perks gain a harmless
unlock rank; original benefits start at rank two, and normal upgrades require
level 310. Challenge-to-perk rewards are not connected yet.

For fast disposable tests, --debug-perks=all opens all rank-one perk cards,
--debug-perk-picker opens the real level-up picker, and --debug-perk=12:2 opens
Toughness rank two. These skip menus, character creation and MVE movies.
The separate nixboy/DEBUGGING.md documents every option and logging/limitations.
No debug option saves or loads automatically. Never test in your clean game copy.
"@
Set-Content -LiteralPath (Join-Path $destinationPath 'READ-ME.txt') -Value $notice -Encoding utf8
$checksum = (Get-FileHash -LiteralPath (Join-Path $destinationPath 'fallout2-ce.exe') -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $destinationPath 'SHA256.txt') -Value "$checksum  fallout2-ce.exe" -Encoding ascii
$loaderChecksum = (Get-FileHash -LiteralPath $loaderPath -Algorithm SHA256).Hash.ToLowerInvariant()
Add-Content -LiteralPath (Join-Path $destinationPath 'SHA256.txt') -Value "$loaderChecksum  WebView2Loader.dll" -Encoding ascii
if ($LiveProbe) {
    $probePath = (Resolve-Path -LiteralPath $LiveProbe).Path
    Copy-Item -LiteralPath $probePath -Destination (Join-Path $destinationPath 'nixboy-live-probe.exe')
    $probeChecksum = (Get-FileHash -LiteralPath $probePath -Algorithm SHA256).Hash.ToLowerInvariant()
    Add-Content -LiteralPath (Join-Path $destinationPath 'SHA256.txt') -Value "$probeChecksum  nixboy-live-probe.exe" -Encoding ascii
}
Compress-Archive -LiteralPath $destinationPath -DestinationPath $archivePath
Write-Output $archivePath
