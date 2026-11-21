$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
docker compose ps
Write-Host ''
try { Invoke-RestMethod 'http://localhost:18080/api/v1/system/status' -TimeoutSec 8 | ConvertTo-Json | Write-Host }
catch { Write-Warning "HTTP check not available yet: $($_.Exception.Message)" }
