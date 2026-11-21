"""DOCX review -> existing mapped customer PPTX Agent. Excel routes are unchanged.

Expected existing modules: app.db, app.profiles, app.template_registry.
The API never writes directly to customer PPTX slide shapes. Layout lives in the Agent.
"""
from __future__ import annotations

import io
import os
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any

import httpx
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from psycopg.types.json import Jsonb

from .db import connect
from .profiles import TENANT
from .template_registry import checked_path, get_item
from .word_profile import FIELDS, from_blocks, worker_payload, fill_missing_project_dates, enrich_projects_from_review, recover_periods_from_reviewed, normalize_profile_period_display  # STAGE02_7_PERIOD_DISPLAY_FIX

from .word_saved_profile import sync_word_saved_profile

router = APIRouter(prefix='/api/v1/career/word-imports', tags=['word-imports'])
BASE = lambda: Path(os.getenv('WORD_IMPORT_DIR', '/app/uploads/word-imports')).resolve()
AGENT = lambda: os.getenv('AGENT_BASE_URL', 'http://career-agent:8001').rstrip('/')
MAX_BYTES = 15 * 1024 * 1024
MAX_BLOCKS = 300
MAX_CHARS = 150_000
PPTX_TYPE = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'


class ReviewPayload(BaseModel):
    title: str = Field(default='경력기술서', max_length=300)
    blocks: list[dict[str, str]] = Field(min_length=1, max_length=MAX_BLOCKS)


class StructuredPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(default='', max_length=120)
    affiliation: str = Field(default='', max_length=120)
    positionTitle: str = Field(default='', max_length=120)
    birthDate: str = Field(default='', max_length=20)
    ageYears: str = Field(default='', max_length=3)
    educationText: str = Field(default='', max_length=250)
    degree: str = Field(default='', max_length=80)
    certificationsText: str = Field(default='', max_length=300)
    displayGrade: str = Field(default='', max_length=80)
    businessRole: str = Field(default='', max_length=120)
    assignmentPeriod: str = Field(default='', max_length=120)
    assignmentMonths: str = Field(default='', max_length=10)
    allocationRate: str = Field(default='', max_length=16)
    customerName: str = Field(default='', max_length=100)
    projects: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    extractionWarnings: list[str] = Field(default_factory=list, max_length=30)


class RenderPayload(BaseModel):
    templateId: str = Field(min_length=1, max_length=180)
    asOfDate: str = Field(default='', max_length=10)
    confirmReviewed: bool = False


def _same_site(request: Request):
    site = request.headers.get('sec-fetch-site')
    if site is not None and site not in ('same-origin', 'none'):
        raise HTTPException(403, '외부 사이트에서 Word 경력정보를 변경할 수 없습니다.')
    # Local operator assumption only; Sec-Fetch-Site is NOT authentication.


def _load(import_id: uuid.UUID) -> dict[str, Any]:
    with connect() as cn:
        row = cn.execute('''SELECT id, original_filename, extracted, reviewed, structured,
                                   status, template_id, output_path, render_meta
                            FROM career_word_import WHERE id=%s AND tenant_id=%s''',
                         (import_id, TENANT)).fetchone()
    if not row:
        raise HTTPException(404, '등록된 Word 문서를 찾을 수 없습니다.')
    return dict(row)


def _public(row: dict[str, Any]) -> dict[str, Any]:
    # STAGE02_7_PERIOD_PUBLIC_BACKFILL: enrich existing saved Word on GET
    # without changing the PostgreSQL row. Save remains an explicit action.
    structured = row.get('structured') or {}
    reviewed = row.get('reviewed') or {}
    if isinstance(structured, dict) and isinstance(reviewed, dict):
        structured, _ = enrich_projects_from_review(structured, reviewed)
        structured = fill_missing_project_dates(structured)
    # STAGE02_7_PERIOD_EVIDENCE_FIX: read-time preview only; no DB mutation.
    structured = locals().get('structured', row.get('structured') or {})
    structured, _ = recover_periods_from_reviewed(structured, row.get('reviewed') or {})
    # Normalize only the response; PostgreSQL and raw Word blocks stay unchanged.
    structured = normalize_profile_period_display(locals().get('structured', row.get('structured') or {}))
    return {'id': str(row['id']), 'filename': row['original_filename'],
            'status': row['status'], 'extracted': row['extracted'],
            'reviewed': row['reviewed'], 'structured': structured,
            'templateId': row['template_id'], 'hasOutput': bool(row['output_path']),
            'renderMeta': row.get('render_meta') or {}}


def _validate_package(data: bytes):
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise HTTPException(422, '정상적인 DOCX ZIP 구조가 아닙니다.')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > 3000 or sum(item.file_size for item in members) > 100_000_000:
                raise HTTPException(413, 'DOCX 압축 내용이 허용 크기를 초과했습니다.')
            names = {item.filename for item in members}
            if 'word/document.xml' not in names:
                raise HTTPException(422, 'Word 본문이 없는 DOCX입니다.')
            if any('..' in item.filename or '\\' in item.filename or item.filename.startswith('/')
                   or item.filename.lower().endswith(('.exe', '.dll'))
                   or 'vbaproject' in item.filename.lower() or '/activex/' in item.filename.lower()
                   for item in members):
                raise HTTPException(422, '허용하지 않는 DOCX 내부 파일이 포함되어 있습니다.')
    except (OSError, zipfile.BadZipFile) as exc:
        raise HTTPException(422, 'DOCX 파일을 읽을 수 없습니다.') from exc


def _parse_docx(data: bytes) -> dict[str, Any]:
    _validate_package(data)
    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:
        raise HTTPException(422, 'DOCX 문서를 파싱할 수 없습니다.') from exc
    blocks: list[dict[str, str]] = []
    characters = 0
    for element in document.element.body.iterchildren():
        if element.tag == qn('w:p'):
            paragraph = Paragraph(element, document)
            value = paragraph.text.strip()
            if not value:
                continue
            style = (paragraph.style.name if paragraph.style else '').casefold()
            kind = 'heading' if style.startswith(('heading', '제목')) else 'paragraph'
            blocks.append({'kind': kind, 'text': value})
            characters += len(value)
        elif element.tag == qn('w:tbl'):
            table = Table(element, document)
            for row in table.rows:
                value = ' | '.join(cell.text.strip().replace('\n', ' / ') for cell in row.cells)
                if value.strip(' |'):
                    blocks.append({'kind': 'table', 'text': value})
                    characters += len(value)
                if len(blocks) > MAX_BLOCKS or characters > MAX_CHARS:
                    raise HTTPException(422, 'DOCX는 최대 300블록 및 15만 글자까지 지원합니다.')
        if len(blocks) > MAX_BLOCKS or characters > MAX_CHARS:
            raise HTTPException(422, 'DOCX는 최대 300블록 및 15만 글자까지 지원합니다.')
    if not blocks:
        raise HTTPException(422, '읽을 수 있는 Word 문단이나 표가 없습니다. 이미지 기반 Word는 지원하지 않습니다.')
    return {'title': next((b['text'][:300] for b in blocks if b['kind'] == 'heading'), '경력기술서'),
            'blocks': blocks}


def _file_dir(import_id: uuid.UUID) -> Path:
    return BASE() / str(import_id)


@router.post('', status_code=201)
async def upload_word(request: Request, file: UploadFile = File(...)):
    _same_site(request)
    filename = (file.filename or '').replace('\\', '/').split('/')[-1]
    if not filename.lower().endswith('.docx') or len(filename) > 240:
        raise HTTPException(415, '15MB 이하의 .docx 파일만 등록할 수 있습니다.')
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, 'Word 파일은 최대 15MB까지 지원합니다.')
    draft = _parse_docx(data)
    structured = from_blocks(draft)
    import_id = uuid.uuid4()
    folder = _file_dir(import_id)
    folder.mkdir(parents=True, exist_ok=False)
    source = folder / 'source.docx'
    source.write_bytes(data)
    try:
        with connect() as cn:
            cn.execute('''INSERT INTO career_word_import
                          (id, tenant_id, original_filename, source_path, extracted, reviewed, structured)
                          VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                       (import_id, TENANT, filename, str(source), Jsonb(draft), Jsonb(draft), Jsonb(structured)))
    except Exception:
        source.unlink(missing_ok=True)
        folder.rmdir()
        raise
    return _public(_load(import_id))


@router.get('/{import_id}')
def get_word(import_id: uuid.UUID):
    # STAGE02_7_REGISTER_AUTOFILL_READ: show existing Word projects
    # with missing fields filled, but never silently alter persisted data.
    row = _load(import_id)
    public = _public(row)
    from .word_profile import enrich_projects_from_review
    enriched, changed = enrich_projects_from_review(row['structured'] or {}, row['reviewed'] or {})
    if changed:
        public['structured'] = enriched
        public['autoExtractedUnsaved'] = True
    return public


@router.put('/{import_id}/review')
def update_review(import_id: uuid.UUID, payload: ReviewPayload, request: Request):
    _same_site(request)
    _load(import_id)
    clean = []
    size = 0
    for block in payload.blocks:
        kind = block.get('kind', 'paragraph')
        text = str(block.get('text') or '').strip()
        if kind not in ('heading', 'paragraph', 'table'):
            raise HTTPException(422, '지원하지 않는 Word 블록 형식입니다.')
        if text:
            clean.append({'kind': kind, 'text': text})
            size += len(text)
    if not clean or size > MAX_CHARS:
        raise HTTPException(422, '내용이 없거나 글자 수 제한을 초과했습니다.')
    draft = {'title': payload.title, 'blocks': clean}
    structured = from_blocks(draft)
    with connect() as cn:
        cn.execute('''UPDATE career_word_import
                      SET reviewed=%s,structured=%s,status='REVIEW',template_id=NULL,
                          output_path=NULL,render_meta=NULL,updated_at=NOW()
                      WHERE id=%s AND tenant_id=%s''',
                   (Jsonb(draft), Jsonb(structured), import_id, TENANT))
    return _public(_load(import_id))


def persist_word_structured_tx(cn, import_id: uuid.UUID, value: dict[str, Any]) -> str:
    """Write Word and saved profile using the CALLER'S PostgreSQL transaction.

    Shared by user PUT /structured and approved AI apply. Row lock serializes
    commits with other structured PUT requests. Failure rolls back both tables.
    """
    current = cn.execute(
        'SELECT id FROM career_word_import WHERE id=%s AND tenant_id=%s FOR UPDATE',
        (import_id, TENANT)).fetchone()
    if not current:
        raise HTTPException(404, 'Word 등록 데이터를 찾지 못했습니다.')
    result = cn.execute('''UPDATE career_word_import
                      SET structured=%s,status='REVIEWED',template_id=NULL,
                          output_path=NULL,render_meta=NULL,updated_at=NOW()
                      WHERE id=%s AND tenant_id=%s''',
                   (Jsonb(value), import_id, TENANT))
    if result.rowcount != 1:
        raise HTTPException(409, 'Word 원본 저장 중 동시 변경이 발생했습니다.')
    # Existing employee/education/certification/career_project sync, same tx.
    return sync_word_saved_profile(cn, import_id, value, TENANT)


@router.put('/{import_id}/structured')
def update_structured(import_id: uuid.UUID, payload: StructuredPayload, request: Request):
    _same_site(request)
    _load(import_id)
    value = fill_missing_project_dates(payload.model_dump())
    # Keep existing validation and Word-save behavior unchanged.
    if len(str(value).encode('utf-8')) > 140_000:
        raise HTTPException(413, '경력 데이터 크기 제한을 초과했습니다.')
    with connect() as cn:
        persist_word_structured_tx(cn, import_id, value)
    return _public(_load(import_id))


@router.post('/{import_id}/render')
async def render_pptx(import_id: uuid.UUID, body: RenderPayload, request: Request):
    _same_site(request)
    row = _load(import_id)
    if row['status'] not in ('REVIEWED', 'RENDERED') or not body.confirmReviewed:
        raise HTTPException(409, '구조화된 경력정보를 저장하고 검토용 출력에 동의해 주세요.')
    checked_path(body.templateId)
    item = get_item(body.templateId)
    if item.get('status') != 'READY' or item.get('isActive') is False:
        raise HTTPException(409, '선택한 템플릿은 매핑 완료·활성화 상태여야 합니다.')
    try:
        payload = worker_payload(row['structured'], template_id=body.templateId,
                                 template_customer_name=item.get('customerName', ''),
                                 as_of_date=body.asOfDate)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    import json
    raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    if len(raw) > 245_000:
        raise HTTPException(413, '렌더링 요청이 제한 크기를 초과했습니다.')
    try:
        async with httpx.AsyncClient(timeout=155) as client:
            response = await client.post(AGENT() + '/render-customer-pptx', content=raw,
                                         headers={'Content-Type': 'application/json'})
    except httpx.RequestError as exc:
        raise HTTPException(502, 'PPTX Agent 연결에 실패했습니다.') from exc
    if response.status_code != 200:
        # A 422 means the existing customer template renderer rejected the mapping/data.
        raise HTTPException(422 if response.status_code == 422 else 502,
                            'PPTX Agent가 템플릿 매핑 또는 데이터를 거부했습니다. 템플릿 분석과 검토 필드를 확인하세요.')
    result = response.content
    if not 100 <= len(result) <= 65_000_000 or not zipfile.is_zipfile(io.BytesIO(result)):
        raise HTTPException(502, 'PPTX Agent에서 올바른 파일을 받지 못했습니다.')
    with zipfile.ZipFile(io.BytesIO(result)) as zipped:
        if 'ppt/presentation.xml' not in zipped.namelist():
            raise HTTPException(502, '생성된 PPTX 파일 구조가 올바르지 않습니다.')
    output_dir = _file_dir(import_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / 'career-result.pptx'
    fd, staging = tempfile.mkstemp(prefix='.result-', suffix='.pptx', dir=output_dir)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(result)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, target)
    finally:
        if os.path.exists(staging): os.unlink(staging)
    meta = {'pageCount': response.headers.get('x-pptx-page-count', ''),
            'projectCount': response.headers.get('x-pptx-project-count', ''),
            'templateId': body.templateId}
    with connect() as cn:
        cn.execute('''UPDATE career_word_import
                      SET status='RENDERED',template_id=%s,output_path=%s,render_meta=%s,updated_at=NOW()
                      WHERE id=%s AND tenant_id=%s''',
                   (body.templateId, str(target), Jsonb(meta), import_id, TENANT))
    return {'id': str(import_id), 'status': 'RENDERED', 'renderMeta': meta,
            'downloadUrl': f'/api/v1/career/word-imports/{import_id}/download'}


@router.get('/{import_id}/download')
def download_pptx(import_id: uuid.UUID):
    row = _load(import_id)
    if not row['output_path']:
        raise HTTPException(404, '생성된 PPTX 파일이 없습니다.')
    file = Path(row['output_path'])
    if not file.is_file() or file.is_symlink() or file.resolve().parent != _file_dir(import_id).resolve():
        raise HTTPException(404, '생성된 PPTX 파일을 찾을 수 없습니다.')
    return FileResponse(file, filename='career-result.pptx', media_type=PPTX_TYPE,
                        headers={'Cache-Control': 'no-store'})


# STAGE04_3_1_WORD_PROFILE_SELECTOR: read-only, tenant-scoped Word profiles for AI chat.
# STAGE04_3_2_WORD_REPRESENTATIVE: UI grouping only; do not delete or merge saved imports.
@router.get('')
def list_word_profiles(include_history: bool = False):
    """One newest Word import per (person name, filename) by default.

    Older versions remain accessible via ?include_history=true. Grouping is
    purely presentational and NEVER merges employee keys or edits source rows.
    A blank person name uses the document's own UUID as the grouping key.
    """
    from fastapi.responses import JSONResponse

    with connect() as cn:
        rows = cn.execute("""
            WITH owned AS (
                SELECT wi.id, wi.original_filename, wi.status, wi.structured,
                       wi.created_at, rs.status AS rag_status,
                       COALESCE(NULLIF(BTRIM(wi.structured->>'name'), ''), wi.id::text)
                           AS selector_person_key
                  FROM public.career_word_import AS wi
                  LEFT JOIN public.career_word_rag_sync AS rs
                    ON rs.import_id = wi.id::text
                 WHERE wi.tenant_id = %s
            ), ranked AS (
                SELECT owned.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY selector_person_key, original_filename
                           ORDER BY created_at DESC, id DESC
                       ) AS version_rank,
                       COUNT(*) OVER (
                           PARTITION BY selector_person_key, original_filename
                       ) AS history_count
                  FROM owned
            )
            SELECT id, original_filename, status, structured,
                   created_at, rag_status, version_rank, history_count
              FROM ranked
             WHERE (%s OR version_rank = 1)
             ORDER BY created_at DESC, id DESC
             LIMIT 200
        """, (TENANT, include_history)).fetchall()

    result = []
    for row in rows:
        structured = row['structured'] if isinstance(row['structured'], dict) else {}
        projects = structured.get('projects')
        word_id = row['id'] if isinstance(row['id'], uuid.UUID) else uuid.UUID(str(row['id']))
        result.append({
            'import_id': str(word_id),
            'profile_id': 'word_' + word_id.hex,
            'name': str(structured.get('name') or '')[:120],
            'filename': str(row['original_filename'] or '')[:240],
            'status': str(row['status'] or ''),
            'rag_status': str(row['rag_status'] or 'NOT_INDEXED'),
            'project_count': len(projects) if isinstance(projects, list) else 0,
            'created_at': row['created_at'].isoformat() if row['created_at'] else None,
            'history_count': int(row['history_count']),
            'is_latest': row['version_rank'] == 1,
        })
    return JSONResponse(content=result, headers={'Cache-Control': 'no-store'})
