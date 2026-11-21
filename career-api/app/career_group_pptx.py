"""Stage 05 - Group customer PPTX. Additive router, no changes to source profiles.

Sequentially renders one PPTX per selected employee through the EXISTING
career-agent endpoint, then combines slides with pptx-slide-copier. Local-only MVP.
"""
from __future__ import annotations

import copy
import io
import json
import os
import re
import threading
import uuid
import zipfile
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .db import connect
from .profiles import TENANT
from .template_registry import checked_path, get_item
from .word_profile import blank_profile, worker_payload

router = APIRouter(prefix='/api/v1/career/group-pptx', tags=['group-pptx'])
PPTX_MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
MAX_ROWS = 20
MAX_PPTX_BYTES = 65_000_000
MAX_GROUP_BYTES = 220_000_000
JOB_DIR = lambda: Path(os.getenv('UPLOAD_DIR', '/app/uploads')).resolve() / 'group-pptx'
_MUTEX = threading.BoundedSemaphore(1)


def _local(request: Request, *, modify: bool = False) -> None:
    if os.getenv('CAREER_ACCESS_MODE') != 'local_single_user':
        raise HTTPException(403, '로컬 단일 사용자 모드에서만 그룹 PPTX를 생성할 수 있습니다.')
    if modify and request.headers.get('sec-fetch-site') not in (None, 'same-origin', 'none'):
        raise HTTPException(403, '외부 사이트에서 그룹 PPTX를 생성할 수 없습니다.')


class Common(BaseModel):
    model_config = ConfigDict(extra='forbid')
    projectName: str = Field(min_length=1, max_length=160)
    templateId: str = Field(min_length=1, max_length=180)
    projectPeriod: str = Field(min_length=1, max_length=100)
    projectPeriodFormat: Literal['YYYY_MM', 'YY_MM'] = 'YYYY_MM'
    pageRange: str = Field(default='ALL', min_length=1, max_length=100)
    asOfDate: date | None = None


class StaffRow(BaseModel):
    model_config = ConfigDict(extra='forbid')
    employeeKey: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    affiliation: str = Field(default='', max_length=120)
    displayGrade: str = Field(default='', max_length=80)
    businessRole: str = Field(default='', max_length=120)
    assignmentPeriod: str = Field(default='', max_length=120)
    assignmentMonths: str = Field(default='', max_length=10)
    allocationRate: str = Field(default='', max_length=16)

    @field_validator('assignmentMonths')
    @classmethod
    def check_months(cls, v: str) -> str:
        v = v.strip()
        if v and (not re.fullmatch(r'(?:0\.5|[1-9]\d{0,2}(?:\.[05])?)', v)
                  or not .5 <= float(v) <= 600):
            raise ValueError('개월수는 0.5~600의 0.5개월 단위여야 합니다.')
        return v

    @field_validator('allocationRate')
    @classmethod
    def check_rate(cls, v: str) -> str:
        v = v.strip()
        if v and (not re.fullmatch(r'\d{1,3}(?:\.\d{1,2})?%?', v)
                  or float(v.rstrip('%')) > 100):
            raise ValueError('투입율은 0~100 사이 숫자 또는 %여야 합니다.')
        return v


class GroupRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    common: Common
    rows: list[StaffRow] = Field(min_length=1, max_length=MAX_ROWS)

    @model_validator(mode='after')
    def check(self):
        # Deliberate duplicate staff rows are allowed when assignment differs.
        parse_page_range(self.common.pageRange, 100)
        return self


def parse_page_range(spec: str, slide_count: int) -> list[int]:
    """1-based page numbers (per employee), sorted in the requested order."""
    if spec.strip().upper() in ('ALL', '전체'):
        return list(range(slide_count))
    if not re.fullmatch(r'\s*\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*\s*', spec):
        raise ValueError('페이지 출력범위는 ALL 또는 1-3,5 형식으로 입력하세요.')
    selected: list[int] = []
    for chunk in spec.split(','):
        nums = [int(n.strip()) for n in chunk.split('-')]
        first, last = (nums[0], nums[-1])
        if first < 1 or last < first or last > slide_count:
            raise ValueError(f'페이지 출력범위 {chunk.strip()}가 1~{slide_count} 범위를 벗어났습니다.')
        for index in range(first - 1, last):
            if index not in selected:
                selected.append(index)
    if not selected or len(selected) > 100:
        raise ValueError('선택할 페이지는 1~100장이어야 합니다.')
    return selected


def _decode(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            result = json.loads(value)
            return result if isinstance(result, dict) else {}
        except ValueError:
            return {}
    return {}


def _text(value: Any) -> str:
    return str(value or '').strip()


def _structured_from_parsed(parsed: dict, employee: dict) -> dict:
    """Map saved Excel/Word profile_import.parsed_json to existing Word worker contract.

    Does not guess empty data. Missing projects cause a clear per-row error.
    """
    profile = _decode(parsed.get('profile'))
    result = blank_profile()
    fields = {
        'name': ('name', employee.get('full_name')),
        'affiliation': ('affiliation', ''),
        'positionTitle': ('position_title', employee.get('position_title')),
        'birthDate': ('birth_date', employee.get('birth_date')),
        'educationText': ('education_text', ''),
        'degree': ('degree', ''),
        'certificationsText': ('certifications_text', ''),
        'displayGrade': ('engineer_grade', employee.get('engineer_grade')),
    }
    for target, (source, default) in fields.items():
        result[target] = _text(profile.get(source) or default)
    if not result['educationText']:
        school_lines = []
        for education in profile.get('education') or []:
            if isinstance(education, dict):
                line = ' / '.join(filter(None, [_text(education.get('school_name')),
                                                _text(education.get('major'))]))
                if line: school_lines.append(line)
        result['educationText'] = ', '.join(school_lines)[:250]
    if not result['certificationsText']:
        result['certificationsText'] = ', '.join(
            _text(cert.get('certification_name')) for cert in (profile.get('certifications') or [])
            if isinstance(cert, dict) and cert.get('certification_name'))[:300]
    projects = []
    for record in profile.get('projects') or []:
        if not isinstance(record, dict) or record.get('include') is False:
            continue
        reviewed = _decode(record.get('reviewed'))
        project = {
            'projectName': _text(record.get('project_name') or reviewed.get('projectName')),
            'startDate': _text(record.get('start_date') or reviewed.get('startDate')),
            'endDate': _text(record.get('end_date') or reviewed.get('endDate')),
            'periodText': _text(record.get('source_duration_text') or reviewed.get('periodText')),
            'submittedRole': _text(record.get('role_text') or reviewed.get('submittedRole')),
            'clientName': _text(record.get('client_name') or reviewed.get('clientName')),
            'workDescription': _text(reviewed.get('workDescription')),
            'technologiesText': _text(record.get('technology_text') or reviewed.get('technologiesText')),
            'include': True,
        }
        if project['projectName']:
            projects.append(project)
    result['projects'] = projects
    return result


def _load_staff(employee_key: str) -> dict:
    with connect() as cn:
        employee = cn.execute('''SELECT employee_id,employee_key,full_name,birth_date,
                    position_title,engineer_grade FROM employee
                    WHERE tenant_id=%s AND employee_key=%s''', (TENANT, employee_key)).fetchone()
        if not employee:
            raise ValueError(f'선택된 직원 {employee_key}을 찾을 수 없습니다.')
        emp = dict(employee)
        if employee_key.startswith('word_') and re.fullmatch(r'word_[0-9a-f]{32}', employee_key):
            word_id = uuid.UUID(hex=employee_key[5:])
            word = cn.execute('''SELECT structured,status FROM career_word_import
                WHERE id=%s AND tenant_id=%s''', (word_id, TENANT)).fetchone()
            if not word or word['status'] not in ('REVIEWED', 'RENDERED'):
                raise ValueError(f'{employee_key}: Word 검토 저장이 완료되지 않았습니다.')
            return _decode(word['structured'])
        # Inspect existing profile_import columns to support historical schema variants.
        cols = cn.execute('''SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='profile_import' ''').fetchall()
        available = {entry['column_name'] if isinstance(entry, dict) else entry[0] for entry in cols}
        order = next((c for c in ('updated_at', 'created_at', 'imported_at', 'import_id') if c in available), 'import_id')
        row = cn.execute(f'''SELECT parsed_json FROM profile_import
            WHERE tenant_id=%s AND employee_id=%s AND parsed_json IS NOT NULL
            ORDER BY {order} DESC LIMIT 1''', (TENANT, emp['employee_id'])).fetchone()
        if not row:
            raise ValueError(f'{employee_key}: 저장된 프로파일이 없습니다.')
        return _structured_from_parsed(_decode(row['parsed_json']), emp)


def make_payload(common: Common, row: StaffRow, structured: dict, customer_name: str) -> dict:
    snapshot = copy.deepcopy(structured)
    for field in ('affiliation', 'displayGrade', 'businessRole', 'assignmentPeriod',
                  'assignmentMonths', 'allocationRate'):
        value = getattr(row, field)
        if value.strip():
            snapshot[field] = value.strip()
    if customer_name:
        snapshot['customerName'] = customer_name
    # One explicitly chosen age reference date for every row; KST today is the
    # backwards-compatible default when an older client omits asOfDate.
    reference = common.asOfDate or datetime.now(ZoneInfo('Asia/Seoul')).date()
    # worker_payload normally prefers a stored ageYears over birthDate.
    # Clear ageYears on this *output-only copy* to calculate full age as of the
    # user-selected date; source PostgreSQL records remain unchanged.
    if _text(snapshot.get('birthDate')):
        snapshot['ageYears'] = ''
    payload = worker_payload(snapshot, template_id=common.templateId,
                             template_customer_name=customer_name,
                             as_of_date=reference.isoformat())
    payload['projectPeriodFormat'] = common.projectPeriodFormat
    payload['careerPageScope'] = 'ALL'  # Page ranges selected after each generated PPTX.
    # Existing Agent renderers may use these common project placeholders.
    payload['projectName'] = common.projectName
    payload['projectPeriod'] = common.projectPeriod
    return payload


def _ensure_pptx(data: bytes) -> None:
    if not 100 <= len(data) <= MAX_PPTX_BYTES or not zipfile.is_zipfile(io.BytesIO(data)):
        raise ValueError('Agent가 유효한 PPTX 파일을 반환하지 않았습니다.')
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if 'ppt/presentation.xml' not in z.namelist():
            raise ValueError('PPTX 프레젠테이션 구조가 없습니다.')


def _select_slides(deck, page_range: str):
    indexes = parse_page_range(page_range, len(deck.slides))
    # Deleting only unselected slides while retaining their original order.
    selected = set(indexes)
    for i in reversed(range(len(deck.slides))):
        if i not in selected:
            sld_id = deck.slides._sldIdLst[i]
            deck.part.drop_rel(sld_id.rId)
            deck.slides._sldIdLst.remove(sld_id)
    return deck


def _add_overview(deck, common: Common, rows: list[StaffRow], names: list[str]) -> None:
    """Guarantee common project name/period appear, regardless of template mapping.

    Add a single overview slide at the beginning, after all employee slides
    have been merged; keeps individual PPTX pages unaltered.
    """
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    layout = min(deck.slide_layouts, key=lambda x: len(x.placeholders))
    slide = deck.slides.add_slide(layout)
    # Remove placeholder objects on this new slide only; do not alter masters.
    for shape in tuple(slide.shapes):
        el = shape._element
        el.getparent().remove(el)
    width = deck.slide_width
    height = deck.slide_height
    shape = slide.shapes.add_shape(1, 0, 0, width, height)
    shape.fill.solid(); shape.fill.fore_color.rgb = RGBColor(14, 35, 68)
    shape.line.fill.background()
    def txt(text: str, top: float, size: int, bold=False, color=(255,255,255)):
        box = slide.shapes.add_textbox(Inches(.65), Inches(top), width-Inches(1.3), Inches(.8))
        frame = box.text_frame; frame.word_wrap = True
        par = frame.paragraphs[0]; par.text = text; par.font.size = Pt(size)
        par.font.bold = bold; par.font.color.rgb = RGBColor(*color)
        par.alignment = PP_ALIGN.LEFT
        return box
    height_in = height / Inches(1)
    txt('GROUP CAREER PROFILE', max(.15, height_in*.13), 13, True, (130,187,255))
    txt(common.projectName, max(.7, height_in*.24), 26, True)
    txt(f'프로젝트기간: {common.projectPeriod}', max(1.5, height_in*.43), 16)
    summary = '  ·  '.join(names)
    txt(f'참여 인력 {len(rows)}명 · {summary[:110]}', max(2.2, height_in*.59), 13, color=(205,220,237))
    # Move new overview slide to index 0 while keeping all employee slide order.
    sld = deck.slides._sldIdLst[-1]
    deck.slides._sldIdLst.remove(sld)
    deck.slides._sldIdLst.insert(0, sld)


def _job_folder(job_id: uuid.UUID) -> Path:
    return JOB_DIR() / str(job_id)


def _write_state(folder: Path, **changes) -> None:
    path = folder / 'state.json'
    state: dict = {}
    if path.exists():
        try: state = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, OSError): pass
    state.update(changes)
    state['updatedAt'] = datetime.now(timezone.utc).isoformat()
    temp = folder / 'state.tmp'
    temp.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    os.replace(temp, path)


def _run_group_job(job_id: uuid.UUID, request: GroupRequest) -> None:
    folder = _job_folder(job_id)
    acquired = _MUTEX.acquire(blocking=False)
    if not acquired:
        _write_state(folder, status='FAILED', error='이미 다른 그룹 PPTX 생성 작업이 실행 중입니다.')
        return
    try:
        _write_state(folder, status='RUNNING', doneRows=0, currentEmployee='', error=None)
        item = get_item(request.common.templateId)
        checked_path(request.common.templateId)
        if not item or item.get('status') != 'READY' or item.get('isActive') is False:
            raise ValueError('선택한 고객사 템플릿이 READY·활성 상태가 아닙니다.')
        customer = _text(item.get('customerName'))
        # Delay imports to generation, so API can still report useful missing-dep errors.
        from pptx import Presentation
        from pptx_slide_copier import SlideCopier
        master = None
        selected_names: list[str] = []
        total_size = 0
        agent = os.getenv('AGENT_BASE_URL', 'http://career-agent:8001').rstrip('/')
        timeout = httpx.Timeout(connect=10, read=250, write=25, pool=10)
        with httpx.Client(timeout=timeout) as client:
            for index, staff in enumerate(request.rows):
                _write_state(folder, currentEmployee=staff.employeeKey, currentRow=index+1)
                structured = _load_staff(staff.employeeKey)
                if not structured.get('projects'):
                    raise ValueError(f'{index+1}번째 직원은 저장된 프로젝트가 없습니다.')
                selected_names.append(_text(structured.get('name')) or staff.employeeKey)
                payload = make_payload(request.common, staff, structured, customer)
                raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
                if len(raw) > 245_000:
                    raise ValueError(f'{index+1}번째 직원의 렌더링 요청이 너무 큽니다.')
                res = client.post(agent + '/render-customer-pptx', content=raw,
                                  headers={'content-type': 'application/json; charset=utf-8'})
                if res.status_code != 200:
                    raise ValueError(f'{index+1}번째 직원 PPTX 생성 실패: Agent HTTP {res.status_code}')
                pptx_data = res.content
                _ensure_pptx(pptx_data)
                total_size += len(pptx_data)
                if total_size > MAX_GROUP_BYTES:
                    raise ValueError('최종 문서 예상 크기가 제한을 초과했습니다.')
                deck = _select_slides(Presentation(io.BytesIO(pptx_data)), request.common.pageRange)
                if len(deck.slides) == 0:
                    raise ValueError('페이지 출력범위에 포함된 슬라이드가 없습니다.')
                if master is None:
                    master = deck
                else:
                    if master.slide_width != deck.slide_width or master.slide_height != deck.slide_height:
                        raise ValueError('직원별 PPTX의 페이지 크기가 서로 다릅니다.')
                    for page_index in range(len(deck.slides)):
                        SlideCopier.copy_slide(deck, page_index, master)
                _write_state(folder, doneRows=index+1, slides=sum(1 for _ in master.slides))
        if master is None:
            raise ValueError('생성할 직원이 없습니다.')
        _add_overview(master, request.common, request.rows, selected_names)
        target = folder / 'group-career.pptx'
        temp = folder / 'group-career.tmp.pptx'
        master.save(str(temp))
        _ensure_pptx(temp.read_bytes())
        os.replace(temp, target)
        _write_state(folder, status='COMPLETE', doneRows=len(request.rows),
                     slides=len(master.slides), currentEmployee='',
                     downloadUrl=f'/api/v1/career/group-pptx/jobs/{job_id}/download')
    except Exception as exc:
        # Do not return file paths/credentials in public errors.
        message = str(exc)
        if not isinstance(exc, (ValueError, HTTPException)):
            message = f'그룹 생성 중 서버 오류: {type(exc).__name__}. career-api 로그를 확인하세요.'
        _write_state(folder, status='FAILED', error=message[:400], currentEmployee='')
    finally:
        _MUTEX.release()


def _registry_records(obj: Any) -> list[dict]:
    """Read *registered* items only, not template files from directories.

    Some deployments expose records as {templates:[...]}, while others expose
    a dictionary keyed by the registered template ID. Both are supported.
    """
    if hasattr(obj, 'model_dump'):
        obj = obj.model_dump()
    if isinstance(obj, (list, tuple)):
        return [record for item in obj for record in _registry_records(item)]
    if not isinstance(obj, dict):
        return []
    if any(k in obj for k in ('templateId', 'template_id', 'id')):
        return [obj]
    for wrapper in ('templates', 'items', 'entries', 'records', 'registry', 'data', 'result'):
        if wrapper in obj:
            return _registry_records(obj[wrapper])
    rows = []
    for identifier, item in obj.items():
        if hasattr(item, 'model_dump'):
            item = item.model_dump()
        if isinstance(item, dict):
            rows.append({'templateId': identifier, **item})
    return rows


def _templates() -> list[dict]:
    """Return READY + active templates from the existing registry's sources.

    We never invent IDs, scan arbitrary .pptx files or change registry state.
    Actual creation revalidates via get_item() and checked_path().
    """
    from . import template_registry as reg
    sources = (
        'list_items', 'list_registry', 'get_items', 'list_templates',
        'get_templates', 'get_registry', 'load_registry', 'read_registry',
        '_load_registry', '_read_registry', '_read', 'read_items',
    )
    candidates: list[dict] = []
    for name in sources:
        getter = getattr(reg, name, None)
        if not callable(getter):
            continue
        try:
            values = _registry_records(getter())
        except (TypeError, ValueError, OSError, KeyError):
            continue
        if values:
            candidates = values
            break
    if not candidates:
        for name in ('REGISTRY', 'TEMPLATES', 'ITEMS', 'registry', 'templates'):
            values = getattr(reg, name, None)
            if values is None or callable(values):
                continue
            candidates = _registry_records(values)
            if candidates:
                break
    # Optional fallback: path supplied by the actual registry module itself.
    # Do NOT scan template folders, and do NOT construct synthetic IDs.
    if not candidates:
        for name in ('REGISTRY_PATH', 'REGISTRY_FILE', 'INDEX_PATH', 'registry_path'):
            path = getattr(reg, name, None)
            if not isinstance(path, (str, Path)):
                continue
            try:
                with open(path, 'r', encoding='utf-8-sig') as fh:
                    candidates = _registry_records(json.load(fh))
            except (OSError, ValueError, TypeError):
                continue
            if candidates:
                break
    output: list[dict] = []
    seen = set()
    for candidate in candidates:
        identifier = candidate.get('templateId') or candidate.get('template_id') or candidate.get('id')
        if not isinstance(identifier, str) or not identifier.strip():
            continue
        identifier = identifier.strip()
        if identifier in seen:
            continue
        try:
            item = reg.get_item(identifier)
        except (ValueError, KeyError, HTTPException):
            continue
        if not isinstance(item, dict):
            continue
        status = str(item.get('status') or candidate.get('status') or '').upper()
        active = item.get('isActive', candidate.get('isActive', True))
        if status != 'READY' or active is False or str(active).lower() == 'false':
            continue
        seen.add(identifier)
        customer = str(item.get('customerName') or candidate.get('customerName') or '').strip()
        name = str(item.get('name') or item.get('displayName')
                   or item.get('title') or candidate.get('name')
                   or candidate.get('displayName') or customer or identifier).strip()
        output.append({'templateId': identifier, 'name': name,
                       'customerName': customer, 'status': 'READY'})
    output.sort(key=lambda record: (record['customerName'], record['name'], record['templateId']))
    return output


@router.get('/options')
def options(request: Request):
    _local(request)
    with connect() as cn:
        employees = cn.execute('''SELECT employee_key,full_name,position_title,engineer_grade
            FROM employee WHERE tenant_id=%s ORDER BY full_name,employee_key LIMIT 1000''',
            (TENANT,)).fetchall()
        # Present only the newest *reviewed* Word import for the same name and
        # original filename; this is a UI option filter, not a DB merge/delete.
        words = cn.execute('''SELECT id,original_filename,structured->>'name' AS person_name
            FROM career_word_import WHERE tenant_id=%s
              AND status IN ('REVIEWED','RENDERED')
            ORDER BY created_at DESC,id DESC LIMIT 2000''', (TENANT,)).fetchall()
    chosen_word_keys: set[str] = set()
    labels_by_key: dict[str, str] = {}
    groups: set[tuple[str, str]] = set()
    for w in words:
        group = (_text(w['person_name']), _text(w['original_filename']))
        if group in groups: continue
        groups.add(group)
        employee_key = 'word_' + uuid.UUID(str(w['id'])).hex
        chosen_word_keys.add(employee_key)
        labels_by_key[employee_key] = group[1]
    output = []
    for e in employees:
        key = e['employee_key']
        if key.startswith('word_') and key not in chosen_word_keys:
            continue
        output.append({'employeeKey': key, 'name': e['full_name'],
                       'positionTitle': e.get('position_title') or '',
                       'engineerGrade': e.get('engineer_grade') or '',
                       'source': 'Word' if key.startswith('word_') else 'Excel',
                       'sourceFileName': labels_by_key.get(key, '')})
    return {'employees': output[:500], 'templates': _templates()}


@router.post('/jobs', status_code=202)
def create_job(body: GroupRequest, request: Request, tasks: BackgroundTasks):
    _local(request, modify=True)
    try: parse_page_range(body.common.pageRange, 100)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    try:
        item = get_item(body.common.templateId)
        checked_path(body.common.templateId)
        if not item or item.get('status') != 'READY' or item.get('isActive') is False:
            raise ValueError('선택한 고객사 템플릿이 READY 상태가 아닙니다.')
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    job_id = uuid.uuid4()
    folder = _job_folder(job_id)
    folder.mkdir(parents=True, exist_ok=False)
    _write_state(folder, jobId=str(job_id), status='QUEUED', doneRows=0,
                 totalRows=len(body.rows), projectName=body.common.projectName,
                 currentEmployee='', downloadUrl=None, error=None)
    tasks.add_task(_run_group_job, job_id, body)
    return {'jobId': str(job_id), 'status': 'QUEUED',
            'statusUrl': f'/api/v1/career/group-pptx/jobs/{job_id}'}


@router.get('/jobs/{job_id}')
def get_job(job_id: uuid.UUID, request: Request):
    _local(request)
    folder = _job_folder(job_id)
    file = folder / 'state.json'
    if not file.is_file():
        raise HTTPException(404, '작업을 찾지 못했습니다.')
    try: return json.loads(file.read_text(encoding='utf-8'))
    except (OSError, ValueError): raise HTTPException(503, '작업 상태를 읽을 수 없습니다.')


@router.get('/jobs/{job_id}/download')
def download(job_id: uuid.UUID, request: Request):
    _local(request)
    folder = _job_folder(job_id)
    file = folder / 'group-career.pptx'
    status_file = folder / 'state.json'
    if not status_file.is_file() or not file.is_file():
        raise HTTPException(404, '완료된 그룹 PPTX가 없습니다.')
    try: state = json.loads(status_file.read_text(encoding='utf-8'))
    except (OSError, ValueError): raise HTTPException(503, '작업 상태가 잘못되었습니다.')
    if state.get('status') != 'COMPLETE' or file.is_symlink():
        raise HTTPException(409, '완료된 결과가 아닙니다.')
    return FileResponse(str(file), media_type=PPTX_MIME,
                        filename='group-career.pptx',
                        headers={'Cache-Control': 'no-store'})
