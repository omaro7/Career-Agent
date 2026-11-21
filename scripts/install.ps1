#requires -Version 5.1
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker CLI not found. Install and start Docker Desktop.' }
docker info 1>$null 2>$null
if ($LASTEXITCODE -ne 0) { throw 'Docker engine unavailable. Start Docker Desktop (Linux containers/WSL2).' }
docker compose version | Out-Host
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose v2 is required.' }
if (-not (Test-Path '.env')) {
  $chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
  function New-Secret([int]$n=40) {
    $bytes = New-Object byte[] $n
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    -join ($bytes | ForEach-Object { $chars[[int]($_ % $chars.Length)] })
  }
  $envText = @(
    'HOST_PORT=18080',
    'MYSQL_DATABASE=bwg_career',
    'MYSQL_USER=bwg_app',
    "MYSQL_PASSWORD=$(New-Secret)",
    "MYSQL_ROOT_PASSWORD=$(New-Secret)",
    "QDRANT_API_KEY=$(New-Secret)"
  ) -join "`n"
  [System.IO.File]::WriteAllText((Join-Path $PSScriptRoot '.env'), $envText + "`n", (New-Object System.Text.UTF8Encoding($false)))
  Write-Host '[OK] .env generated. Do not share it.'
} else { Write-Host '[INFO] Existing .env kept unchanged.' }
if (Test-Path '.\images\career-images-v1.0.tar') {
  docker load -i '.\images\career-images-v1.0.tar'
  if ($LASTEXITCODE -ne 0) { throw 'Image load failed.' }
  docker compose up -d --no-build
} else {
  Write-Host '[INFO] No prebuilt bundle found: building images from source (internet required on first run).'
  docker compose up -d --build
}
if ($LASTEXITCODE -ne 0) { throw 'Compose startup failed. Check docker compose logs.' }
Write-Host '[OK] Containers started. Check: .\status.ps1'
Write-Host 'Open http://localhost:18080 after the health checks become healthy.'
