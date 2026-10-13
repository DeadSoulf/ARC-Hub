# ARC Agent v0.2: install in the current user's session, no admin required.
[CmdletBinding()]
param([int]$Port=8876, [switch]$NoStart)
$ErrorActionPreference='Stop'
if($Port -lt 1024 -or $Port -gt 65535){throw 'Port must be between 1024 and 65535'}
$Source=Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallDir=Join-Path $env:LOCALAPPDATA 'ARC'
$Dest=Join-Path $InstallDir 'bin'
$Python=(Get-Command python.exe -ErrorAction Stop).Source
$Pythonw=Join-Path (Split-Path $Python) 'pythonw.exe'
if(-not (Test-Path -LiteralPath $Pythonw)){throw 'pythonw.exe not found'}
& $Python -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11+ required'"
if($LASTEXITCODE -ne 0){throw 'Python 3.11+ required'}
New-Item -ItemType Directory -Path $Dest -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $InstallDir 'logs') -Force | Out-Null
foreach($file in @('arc_full_ops.py','arc_gui_extra.py','arc_http_full.py',
                    'arc_local.py','arc_stdio_readonly.py','arc_worker.py',
                    'arc_supervisor.py')){
 $sourceFile=Join-Path $Source $file
 if(-not (Test-Path -LiteralPath $sourceFile)){throw ('Missing '+$file)}
 Copy-Item -LiteralPath $sourceFile -Destination $Dest -Force
}
$Config=Join-Path $InstallDir 'agent.json'
if(-not (Test-Path -LiteralPath $Config)){
 $cfg=[ordered]@{
  device_id=[guid]::NewGuid().ToString('N')
  name=[Environment]::MachineName
  port=$Port
  roots=@($env:USERPROFILE)
  permissions=[ordered]@{
   status=$true; inspect=$true
   read_files=$false; screen=$false; apps=$false
   input=$false; write=$false; shell=$false
  }
  gateway_url=''
  device_token=''
 }
 [IO.File]::WriteAllText($Config,($cfg|ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false))
 $identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name
 & icacls.exe $Config '/inheritance:r' '/grant:r' ($identity+':(F)') | Out-Null
 if($LASTEXITCODE -ne 0){throw 'Cannot apply NTFS restrictions'}
}else{
 $cfg=Get-Content -LiteralPath $Config -Raw|ConvertFrom-Json
 if($cfg.port -ne $Port){Write-Host ('Retaining existing agent port '+$cfg.port)}
}
$cfg=Get-Content -LiteralPath $Config -Raw|ConvertFrom-Json
$ArgsTest=@((Join-Path $Dest 'arc_http_full.py'),'--port',"$($cfg.port)")
foreach($root in $cfg.roots){$ArgsTest+=@('--allow-root', $root)}
$ArgsTest+='--self-test'
& $Python @ArgsTest
if($LASTEXITCODE -ne 0){throw 'ARC local agent self-test failed'}
$Task='ARC Agent'
$User=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$Action=New-ScheduledTaskAction -Execute $Pythonw -Argument ('-B "'+(Join-Path $Dest 'arc_supervisor.py')+'"') -WorkingDirectory $Dest
$Logon=New-ScheduledTaskTrigger -AtLogOn -User $User
$Timer=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$Principal=New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$Settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $Task -Action $Action -Trigger @($Logon,$Timer) -Principal $Principal -Settings $Settings -Force|Out-Null
$busy=Get-NetTCPConnection -LocalPort $cfg.port -State Listen -ErrorAction SilentlyContinue
if($busy -and -not $NoStart){
 Write-Warning ('Port '+$cfg.port+' already in use. Installed without starting ARC Agent, so the existing service remains untouched.')
}elseif(-not $NoStart){
 Start-ScheduledTask -TaskName $Task
}
Write-Output 'ARC_AGENT_INSTALLED'
Write-Output ('Device ID: '+$cfg.device_id)
Write-Output ('Folder: '+$InstallDir)
Write-Output ('Task: '+$Task)
