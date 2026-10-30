param(
    [ValidateSet('Start','Stop','Status')][string]$Action = 'Start',
    [Parameter(Mandatory=$true)][string]$ProviderRecord,
    [string]$TokenFile,
    [string]$StateDirectory,
    [string]$ContentDirectory,
    [int]$Port = 47821,
    [string]$Distro = 'Ubuntu-26.04',
    [string]$Python = '/home/mictian/build/nixboy-venv/bin/python'
)
$ErrorActionPreference = 'Stop'
function LinuxPath([string]$value) {
    if ($value.StartsWith('/')) { return $value }
    $resolved = [System.IO.Path]::GetFullPath($value)
    if ($resolved -notmatch '^([A-Za-z]):[\\/](.*)$') { throw 'Use a local drive or Linux absolute path.' }
    return '/mnt/' + $Matches[1].ToLowerInvariant() + '/' + $Matches[2].Replace('\','/')
}
function QuoteArgument([string]$value) {
    if ($value.Contains('"') -or $value.Contains("`r") -or $value.Contains("`n")) { throw 'Invalid argument.' }
    if ($value -notmatch '\s') { return $value }
    return '"' + $value + '"'
}
$provider = LinuxPath (Join-Path $PSScriptRoot 'nixboy/wsl_provider.py')
$record = LinuxPath $ProviderRecord
$wsl = Join-Path $env:WINDIR 'System32/wsl.exe'
if ($Action -ne 'Start') {
    & $wsl -d $Distro --exec $Python $provider $Action.ToLowerInvariant() --record $record
    if ($LASTEXITCODE -ne 0) { throw "Provider $Action failed." }
    return
}
if (!$TokenFile -or !$StateDirectory -or !$ContentDirectory) { throw 'Start requires TokenFile, StateDirectory and ContentDirectory.' }
if ($Port -lt 1 -or $Port -gt 65535) { throw 'Invalid port.' }
$existing = [System.Net.Sockets.TcpClient]::new()
try {
    $probe = $existing.ConnectAsync('127.0.0.1',$Port)
    if ($probe.Wait(200) -and $existing.Connected) { throw "Port $Port already has a listener; inspect it before starting another bridge." }
} catch [System.AggregateException] {} finally { $existing.Dispose() }
$log = $record + '.log'
$arguments = @('-d',$Distro,'--exec',$Python,$provider,'run','--record',$record,'--log',$log,'--',
               '--token-file',(LinuxPath $TokenFile),'--state-dir',(LinuxPath $StateDirectory),
               '--content-dir',(LinuxPath $ContentDirectory),'--port',"$Port")
# A foreground bridge under a hidden wsl.exe client holds the distro alive.
# The caller can exit PowerShell; Stop signals only the recorded provider.
$windowsLog = if ($ProviderRecord.StartsWith('/')) {
    Join-Path ([IO.Path]::GetTempPath()) "nixboy-provider-$PID"
} else { [IO.Path]::GetFullPath($ProviderRecord) }
$process = Start-Process -FilePath $wsl -ArgumentList (($arguments | ForEach-Object { QuoteArgument $_ }) -join ' ') `
    -WindowStyle Hidden -PassThru -RedirectStandardOutput "$windowsLog.launch.log" -RedirectStandardError "$windowsLog.launch.err"
$deadline = [DateTime]::UtcNow.AddSeconds(15)
do {
    Start-Sleep -Milliseconds 250
    if ($process.HasExited) { throw "Provider exited. Inspect $ProviderRecord.log" }
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connection = $client.ConnectAsync('127.0.0.1',$Port)
        if ($connection.Wait(200) -and $client.Connected) {
            Write-Output "Provider started (Windows PID $($process.Id)), bridge localhost:$Port. Log: $ProviderRecord.log"
            return
        }
    } catch {} finally { $client.Dispose() }
} while ([DateTime]::UtcNow -lt $deadline)
throw "Provider readiness not observed within 15 seconds. Inspect $ProviderRecord.log and use -Action Status/Stop with the same record."
