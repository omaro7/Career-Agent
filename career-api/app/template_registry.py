'''Port of CareerPptxTemplateController/RegistryService (operator-only).
Original MySQL records are stored in career_pptx_template_registry on PostgreSQL.
Requires the old Worker /pptx/templates and /inspect endpoints in career-agent.
Auto-map remains intentionally disabled until the agent actually provides /auto-map.
'''
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

import httpx
from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel
from .db import connect
from .profiles import TENANT

router = APIRouter(prefix='/api/v1/career/pptx/templates', tags=['pptx-templates'])
MAX_PPTX = 40 * 1024 * 1024
MAX_UNZIPPED = 250 * 1024 * 1024
MAX_MAPPING = 64000
SAFE = re.compile(r'^[A-Za-z0-9_.-]{1,180}\.pptx$', re.I)
MANAGED = re.compile(r'^tpl-[0-9a-fA-F-]{36}\.pptx$')
BASE = lambda: Path(os.getenv('CAREER_TEMPLATE_DIR', '/app/templates')).resolve()
AGENT = lambda: os.getenv('AGENT_BASE_URL', 'http://career-agent:8001').rstrip('/')

class AdoptRequest(BaseModel):
    displayName: str
    customerName: str

class MappingRequest(BaseModel):
    mappingJson: str

class ActiveRequest(BaseModel):
    active: bool


def same_site(request: Request):
    site = request.headers.get('sec-fetch-site')
    if site is not None and site not in ('same-origin', 'none'):
        raise HTTPException(403, '외부 사이트에서 템플릿을 변경할 수 없습니다.')
    # This alone is NOT authentication. Bind API to localhost until auth is added.


def checked_path(template_id: str) -> Path:
    if not SAFE.fullmatch(template_id) or template_id.startswith('.'):
        raise HTTPException(400, '허용되지 않은 템플릿 ID입니다.')
    base = BASE()
    candidate = base / template_id
    if candidate.is_symlink() or candidate.parent != base:
        raise HTTPException(400, '템플릿 경로가 올바르지 않습니다.')
    return candidate


def mapping_path(p: Path) -> Path:
    return p.with_suffix('.mapping.json')


def valid_name(s: str, label: str, max_length=120) -> str:
    result = (s or '').strip()
    if not 1 <= len(result) <= max_length:
        raise HTTPException(400, f'{label}은(는) 1~{max_length}자여야 합니다.')
    return result


def validate_mapping(value: str | None):
    if not value or not value.strip():
        return None
    if len(value) > MAX_MAPPING:
        raise HTTPException(413, '매핑 JSON은 64KB 이하여야 합니다.')
    try:
        obj = json.loads(value)
    except (ValueError, TypeError):
        raise HTTPException(400, '매핑 JSON은 유효한 객체여야 합니다.')
    if not isinstance(obj, dict):
        raise HTTPException(400, '매핑 JSON은 유효한 객체여야 합니다.')
    return value


def validate_pptx(raw: bytes):
    if len(raw) < 100 or len(raw) > MAX_PPTX:
        raise HTTPException(413, 'PPTX는 최대 40MB입니다.')
    count, total, has_presentation = 0, 0, False
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for info in archive.infolist():
                count += 1
                name = info.filename
                low = name.lower()
                if (count > 6000 or '..' in name or '\\' in name or name.startswith('/')
                    or 'vbaproject' in low or low.startswith(('ppt/activex/', 'ppt/embeddings/'))
                    or low.endswith(('.exe', '.dll'))):
                    raise HTTPException(422, '보안상 허용되지 않은 PPTX 패키지 구성입니다.')
                total += info.file_size
                if total > MAX_UNZIPPED:
                    raise HTTPException(413, '압축 해제 용량 제한을 초과했습니다.')
                if name == 'ppt/presentation.xml':
                    has_presentation = True
            if not has_presentation:
                raise HTTPException(422, 'PPTX 구조가 올바르지 않습니다.')
            # Check actual decompression, not solely declared file sizes.
            actual = 0
            for info in archive.infolist():
                if info.is_dir(): continue
                with archive.open(info) as item:
                    while True:
                        chunk = item.read(8192)
                        if not chunk: break
                        actual += len(chunk)
                        if actual > MAX_UNZIPPED:
                            raise HTTPException(413, '압축 해제 용량 제한을 초과했습니다.')
    except (zipfile.BadZipFile, RuntimeError, EOFError, OSError) as exc:
        raise HTTPException(422, 'PPTX 파일 구조를 읽을 수 없습니다.') from exc


def atomic_write(path: Path, payload: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.upload-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def agent_json(path: str, timeout=45):
    try:
        with httpx.Client(timeout=timeout) as cli:
            resp = cli.get(AGENT() + path)
            resp.raise_for_status()
            if len(resp.content) > 150000:
                raise ValueError('Agent response too large')
            return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, 'Agent 템플릿 분석 서비스에 연결할 수 없습니다.') from exc


def agent_list():
    payload = agent_json('/pptx/templates')
    if not isinstance(payload, dict) or not isinstance(payload.get('templates'), list):
        raise HTTPException(502, 'Agent 템플릿 목록 응답이 잘못되었습니다.')
    return payload['templates']


def agent_item(template_id):
    return next((x for x in agent_list() if x.get('templateId') == template_id), None)


SELECT_SQL = '''SELECT template_id,customer_name,display_name,original_file_name,file_sha256,
 file_size,template_version,analysis_status,layout_mode,is_active,created_at,updated_at
 FROM career_pptx_template_registry WHERE tenant_id=%s'''


def rows():
    with connect() as cn:
        return cn.execute(SELECT_SQL, (TENANT,)).fetchall()


def record(template_id):
    return next((r for r in rows() if r['template_id'] == template_id), None)


def enriched(item, meta):
    result = dict(item)
    if meta:
        result.update(registered=True, customerName=meta['customer_name'], displayName=meta['display_name'],
            originalFileName=meta['original_file_name'], fileSize=meta['file_size'],
            templateVersion=meta['template_version'], isActive=bool(meta['is_active']))
        if not meta['is_active']:
            result.update(status='DISABLED', message='관리자가 비활성화한 템플릿입니다.')
    else:
        result.update(registered=False, customerName='', originalFileName=item.get('fileName'), isActive=True)
    return result


def get_item(template_id):
    item = agent_item(template_id)
    if not item:
        raise HTTPException(404, '템플릿을 찾을 수 없습니다.')
    return enriched(item, record(template_id))


@router.get('')
def list_templates():
    meta = {r['template_id']: r for r in rows()}
    items = [enriched(i, meta.get(i.get('templateId'))) for i in agent_list()]
    ready = [i['templateId'] for i in items if i.get('status') == 'READY']
    default = 'customer-profile.pptx' if 'customer-profile.pptx' in ready else (ready[0] if ready else '')
    return {'templates': items, 'defaultTemplateId': default}


@router.get('/{template_id}/analysis')
def analyze(template_id: str):
    checked_path(template_id)
    if not agent_item(template_id): raise HTTPException(404, '등록되지 않은 템플릿입니다.')
    return agent_json('/pptx/templates/' + template_id + '/inspect')


def save_record(template_id, customer, display, original, raw, status):
    with connect() as cn:
        cn.execute('''INSERT INTO career_pptx_template_registry
        (template_id,tenant_id,customer_name,display_name,original_file_name,file_sha256,file_size,
         template_version,analysis_status,layout_mode,is_active)
        VALUES (%s,%s,%s,%s,%s,%s,%s,1,%s,%s,TRUE)''',
        (template_id,TENANT,customer,display,original,hashlib.sha256(raw).hexdigest(),
         len(raw),status.get('status','INVALID'),status.get('layoutMode') or ''))
    return get_item(template_id)


@router.post('', status_code=201)
async def upload(request: Request, file: UploadFile = File(...), displayName: str = Form(...),
                 customerName: str = Form(...), mappingJson: str | None = Form(None)):
    same_site(request)
    display = valid_name(displayName, '템플릿 표시명')
    customer = valid_name(customerName, '고객사명')
    original = (file.filename or '').replace('\\','/').split('/')[-1].strip()
    if len(original) > 240 or not original.lower().endswith('.pptx'):
        raise HTTPException(400, 'PPTX 파일을 선택하세요.')
    raw = await file.read(MAX_PPTX + 1)
    validate_pptx(raw)
    mapping = validate_mapping(mappingJson)
    template_id = f'tpl-{uuid.uuid4()}.pptx'
    path = checked_path(template_id)
    mp = mapping_path(path)
    try:
        atomic_write(path, raw)
        if mapping is not None:
            atomic_write(mp, mapping.encode('utf-8'))
        status = agent_item(template_id)
        if not status or status.get('status') == 'INVALID':
            raise HTTPException(422, 'Agent가 PPTX 구조를 인식하지 못했습니다.')
        item = save_record(template_id, customer, display, original, raw, status)
        item.update(autoMappingApplied=False, autoMappingWarnings=['자동 매핑은 Agent 지원 전까지 비활성화되어 있습니다.'])
        return item
    except Exception:
        mp.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        raise


@router.post('/{template_id}/adopt')
def adopt(template_id: str, body: AdoptRequest, request: Request):
    same_site(request)
    path = checked_path(template_id)
    if not path.is_file() or path.is_symlink(): raise HTTPException(404, '템플릿 파일이 없습니다.')
    if record(template_id): raise HTTPException(409, '이미 등록된 템플릿입니다.')
    raw = path.read_bytes()
    validate_pptx(raw)
    status = agent_item(template_id)
    if not status or status.get('status') == 'INVALID': raise HTTPException(422, '유효하지 않은 템플릿입니다.')
    return save_record(template_id,valid_name(body.customerName,'고객사명'),valid_name(body.displayName,'템플릿 표시명'),template_id,raw,status)


@router.put('/{template_id}/mapping')
def update_mapping(template_id: str, body: MappingRequest, request: Request):
    same_site(request)
    if not record(template_id): raise HTTPException(404, 'DB에 등록되지 않은 템플릿입니다.')
    path = checked_path(template_id)
    if not path.is_file() or path.is_symlink(): raise HTTPException(404, '템플릿 파일이 없습니다.')
    mapping = validate_mapping(body.mappingJson)
    if mapping is None: raise HTTPException(400, '매핑 JSON을 입력하세요.')
    mp = mapping_path(path)
    prior = mp.read_bytes() if mp.exists() and not mp.is_symlink() else None
    try:
        atomic_write(mp, mapping.encode('utf-8'))
        status = agent_item(template_id)
        if not status or status.get('status') != 'READY':
            raise HTTPException(422, '매핑을 적용할 수 없습니다. shape 이름 및 행·열 위치를 확인하세요.')
        with connect() as cn:
            cn.execute('''UPDATE career_pptx_template_registry SET template_version=template_version+1,
            analysis_status=%s,layout_mode=%s,updated_at=CURRENT_TIMESTAMP
            WHERE tenant_id=%s AND template_id=%s''',(status['status'],status.get('layoutMode') or '',TENANT,template_id))
        return get_item(template_id)
    except Exception:
        if prior is None: mp.unlink(missing_ok=True)
        else: atomic_write(mp, prior)
        raise


@router.put('/{template_id}/active')
def set_active(template_id: str, body: ActiveRequest, request: Request):
    same_site(request)
    if not record(template_id): raise HTTPException(404, 'DB에 등록되지 않은 템플릿입니다.')
    if body.active and (not (status := agent_item(template_id)) or status.get('status') != 'READY'):
        raise HTTPException(409, '매핑 완료된 템플릿만 활성화할 수 있습니다.')
    with connect() as cn:
        cn.execute('''UPDATE career_pptx_template_registry SET is_active=%s,updated_at=CURRENT_TIMESTAMP
                    WHERE tenant_id=%s AND template_id=%s''',(body.active,TENANT,template_id))
    return get_item(template_id)


@router.delete('/{template_id}')
def delete_uploaded(template_id: str, request: Request,
                    x_confirm_template_id: str | None = Header(None, alias='X-Confirm-Template-Id')):
    same_site(request)
    if template_id != x_confirm_template_id: raise HTTPException(400, '삭제 대상 템플릿 ID 확인이 일치하지 않습니다.')
    if not MANAGED.fullmatch(template_id):
        raise HTTPException(409, '기존 폴더 템플릿은 삭제할 수 없습니다.')
    if not record(template_id): raise HTTPException(404, 'DB에 등록되지 않은 템플릿입니다.')
    path = checked_path(template_id)
    mp = mapping_path(path)
    # Stage files away from discovery, restore on failed DB operation.
    staged=[]
    try:
        for source in (path,mp):
            if source.exists():
                if source.is_symlink() or not source.is_file(): raise HTTPException(409,'삭제 대상이 일반 파일이 아닙니다.')
                dst = source.parent / ('.deleting-' + str(uuid.uuid4()) + '.tmp')
                source.replace(dst)
                staged.append((source,dst))
        with connect() as cn:
            deleted=cn.execute('DELETE FROM career_pptx_template_registry WHERE tenant_id=%s AND template_id=%s',
                               (TENANT,template_id)).rowcount
            if deleted!=1: raise RuntimeError('DB 삭제 건수 불일치')
    except Exception:
        for source,dst in reversed(staged):
            if dst.exists(): dst.replace(source)
        raise
    for source,dst in staged: dst.unlink(missing_ok=True)
    return {'deleted': True, 'templateId':template_id}
