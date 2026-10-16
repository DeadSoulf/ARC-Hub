# ARC Lite: register the local stdio agent with ChatGPT Desktop / Codex.
# No VDS, public endpoint, localhost HTTP, Secure MCP Tunnel or API keys.
param(
    [switch]$EnableApps,
    [switch]$Preflight,
    [switch]$Reinstall
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Agent = Join-Path $Root 'arc_lite.py'
$Python = (Get-Command python -ErrorAction Stop).Source
$Codex = (Get-Command codex -ErrorAction Stop).Source
$Name = 'arc_lite'
$Shared = Join-Path $env:LOCALAPPDATA 'ARC-Lite\shared'
if (-not (Test-Path -LiteralPath $Agent -PathType Leaf)) {
    throw 'Missing arc_lite.py next to start.ps1'
}
New-Item -ItemType Directory -Path $Shared -Force | Out-Null
$Arguments = @('--shared', $Shared)
if ($EnableApps) { $Arguments += '--enable-apps' }
& $Python $Agent --self-test @Arguments
if ($LASTEXITCODE -ne 0) { throw 'Local agent self-test failed' }
Write-Host ('Agent: ' + $Agent)
Write-Host ('Shared folder: ' + $Shared)
Write-Host ('Notepad allowed: ' + $EnableApps)
Write-Host 'ChatGPT Desktop / Codex: local stdio mode only; no server or tunnel.'
if ($Preflight) {
    Write-Host 'ARC_LITE_PREFLIGHT_OK'
    return
}

$Installed = $false
try {
    $null = & $Codex mcp get $Name 2>&1
    $Installed = ($LASTEXITCODE -eq 0)
}
catch {
    # A missing local MCP is normal on first installation.
    $Installed = $false
}
if ($Installed -and -not $Reinstall) {
    Write-Host 'ARC_LITE_ALREADY_REGISTERED. To change permissions, run with -Reinstall.'
    & $Codex mcp get $Name
    return
}

$Config = Join-Path $env:USERPROFILE '.codex\config.toml'
if (Test-Path -LiteralPath $Config -PathType Leaf) {
    $Backup = $Config + '.backup-arc-lite-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
    Copy-Item -LiteralPath $Config -Destination $Backup -ErrorAction Stop
    Write-Host ('Configuration backup: ' + $Backup)
}
if ($Installed) {
    & $Codex mcp remove $Name
    if ($LASTEXITCODE -ne 0) { throw 'Could not replace existing ARC Lite entry' }
}
$CommandArgs = @('mcp', 'add', $Name, '--', $Python, '-u', $Agent) + $Arguments
& $Codex @CommandArgs
if ($LASTEXITCODE -ne 0) { throw 'Codex MCP registration failed' }

Write-Host 'ARC_LITE_REGISTERED'
& $Codex mcp get $Name
if ($LASTEXITCODE -ne 0) { throw 'Installed MCP was not found during read-back' }
Write-Host 'Now restart ChatGPT Desktop; open a LOCAL chat and use /mcp.'
