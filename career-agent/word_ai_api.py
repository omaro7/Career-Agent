"""Internal-only AI endpoint installed on the EXISTING career-agent FastAPI app."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID
from word_ai_cancel import signal_cancel

from word_ai_agent import reinterpret

router = APIRouter(tags=['internal-word-ai'])


class SourceBlock(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: str = Field(default='paragraph', max_length=20)
    text: str = Field(min_length=1, max_length=150_000)


class ReinterpretRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    blocks: list[SourceBlock] = Field(min_length=1, max_length=300)
    focus: str = Field(default='', max_length=300)
    runId: UUID | None = None


@router.post('/reinterpret-word')
def reinterpret_word(body: ReinterpretRequest):
    try:
        return reinterpret([block.model_dump() for block in body.blocks], body.focus)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    # AI failures bubble as 500 for the API worker to record as FAILED.


@router.post('/reinterpret-word-stream')
def reinterpret_word_stream(body: ReinterpretRequest):
    """Stream AI progress events over NDJSON without exposing source text in events."""
    from word_ai_stream import stream_reinterpret
    return StreamingResponse(
        stream_reinterpret([block.model_dump() for block in body.blocks], body.focus, body.runId),
        media_type='application/x-ndjson',
        headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'},
    )


@router.post('/reinterpret-word-stream/{run_id}/cancel')
def cancel_reinterpret_word_stream(run_id: UUID):
    """Only reachable through the internal Agent network in this deployment."""
    active = signal_cancel(run_id)
    return {'runId': str(run_id), 'cancelRequested': True, 'active': active}
