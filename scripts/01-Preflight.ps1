param([string]$ProjectRoot='D:\06.BWG-AI\02.BWG-Career-Agent')
$ErrorActionPreference='Stop'
Set-Location $ProjectRoot
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker CLI not found' }
$services = @(docker compose config --services)
if ($LASTEXITCODE -ne 0) { throw 'docker compose config failed' }
$expected = @('career-agent','career-api','career-db','career-web','career-ollama','career-qdrant')
$missing = @($expected | Where-Object { $_ -notin $services })
if ($missing.Count) { throw "Missing Compose services: $($missing -join ', ')" }
if (@($services | Where-Object { $_ -in @('mysql','api','worker','career-work') }).Count -gt 0) { Write-Warning 'Legacy compose services found. Do not delete before verifying migration.' }
Write-Host "COMPOSE_SERVICES_OK: $($services -join ', ')"
foreach ($path in @('career-agent','career-api','career-db','career-web')) {
  if (-not (Test-Path $path)) { throw "Missing directory: $path" }
}
$running = @(docker compose ps --status running --services)
Write-Host "RUNNING_SERVICES: $($running -join ', ')"
Write-Host 'Preflight complete. Check that public-facing ports are localhost-only before enabling local_single_user.'
