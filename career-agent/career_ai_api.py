"""Internal AI chat router. Mounted in existing career-agent FastAPI app.

Security: agent:8001 is reachable on the private Docker bridge only.
Public access must go through the existing career-api local access policy.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from career_ai_graph import run_chat, apply_preview
from career_rag_auto_sync import connect_db, decode_structured

router = APIRouter(prefix='/internal/career-ai', tags=['Career AI Stage04'])


class ChatRequest(BaseModel):
    profile_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=1200)
    intent: Literal['auto', 'search', 'analysis', 'edit'] = 'auto'
    limit: int = Field(default=5, ge=1, le=8)
    project_id: str | None = Field(default=None, max_length=128)
    edit_field: str | None = Field(default=None, max_length=64)
    new_value: str | None = Field(default=None, max_length=1200)


class ApproveRequest(BaseModel):
    confirm: Literal[True]
    expected_source_hash: str = Field(min_length=64, max_length=64)


def save_proposal(proposal: dict) -> str:
    """Stores a proposed delta ONLY, never changes source career records."""
    import uuid
    pid = uuid.uuid4()
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                INSERT INTO public.career_ai_edit_proposal
                (proposal_id, import_id, profile_id, project_id, project_index,
                 source_path, field, actual_field, old_value, new_value, source_hash,
                 project_name, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'DRAFT')
            ''', (pid, UUID(proposal['import_id']), proposal['profile_id'], proposal['project_id'],
                  proposal['project_index'], proposal['source_path'], proposal['field'],
                  proposal['actual_field'], proposal['old_value'], proposal['new_value'],
                  proposal['source_hash'], proposal['project_name']))
    return str(pid)


@router.post('/chat')
def chat(request: ChatRequest):
    try:
        result = run_chat(request.profile_id, request.message, request.intent,
                          request.limit, project_id=request.project_id,
                          edit_field=request.edit_field, new_value=request.new_value)
        if result.get('edit'):
            proposal_id = save_proposal(result['edit'])
            result['edit']['proposal_id'] = proposal_id
            result['edit']['status'] = 'DRAFT_REQUIRES_APPROVAL'
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f'AI service unavailable: {type(exc).__name__}') from exc


@router.post('/proposals/{proposal_id}/approve')
def approve(proposal_id: UUID, request: ApproveRequest):
    """Approve draft and return a preview. NO profile DB update happens here.

    Existing PUT /api/v1/career/word-imports/{id}/structured must handle the
    relational profile sync and checks; do not replicate that SQL here.
    """
    try:
        with connect_db() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute('''SELECT import_id, profile_id, project_id, project_index,
                          source_path, field, actual_field, old_value, new_value,
                          source_hash, project_name, status
                          FROM public.career_ai_edit_proposal
                          WHERE proposal_id=%s FOR UPDATE''', (proposal_id,))
                    row = cur.fetchone()
                    if not row:
                        raise HTTPException(status_code=404, detail='수정안을 찾지 못했습니다')
                    keys = ('import_id', 'profile_id', 'project_id', 'project_index',
                            'source_path', 'field', 'actual_field', 'old_value',
                            'new_value', 'source_hash', 'project_name', 'status')
                    proposal = dict(zip(keys, row))
                    if proposal['status'] != 'DRAFT':
                        raise HTTPException(status_code=409, detail='승인 가능한 수정안 상태가 아닙니다')
                    if proposal['source_hash'] != request.expected_source_hash:
                        raise HTTPException(status_code=409, detail='요청 원본 버전이 일치하지 않습니다')
                    cur.execute('''SELECT structured FROM public.career_word_import
                                   WHERE id=%s FOR SHARE''', (proposal['import_id'],))
                    original = cur.fetchone()
                    if not original:
                        raise HTTPException(status_code=404, detail='원본 Word 등록정보가 없습니다')
                    structured = decode_structured(original[0])
                    preview = apply_preview(structured, proposal)
                    cur.execute('''UPDATE public.career_ai_edit_proposal
                                   SET status='APPROVED_NOT_APPLIED', approved_at=NOW()
                                   WHERE proposal_id=%s AND status='DRAFT' ''',
                                (proposal_id,))
                    if cur.rowcount != 1:
                        raise HTTPException(status_code=409, detail='동시 수정 충돌')
        return {
            'proposal_id': str(proposal_id), 'status': 'APPROVED_NOT_APPLIED',
            'import_id': str(proposal['import_id']),
            'field': proposal['field'], 'project_index': proposal['project_index'],
            'old_value': proposal['old_value'], 'new_value': proposal['new_value'],
            'preview_structured': preview,
            'message': ('수정안 승인 완료. 실제 프로파일은 아직 변경되지 않았습니다. '
                        '기존 Word 구조화 저장 API를 통해 저장해야 합니다.'),
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post('/proposals/{proposal_id}/cancel')
def cancel(proposal_id: UUID):
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''UPDATE public.career_ai_edit_proposal
                        SET status='CANCELLED', cancelled_at=NOW()
                        WHERE proposal_id=%s AND status='DRAFT' ''', (proposal_id,))
            if cur.rowcount != 1:
                raise HTTPException(status_code=404, detail='취소 가능한 수정안을 찾지 못했습니다')
    return {'proposal_id': str(proposal_id), 'status': 'CANCELLED'}
