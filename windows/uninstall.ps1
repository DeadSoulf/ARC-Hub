# Stops/unregisters scheduled task, keeps files/credentials for deliberate backup.
$ErrorActionPreference='Stop'
$Task='ARC Agent'
if(Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue){
 Stop-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue
 Unregister-ScheduledTask -TaskName $Task -Confirm:$false
}
Write-Output 'ARC_AGENT_TASK_REMOVED; data retained in %LOCALAPPDATA%\ARC'
