$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $MyInvocation.MyCommand.Path
$Dist=Join-Path $Root 'release'
New-Item -ItemType Directory -Force -Path $Dist|Out-Null

$WinFiles=@('arc_full_ops.py','arc_gui_extra.py','arc_http_full.py',
 'arc_local.py','arc_stdio_readonly.py','arc_worker.py','arc_supervisor.py',
 'install.ps1','install.cmd','status.ps1','uninstall.ps1','enroll.ps1','README.md')
$LinuxFiles=@('arc_hub.py','arc_admin.py','arc-hub.service',
 'install-vds.sh','Caddyfile.example','README.md')
$PluginFiles=@('plugin.json','mcp.json','README.md')

function Make-Zip($Name,$Source,$Files) {
 $stage=Join-Path $Dist ($Name+'-stage')
 $zip=Join-Path $Dist ($Name+'.zip')
 if(Test-Path $stage){Remove-Item $stage -Recurse -Force}
 New-Item -ItemType Directory -Path $stage | Out-Null
 foreach($item in $Files){ Copy-Item -LiteralPath (Join-Path $Source $item) -Destination (Join-Path $stage $item) }
 if(Test-Path $zip){Remove-Item $zip -Force}
 Compress-Archive -LiteralPath $stage -DestinationPath $zip -CompressionLevel Optimal
 Remove-Item $stage -Recurse -Force
 Write-Output ('PACKAGE '+$zip)
 Write-Output ('SHA256 '+(Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash)
}
Make-Zip 'ARC-Agent-0.2.0-Windows' (Join-Path $Root 'windows') $WinFiles
Make-Zip 'ARC-Plugin-0.2.0-TEMPLATE' (Join-Path $Root 'plugin') $PluginFiles

$stage=Join-Path $Dist 'ARC-Hub-0.2.0'
$tar=Join-Path $Dist 'ARC-Hub-0.2.0-Debian.tar.gz'
if(Test-Path $stage){Remove-Item $stage -Recurse -Force}
New-Item -ItemType Directory -Path $stage | Out-Null
foreach($item in $LinuxFiles){Copy-Item -LiteralPath (Join-Path $Root ('vds\'+$item)) -Destination (Join-Path $stage $item)}
if(Test-Path $tar){Remove-Item $tar -Force}
Push-Location $Dist
try {
 & tar.exe -czf 'ARC-Hub-0.2.0-Debian.tar.gz' 'ARC-Hub-0.2.0'
 if($LASTEXITCODE -ne 0){throw 'tar failed'}
} finally {Pop-Location}
Remove-Item $stage -Recurse -Force
Write-Output ('PACKAGE '+$tar)
Write-Output ('SHA256 '+(Get-FileHash -LiteralPath $tar -Algorithm SHA256).Hash)
$all=@(Get-ChildItem -LiteralPath $Dist -File)
if($all.Count -ne 3){throw 'unexpected release files'}
