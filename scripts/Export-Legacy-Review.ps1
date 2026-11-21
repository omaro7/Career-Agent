[CmdletBinding()]
param(
    [string]$ProjectRoot = 'D:\06.BWG-AI\02.BWG-Career-Agent',
    [string]$OutputZip = '',
    [int]$MaxFileMB = 3
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) { throw "Project root not found: $ProjectRoot" }
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
if (-not $OutputZip) { $OutputZip = Join-Path $ProjectRoot 'output\migration-stage02\legacy-source-review.zip' }
$TempDir = Join-Path ([IO.Path]::GetTempPath()) ("bwg-legacy-review-" + [Guid]::NewGuid().ToString('N'))
$ExcludedDirs = @('node_modules','.git','.venv','venv','__pycache__','dist','build','coverage','.next','.pytest_cache','.mypy_cache','.idea','.vscode','logs','secrets','credentials','certs','certificates')
$ExcludedNames = @('.env','.env.local','.env.production','.env.development','id_rsa','id_ed25519','credentials.json','client_secret.json','token.json','secret.json','bridge-token.txt','.npmrc','.pypirc')
$ExcludedExtensions = @('.sqlite','.sqlite3','.db','.pem','.key','.p12','.pfx','.jks','.keystore','.crt','.cer','.der','.log','.zip','.7z','.tar','.gz','.mp4','.wav','.mp3','.pdf','.docx','.png','.jpg','.jpeg','.webp')
$AllowedRootFiles = @('compose.yaml','compose.stage02.yml','.env.example','.env.stage02.example')
$Sources = @('api','worker','web','db','tests')
$Rows = New-Object 'System.Collections.Generic.List[object]'
try {
    New-Item -ItemType Directory -Path $TempDir -Force | Out-Null
    foreach ($source in $Sources) {
        $sourcePath = Join-Path $ProjectRoot $source
        if (-not (Test-Path -LiteralPath $sourcePath -PathType Container)) { continue }
        Get-ChildItem -LiteralPath $sourcePath -Recurse -File -Force | ForEach-Object {
            $f = $_
            $relative = $f.FullName.Substring($ProjectRoot.Length).TrimStart('\','/')
            $parts = $relative -split '[\\/]'
            $skip = $false; $reason = ''
            foreach ($part in $parts) { if ($ExcludedDirs -contains $part) { $skip = $true; $reason = 'excluded directory'; break } }
            if (-not $skip -and ($ExcludedNames -contains $f.Name -or $f.Name -match '^\.env\.' -and $f.Name -notmatch '\.example$')) { $skip = $true; $reason = 'sensitive filename' }
            if (-not $skip -and $ExcludedExtensions -contains $f.Extension.ToLowerInvariant()) { $skip = $true; $reason = 'binary/sensitive extension' }
            if (-not $skip -and $f.Length -gt ($MaxFileMB * 1MB)) { $skip = $true; $reason = 'file too large' }
            if (-not $skip) {
                $destination = Join-Path $TempDir $relative
                New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
                Copy-Item -LiteralPath $f.FullName -Destination $destination -Force
            }
            $Rows.Add([pscustomobject]@{Path=$relative;Included=(-not $skip);Reason=$reason;Bytes=$f.Length})
        }
    }
    foreach ($rootFile in $AllowedRootFiles) {
        $src = Join-Path $ProjectRoot $rootFile
        if (Test-Path -LiteralPath $src -PathType Leaf) {
            Copy-Item -LiteralPath $src -Destination (Join-Path $TempDir $rootFile)
            $Rows.Add([pscustomobject]@{Path=$rootFile;Included=$true;Reason='';Bytes=(Get-Item -LiteralPath $src).Length})
        }
    }
    $Rows | Export-Csv -LiteralPath (Join-Path $TempDir 'manifest.csv') -NoTypeInformation -Encoding UTF8
    @'
BEFORE SHARING THIS ARCHIVE:
1. Review all included files for passwords, access tokens, API keys and personal information.
2. The script skips common secret names, but cannot guarantee all secrets are excluded.
3. The legacy project remains unchanged. No Docker containers or volumes were modified.
4. The contents are source review copies, not a deployable migration.
'@ | Set-Content -LiteralPath (Join-Path $TempDir 'REVIEW-BEFORE-SHARING.txt') -Encoding UTF8
    $parent = Split-Path -Parent $OutputZip
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    if (Test-Path -LiteralPath $OutputZip) { throw "Output ZIP already exists: $OutputZip (specify -OutputZip to avoid overwriting)" }
    Compress-Archive -Path (Join-Path $TempDir '*') -DestinationPath $OutputZip -CompressionLevel Optimal
    Write-Host "CREATED: $OutputZip"
    Write-Host "INCLUDED: $(($Rows | Where-Object Included).Count); EXCLUDED: $(($Rows | Where-Object { -not $_.Included }).Count)"
    Write-Warning 'Inspect ZIP for secrets and personal information before sharing.'
}
finally {
    if (Test-Path -LiteralPath $TempDir) { Remove-Item -LiteralPath $TempDir -Recurse -Force }
}
