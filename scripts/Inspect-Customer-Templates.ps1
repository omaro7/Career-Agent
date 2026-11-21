# Stage 4 diagnostics only; PPTX generation remains React-only.
# Examples:
#   .\scripts\Inspect-Customer-Templates.ps1
#   .\scripts\Inspect-Customer-Templates.ps1 -TemplateId customer-profile-woori.pptx
[CmdletBinding()]
param([string]$TemplateId = '')
$ErrorActionPreference = 'Stop'
$Root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Push-Location $Root
try {
    if ($TemplateId -and $TemplateId -notmatch '^[a-zA-Z0-9._-]+\.pptx$') {
        throw 'TemplateId must be one filename only (letters/digits/dash/underscore/dot).'
    }
    $python = @'
import json,sys
from pathlib import Path
from career_pptx import available_templates, inspect_template
root=Path('/app/templates')
if len(sys.argv)>1:
    f=root/sys.argv[1]
    if not f.is_file() or f.is_symlink():
        raise SystemExit('Template not found')
    result=inspect_template(f)
else:
    result=available_templates(root)
print(json.dumps(result,ensure_ascii=False,indent=2))
'@
    if ($TemplateId) {
        & docker compose exec -T worker python -c $python $TemplateId
    } else {
        & docker compose exec -T worker python -c $python
    }
    if ($LASTEXITCODE -ne 0) { throw "Inspect-Customer-Templates failed: exit $LASTEXITCODE" }
} finally {
    Pop-Location
}
