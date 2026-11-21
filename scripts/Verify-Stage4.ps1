param(
    [string]$EmployeeKey = 'R000936',
    [int]$Port = 18080
)
$ErrorActionPreference = 'Stop'
$Root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Set-Location $Root
if (!(Test-Path '.\templates\customer-profile.pptx')) {
    throw 'templates\customer-profile.pptx 파일이 없습니다.'
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker CLI를 찾을 수 없습니다.'
}
docker compose ps
$Url = "http://127.0.0.1:$Port/api/v1/career/employees/$EmployeeKey/pptx/options"
try {
    $Result = Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 15
    Write-Host ("STAGE4_OPTIONS_OK employee={0} status={1} projects={2}" -f $Result.employeeKey, $Result.status, $Result.projects.Count)
} catch {
    Write-Error 'PPTX 조회 API에 연결할 수 없습니다. docker compose logs --tail 100 api worker 를 확인하세요.'
    throw
}
