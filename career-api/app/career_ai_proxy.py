"""Thin local-only proxy to career-agent: no public direct access to port 8001."""
import os
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

router = APIRouter(prefix='/api/v1/career/ai', tags=['Career AI'])


def require_local_mode():
    if os.getenv('CAREER_ACCESS_MODE') != 'local_single_user':
        raise HTTPException(status_code=403, detail='Stage04는 로컬 단일사용자 모드에서만 활성화됩니다')


async def forward(method: str, suffix: str, request: Request):
    require_local_mode()
    base = os.environ.get('AGENT_BASE_URL', 'http://career-agent:8001').rstrip('/')
    try:
        # Input intentionally bounded; no arbitrary URL/path or headers forwarded.
        body = await request.body()
        if len(body) > 32_768:
            raise HTTPException(status_code=413, detail='요청 크기 제한 초과')
        timeout = httpx.Timeout(
            connect=10.0,
            read=300.0,
            write=15.0,
            pool=10.0
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.request(method, base + '/internal/career-ai' + suffix,
                                       content=body if method == 'POST' and body else None,
                                       headers={'content-type': 'application/json'})
        # Only JSON responses, no non-JSON arbitrary content.
        return Response(content=res.content, status_code=res.status_code,
                        media_type='application/json; charset=utf-8')
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f'career-agent 통신 오류: {type(exc).__name__}') from exc


@router.post('/chat')
async def chat(request: Request):
    return await forward('POST', '/chat', request)


@router.post('/proposals/{proposal_id}/approve')
async def approve(proposal_id: UUID, request: Request):
    return await forward('POST', f'/proposals/{proposal_id}/approve', request)


@router.post('/proposals/{proposal_id}/cancel')
async def cancel(proposal_id: UUID, request: Request):
    return await forward('POST', f'/proposals/{proposal_id}/cancel', request)
