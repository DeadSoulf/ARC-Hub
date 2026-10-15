# ARC Lite launcher: Windows user session + outbound OpenAI Secure MCP Tunnel.
# No VDS, Docker, localhost port or saved API keys.
param(
    [switch]$EnableApps,
    [switch]$Preflight,
    [string]$TunnelId = '',
    [string]$ClientPath = ''
)
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Agent = Join-Path $Here 'arc_lite.py'
$PythonCommand = Get-Command python -ErrorAction Stop
$Python = $PythonCommand.Source
if (-not (Test-Path -LiteralPath $Agent -PathType Leaf)) {
    throw 'arc_lite.py must be in the same folder as start.ps1'
}
$Candidates = @()
if ($ClientPath) { $Candidates += $ClientPath }
$Located = Get-Command tunnel-client -ErrorAction SilentlyContinue
if ($Located) { $Candidates += $Located.Source }
$Candidates += (Join-Path $Here 'tunnel-client.exe')
$Candidates += 'D:\AI\01_Projects\Remote-Control\AI-Remote-Commander-Local-Pilot\tunnel-client\bin\tunnel-client.exe'
$Client = $Candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $Client) {
    throw 'Official tunnel-client.exe not found. Pass -ClientPath to a verified OpenAI binary.'
}

$Shared = Join-Path $env:LOCALAPPDATA 'ARC-Lite\shared'
New-Item -ItemType Directory -Path $Shared -Force | Out-Null
$AppArg = @()
if ($EnableApps) { $AppArg += '--enable-apps' }
& $Python $Agent --shared $Shared --self-test @AppArg
if ($LASTEXITCODE -ne 0) { throw 'ARC Lite local self-test failed' }
Write-Host ('Shared directory: ' + $Shared)
Write-Host ('Notepad permission enabled: ' + $EnableApps)
& $Client --version
if ($LASTEXITCODE -ne 0) { throw 'tunnel-client binary did not start' }
if ($Preflight) {
    Write-Host 'ARC_LITE_PREFLIGHT_OK: local agent and tunnel-client are available.'
    Write-Host 'No secrets requested and no tunnel started.'
    return
}

if (-not $TunnelId) { $TunnelId = Read-Host 'Enter your Platform Tunnel ID' }
if ($TunnelId -cnotmatch '^tunnel_[A-Za-z0-9_]{16,128}$') {
    throw 'Invalid Tunnel ID. Create it at https://platform.openai.com/settings/organization/tunnels'
}

# This stores a command and tunnel ID in a dedicated profile, NOT the runtime secret.
$Profile = 'arc-lite-asus'
$McpCommand = ('"{0}" -u "{1}" --shared "{2}"' -f $Python, $Agent, $Shared)
if ($EnableApps) { $McpCommand += ' --enable-apps' }
& $Client init --sample sample_mcp_stdio_local --profile $Profile --tunnel-id $TunnelId --mcp-command $McpCommand --health-listen-addr '127.0.0.1:0' --force
if ($LASTEXITCODE -ne 0) { throw 'Could not initialize ARC Lite tunnel profile' }

$SecureKey = Read-Host 'Runtime API Key (hidden; never saved)' -AsSecureString
if ($SecureKey.Length -lt 20) {
    $SecureKey.Dispose()
    throw 'Missing Runtime API Key'
}
$Pointer = [IntPtr]::Zero
try {
    $Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureKey)
    $env:CONTROL_PLANE_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
    & $Client doctor --profile $Profile --explain
    if ($LASTEXITCODE -ne 0) { throw 'Tunnel doctor failed. Check permissions/workspace/tunnel ID.' }
    Write-Host 'Tunnel checks passed. Keep this window open while using ChatGPT.'
    & $Client run --profile $Profile
    if ($LASTEXITCODE -ne 0) { throw 'Tunnel client stopped with an error' }
}
finally {
    Remove-Item Env:CONTROL_PLANE_API_KEY -ErrorAction SilentlyContinue
    if ($Pointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
    }
    $SecureKey.Dispose()
}
