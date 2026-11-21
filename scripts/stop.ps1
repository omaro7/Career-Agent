$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
docker compose down
if ($LASTEXITCODE -ne 0) { throw 'Stop failed.' }
Write-Host '[OK] Stopped. Named volumes are retained.'
