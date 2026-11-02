# Run on your own Windows PC after reviewing this script.
# Installs only in the USB toolkit, never system-wide.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$installer = Join-Path $env:TEMP 'portableai-dotnet-install.ps1'
$official = 'https://dot.net/v1/dotnet-install.ps1'
Write-Host "Official source: $official"
Write-Host "Target: $root\toolchains\dotnet"
$ok = Read-Host 'Download and run official Microsoft install script? Type YES to continue'
if ($ok -cne 'YES') { throw 'Cancelled' }
Invoke-WebRequest -UseBasicParsing -Uri $official -OutFile $installer
# The installer itself comes from Microsoft; pin/verify downloaded release checksums
# in high-security or offline environments before deployment.
& $installer -Channel '10.0' -Architecture 'x64' -InstallDir (Join-Path $root 'toolchains\dotnet') -NoPath
Write-Host 'SDK installed into portable toolchains/dotnet.'
