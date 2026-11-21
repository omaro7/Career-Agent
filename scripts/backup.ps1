$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.env')) { throw 'Missing .env' }
New-Item -ItemType Directory -Force '.\backups' | Out-Null
$stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
$backupFile=Join-Path $PSScriptRoot "backups\mysql-$stamp.sql"
# mysqldump streams from the running container; never print the password.
$cid=(docker compose ps -q mysql)
if (-not $cid) { throw 'MySQL container is not running.' }
# Redirect shell output to UTF-8 SQL without PowerShell 5.1 UTF-16 redirection.
$psi=New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName='docker'
$psi.Arguments='compose exec -T mysql sh -c ''exec mysqldump -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" --single-transaction --routines --triggers "$MYSQL_DATABASE"'''
$psi.UseShellExecute=$false; $psi.RedirectStandardOutput=$true; $psi.RedirectStandardError=$true
$p=[System.Diagnostics.Process]::Start($psi)
$stream=[System.IO.File]::Create($backupFile)
try { $p.StandardOutput.BaseStream.CopyTo($stream) } finally { $stream.Dispose() }
$err=$p.StandardError.ReadToEnd(); $p.WaitForExit()
if ($p.ExitCode -ne 0) { Remove-Item $backupFile -Force; throw "Backup failed: $err" }
Write-Host "[OK] Database backup: $backupFile (contains personal data; protect this file)"
