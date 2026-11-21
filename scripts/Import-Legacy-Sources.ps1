[CmdletBinding()]
param(
    [string]$ProjectRoot = 'D:\06.BWG-AI\02.BWG-Career-Agent',
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$mapping = [ordered]@{'api'='career-api';'worker'='career-agent';'web'='career-web'}
$excludeDirs = @('.git','.venv','venv','node_modules','__pycache__','.pytest_cache','.mypy_cache','.next','dist','build','coverage','.idea','.vscode')
$excludeFiles = @('.env','.env.local','.env.production','.env.development','*.pem','*.key','*.p12','*.pfx','*secret*','*credential*','*.sqlite','*.db','*.log')
$reportDir = Join-Path $ProjectRoot 'output\migration-stage02'
New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
$manifest = New-Object 'System.Collections.Generic.List[object]'
foreach ($sourceName in $mapping.Keys) {
    $source = Join-Path $ProjectRoot $sourceName
    $dest = Join-Path (Join-Path $ProjectRoot $mapping[$sourceName]) '_legacy_import'
    if (-not (Test-Path -LiteralPath $source -PathType Container)) { Write-Warning "Missing source directory: $source"; continue }
    $sourceFull = (Resolve-Path -LiteralPath $source).Path
    $all = Get-ChildItem -LiteralPath $source -Recurse -File -Force -ErrorAction Stop
    foreach ($file in $all) {
        $relative = $file.FullName.Substring($sourceFull.Length).TrimStart('\','/')
        $parts = $relative -split '[\\/]'
        if (@($parts | Where-Object { $_ -in $excludeDirs }).Count -gt 0) { continue }
        $isSensitive = $false
        foreach ($pattern in $excludeFiles) { if ($file.Name -like $pattern) { $isSensitive = $true; break } }
        if ($isSensitive) { continue }
        $target = Join-Path $dest $relative
        $status = 'PLAN'
        if (Test-Path -LiteralPath $target) {
            $srcHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
            $dstHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
            $status = if ($srcHash -eq $dstHash) { 'UNCHANGED' } else { 'CONFLICT_SKIPPED' }
        } elseif ($Apply) {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item -LiteralPath $file.FullName -Destination $target -ErrorAction Stop
            $status = 'COPIED'
        }
        $manifest.Add([pscustomobject]@{ Source=$sourceName; RelativePath=$relative; Destination=(Join-Path $mapping[$sourceName] ('_legacy_import\'+$relative)); Bytes=$file.Length; Result=$status })
    }
}
$csv = Join-Path $reportDir 'source-manifest.csv'
$manifest | Export-Csv -LiteralPath $csv -NoTypeInformation -Encoding UTF8
$keywords = 'FastAPI|APIRouter|SQLAlchemy|pymysql|mysql.connector|mysqlclient|create_engine|DATABASE_URL|uvicorn|langchain|langgraph|ollama|qdrant|axios|fetch\(|react-router|VITE_'
$matches = New-Object 'System.Collections.Generic.List[object]'
foreach ($entry in $manifest) {
    $filepath = Join-Path (Join-Path $ProjectRoot $entry.Source) $entry.RelativePath
    if ([IO.Path]::GetExtension($filepath) -notin @('.py','.tsx','.ts','.js','.jsx','.yaml','.yml','.toml','.txt','.json')) { continue }
    try {
        foreach ($match in @(Select-String -LiteralPath $filepath -Pattern $keywords -AllMatches -ErrorAction Stop)) {
            $matches.Add([pscustomobject]@{ Source=$entry.Source; File=$entry.RelativePath; Line=$match.LineNumber; Keyword=($match.Matches | Select-Object -First 1).Value })
        }
    } catch { Write-Warning "Could not scan $filepath" }
}
$matches | Export-Csv -LiteralPath (Join-Path $reportDir 'dependency-hints.csv') -NoTypeInformation -Encoding UTF8
Write-Host "Mode: $(if ($Apply) {'COPY (no overwrite)'} else {'DRY RUN'})"
Write-Host "Files examined: $($manifest.Count)"
Write-Host "Manifest: $csv"
Write-Host "Dependency hints: $(Join-Path $reportDir 'dependency-hints.csv')"
Write-Host 'Existing source directories are unchanged. Existing target files are never overwritten.'
