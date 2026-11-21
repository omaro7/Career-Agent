$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.env')) { throw 'Run .\install.ps1 first.' }
docker compose build web api worker
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
docker compose pull mysql qdrant
if ($LASTEXITCODE -ne 0) { throw 'MySQL/Qdrant image pull failed.' }
New-Item -Type Directory -Force '.\images' | Out-Null
docker save -o '.\images\career-images-v1.0.tar' 'bwg-career-agent/web:1.0.0' 'bwg-career-agent/api:1.0.0' 'bwg-career-agent/worker:1.0.0' 'mysql:8.4' 'qdrant/qdrant:v1.15.5'
if ($LASTEXITCODE -ne 0) { throw 'Image export failed.' }
Write-Host '[OK] Exported all five service images (web/api/worker/mysql/qdrant).'
