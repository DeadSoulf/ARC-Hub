$ErrorActionPreference='Stop'
$InstallDir=Join-Path $env:LOCALAPPDATA 'ARC'
$Config=Get-Content (Join-Path $InstallDir 'agent.json') -Raw|ConvertFrom-Json
$Task=Get-ScheduledTask -TaskName 'ARC Agent' -ErrorAction SilentlyContinue
$health='offline'
try{ $health=(Invoke-RestMethod -Uri ('http://127.0.0.1:'+$Config.port+'/healthz') -TimeoutSec 3).status }catch{}
Write-Output ('Device: '+$Config.name)
Write-Output ('Device ID: '+$Config.device_id)
Write-Output ('Agent task: '+$(if($Task){$Task.State}else{'not installed'}))
Write-Output ('Local health: '+$health)
Write-Output ('Port: '+$Config.port)
Write-Output ('Gateway configured: '+[bool]($Config.gateway_url -and $Config.device_token))
Write-Output ('Logs: '+(Join-Path $InstallDir 'logs'))
