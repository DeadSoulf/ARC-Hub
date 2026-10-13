# Applies one private enrollment JSON from the trusted VDS ARC admin CLI.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$File)
$ErrorActionPreference='Stop'
$InstallDir=Join-Path $env:LOCALAPPDATA 'ARC'
$CfgPath=Join-Path $InstallDir 'agent.json'
if(-not (Test-Path -LiteralPath $CfgPath)){throw 'Install ARC Agent first'}
$Cfg=Get-Content -LiteralPath $CfgPath -Raw|ConvertFrom-Json
$E=Get-Content -LiteralPath $File -Raw|ConvertFrom-Json
if($E.device_id -cne $Cfg.device_id){throw 'Device ID mismatch; refusing another PC credential'}
if(-not $E.gateway_url.StartsWith('https://')){throw 'Remote VDS URL must use HTTPS'}
if($E.device_token.Length -lt 48){throw 'Device credential too short'}
$Cfg.gateway_url=$E.gateway_url
$Cfg.device_token=$E.device_token
if($E.name){$Cfg.name=$E.name}
$Temp=$CfgPath+'.tmp'
[IO.File]::WriteAllText($Temp,($Cfg|ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false))
$Who=[Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $Temp '/inheritance:r' '/grant:r' ($Who+':(F)')|Out-Null
if($LASTEXITCODE -ne 0){Remove-Item $Temp -Force;throw 'Credential ACL failure'}
Move-Item -LiteralPath $Temp -Destination $CfgPath -Force
Write-Output ('ARC_ENROLLED device_id='+$Cfg.device_id)
Write-Output 'Delete enrollment JSON from transfer location after use.'
