"""Stage 04-2: atomic, approved Word-profile edit application.

Never updates Excel imports. Uses the existing Word structured-save transaction
helper, including the existing employee/profile_import/career_project synchronizer.

Safety: local-only proof of concept. No session/tenant authorization; do not
expose career-api or career-agent outside loopback/private Docker bridge.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from typing import Literal

from .db import connect
from .profiles import TENANT
from .word_imports import StructuredPayload, _same_site, persist_word_structured_tx
from .word_profile import fill_missing_project_dates

router = APIRouter(prefix='/api/v1/career/ai', tags=['Career AI apply Stage04-2'])
ALLOWED_FIELDS = frozenset({'projectName', 'submittedRole', 'clientName',
                            'workDescription', 'technologiesText', 'periodText'})
FIELD_MAX = {'projectName': 500, 'submittedRole': 500, 'clientName': 250,
             'workDescription': 1200, 'technologiesText': 500, 'periodText': 100}
PATH_RE = re.compile(r'^root\.projects\[(\d+)\]$')
SAVE_ROUTE = '/api/v1/career/word-imports/{import_id}/structured'


class ApplyRequest(BaseModel):
    confirm: Literal[True]
    expected_source_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


def fingerprint(value: dict) -> str:
    """Match career_rag_auto_sync.digest exactly (SHA256 of sorted UTF-8 JSON)."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), default=str).encode('utf-8')).hexdigest()


def decode(raw):
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ValueError('원본 structured는 JSON 객체여야 합니다')
    return raw


def require_local():
    if os.getenv('CAREER_ACCESS_MODE') != 'local_single_user':
        raise HTTPException(403, '로컬 단일사용자 모드에서만 허용됩니다')


def as_dict(row, fields):
    """Existing app.db.connect uses dict_row; this also accepts tuple rows."""
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    return dict(zip(fields, row))


PROPOSAL_FIELDS = ('import_id', 'profile_id', 'project_id', 'project_index',
                   'source_path', 'field', 'actual_field', 'old_value', 'new_value',
                   'source_hash', 'status')


def load_proposal(cn, proposal_id: UUID, *, lock: bool = False):
    query = '''SELECT import_id, profile_id, project_id, project_index,
                     source_path, field, actual_field, old_value, new_value,
                     source_hash, status
               FROM public.career_ai_edit_proposal WHERE proposal_id=%s'''
    if lock:
        query += ' FOR UPDATE'
    p = as_dict(cn.execute(query, (proposal_id,)).fetchone(), PROPOSAL_FIELDS)
    if not p:
        raise HTTPException(404, 'AI 수정안을 찾지 못했습니다')
    if p['profile_id'] != 'word_' + UUID(str(p['import_id'])).hex:
        raise HTTPException(409, 'Word 원본과 프로파일 ID가 일치하지 않습니다')
    return p


def load_word(cn, import_id: UUID, *, lock: bool = False) -> dict:
    query = '''SELECT structured FROM public.career_word_import
               WHERE id=%s AND tenant_id=%s'''
    if lock:
        query += ' FOR UPDATE'
    row = cn.execute(query, (import_id, TENANT)).fetchone()
    if row is None:
        raise HTTPException(404, '해당 테넌트에서 Word 원본을 찾지 못했습니다')
    return decode(row['structured'] if isinstance(row, dict) else row[0])


def preview_change(structured: dict, proposal: dict) -> dict:
    """CAS preconditions; returns copy, never mutates source."""
    if fingerprint(structured) != str(proposal['source_hash']).strip():
        raise ValueError('Word 원본이 변경되어 수정안을 적용할 수 없습니다')
    match = PATH_RE.fullmatch(str(proposal['source_path']))
    if not match or int(match.group(1)) != int(proposal['project_index']):
        raise ValueError('수정 대상 프로젝트 경로가 변경되었습니다')
    index = int(proposal['project_index'])
    projects = structured.get('projects')
    if not isinstance(projects, list) or not (0 <= index < len(projects)):
        raise ValueError('수정 대상 프로젝트가 현재 원본에 없습니다')
    field = proposal['field']
    key = proposal['actual_field']
    if field not in ALLOWED_FIELDS or field != key:
        raise ValueError('정규 Word 프로젝트 필드만 수정할 수 있습니다')
    project = projects[index]
    if not isinstance(project, dict) or str(project.get(key) or '') != proposal['old_value']:
        raise ValueError('기존 프로젝트 필드값이 변경되었습니다')
    new_value = proposal['new_value']
    if not isinstance(new_value, str) or not new_value.strip() or len(new_value) > FIELD_MAX[field]:
        raise ValueError('필드별 길이 제한을 초과했거나 수정값이 비어 있습니다')
    if str(project.get(key) or '') == new_value:
        raise ValueError('변경 전후 값이 동일합니다')
    edited = copy.deepcopy(structured)
    edited['projects'][index][key] = new_value
    return edited


def validated_save_value(edited: dict) -> dict:
    """Reuse the actual Word PUT Pydantic model and period normalization."""
    try:
        parsed = StructuredPayload.model_validate(edited)
    except ValidationError as exc:
        # Avoid echoing personal fields from pydantic input in error response.
        raise ValueError('StructuredPayload 검증 실패: 필드 형식·개수·길이를 확인하세요') from exc
    value = fill_missing_project_dates(parsed.model_dump())
    if len(str(value).encode('utf-8')) > 140_000:
        raise ValueError('기존 Word 저장 크기 제한(140KB)을 초과합니다')
    return value


@router.get('/apply-contract')
def contract():
    require_local()
    return {
        'path': SAVE_ROUTE,
        'request_schema': {'$ref': '#/components/schemas/StructuredPayload'},
        'issue': None, 'configured_body_style': 'raw',
        'apply_enabled': os.getenv('CAREER_AI_APPLY_ENABLED', 'false').lower() == 'true',
        'atomic_transaction': True,
        'warning': '실제 DB 반영은 별도 승인과 설정이 필요한 로컬 전용 기능입니다.',
    }


@router.get('/proposals/{proposal_id}/apply-preview')
def apply_preview(proposal_id: UUID):
    require_local()
    with connect() as cn:
        p = load_proposal(cn, proposal_id)
        if p['status'] != 'APPROVED_NOT_APPLIED':
            raise HTTPException(409, '승인 후 미반영 상태만 미리볼 수 있습니다')
        original = load_word(cn, p['import_id'])
        try:
            edited = preview_change(original, p)
            normalized = validated_save_value(edited)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {'proposal_id': str(proposal_id), 'status': p['status'],
                'import_id': str(p['import_id']), 'project_index': p['project_index'],
                'field': p['field'], 'old_value': p['old_value'],
                'new_value': p['new_value'], 'expected_source_hash': str(p['source_hash']).strip(),
                'expected_after_hash': fingerprint(normalized),
                'message': '읽기 전용: PostgreSQL 경력정보를 변경하지 않았습니다.'}


@router.post('/proposals/{proposal_id}/apply')
def apply_approved(proposal_id: UUID, body: ApplyRequest, request: Request):
    """Atomic compare-and-set of Word + relational profile + proposal status.

    Acquires the proposal lock, then the Word row lock. The legacy Word PUT
    acquires the Word row lock through persist_word_structured_tx. Everything
    commits or rolls back together on the SAME app.db.connect transaction.
    """
    require_local()
    _same_site(request)
    if os.getenv('CAREER_AI_APPLY_ENABLED', 'false').lower() != 'true':
        raise HTTPException(403, 'DB 저장 기능은 비활성화되어 있습니다')
    with connect() as cn:
        p = load_proposal(cn, proposal_id, lock=True)
        if p['status'] != 'APPROVED_NOT_APPLIED':
            raise HTTPException(409, '이미 반영되었거나 승인되지 않은 수정안입니다')
        if str(p['source_hash']).strip() != body.expected_source_hash:
            raise HTTPException(409, '요청 원본 SHA256이 수정안과 다릅니다')
        original = load_word(cn, p['import_id'], lock=True)
        try:
            edited = preview_change(original, p)
            value = validated_save_value(edited)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

        # Reuses the exact Word save SQL, sync_word_saved_profile and tenant
        # scoping in one transaction; no internal HTTP or second DB connection.
        employee_key = persist_word_structured_tx(cn, p['import_id'], value)
        stored = load_word(cn, p['import_id'])
        target_hash = fingerprint(value)
        if fingerprint(stored) != target_hash:
            raise HTTPException(409, '저장 후 Word 데이터 검증에 실패했습니다 (트랜잭션 취소)')
        updated = cn.execute('''UPDATE public.career_ai_edit_proposal
                     SET status='APPLIED', applied_at=NOW(), applied_source_hash=%s
                     WHERE proposal_id=%s AND status='APPROVED_NOT_APPLIED' ''',
                     (target_hash, proposal_id))
        if updated.rowcount != 1:
            raise HTTPException(409, '수정안 상태가 변경되어 전체 저장을 취소했습니다')

    return {'proposal_id': str(proposal_id), 'status': 'APPLIED',
            'import_id': str(p['import_id']), 'profile_id': p['profile_id'],
            'employee_key': employee_key,
            'source_hash': target_hash, 'qdrant_status': 'PENDING_AUTOSYNC',
            'message': 'Word와 관계형 경력정보가 함께 저장되었습니다. Qdrant 동기화는 별도로 확인해야 합니다.'}


# Stage 04-3.3: read-only, version-aware RAG sync status.
def evaluate_rag_index(current_hash: str, indexed_hash: str | None,
                       sync_status: str | None) -> tuple[str, bool]:
    """INDEXED is valid only when the sync log contains the current Word hash."""
    if indexed_hash == current_hash and sync_status == 'INDEXED':
        return 'INDEXED', True
    if indexed_hash == current_hash and sync_status == 'FAILED':
        return 'FAILED', False
    return 'PENDING', False


@router.get('/profiles/{profile_id}/rag-status')
def rag_status(profile_id: str):
    """Poll the existing Stage03 sync ledger; never writes to DB or Qdrant."""
    require_local()
    if not re.fullmatch(r'word_[0-9a-f]{32}', profile_id):
        raise HTTPException(422, 'Word 프로파일 ID 형식이 올바르지 않습니다')
    import_id = UUID(hex=profile_id[5:])
    with connect() as cn:
        row = cn.execute(
            '''SELECT wi.structured, s.profile_id AS sync_profile_id, s.source_hash,
                      s.status AS sync_status, s.last_indexed_at
               FROM public.career_word_import wi
               LEFT JOIN public.career_word_rag_sync s ON s.import_id = wi.id::text
               WHERE wi.id=%s AND wi.tenant_id=%s''',
            (import_id, TENANT),
        ).fetchone()
    if row is None:
        raise HTTPException(404, 'Word 프로파일을 찾지 못했습니다')
    r = as_dict(row, ('structured', 'sync_profile_id', 'source_hash', 'sync_status', 'last_indexed_at'))
    original = r['structured']
    if original is None:
        return {'profile_id': profile_id, 'status': 'NOT_READY', 'up_to_date': False,
                'current_source_hash': None, 'indexed_source_hash': r['source_hash'],
                'last_indexed_at': None}
    current_hash = fingerprint(decode(original))
    indexed_hash = str(r['source_hash']) if r['source_hash'] else None
    # Do not accept an index row for a different profile ID even when UUID matches.
    if r['sync_profile_id'] is not None and r['sync_profile_id'] != profile_id:
        raise HTTPException(409, 'Qdrant 동기화의 프로파일 ID가 원본과 일치하지 않습니다')
    status, fresh = evaluate_rag_index(current_hash, indexed_hash, r['sync_status'])
    at = r.get('last_indexed_at')
    return {'profile_id': profile_id, 'status': status, 'up_to_date': fresh,
            'current_source_hash': current_hash, 'indexed_source_hash': indexed_hash,
            'last_indexed_at': at.isoformat() if hasattr(at, 'isoformat') else at}
