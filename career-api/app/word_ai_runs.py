"""Queued local-AI re-interpretation runs without altering reviewed Word or Excel records.

This local-operator MVP uses FastAPI BackgroundTasks. It is not durable queueing:
container restart interrupts a running job, and the poller will mark it FAILED.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from .db import connect
from .profiles import TENANT
from .word_imports import _load, _same_site

log = logging.getLogger(__name__)
router = APIRouter(prefix='/api/v1/career/word-imports', tags=['word-ai'])
# One Uvicorn worker per career-api container: used only to detect orphan runs.
_active_runs: set[uuid.UUID] = set()
_active_lock = Lock()


class ReinterpretOptions(BaseModel):
    focus: str = Field(default='', max_length=300)


def _sha(reviewed: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(reviewed, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def _update_progress(run_id: uuid.UUID, progress: dict[str, Any]) -> None:
    # Persist only progress counters; Word content is never copied into log metadata.
    completed = max(0, int(progress.get('completedChunks') or 0))
    total = max(0, int(progress.get('totalChunks') or 0))
    percent = min(99, max(0, int(progress.get('percent') or 0)))
    value = {'stage': str(progress.get('stage') or 'RUNNING')[:30],
             'currentChunk': max(0, int(progress.get('currentChunk') or 0)),
             'completedChunks': completed, 'totalChunks': total, 'percent': percent}
    with connect() as cn:
        cn.execute('''UPDATE career_word_ai_run SET meta=%s
                      WHERE id=%s AND tenant_id=%s AND status='RUNNING' ''',
                   (Jsonb({'progress': value}), run_id, TENANT))


class JobCancelled(Exception):
    """Cancel requested for this run."""


def _get_status(run_id: uuid.UUID) -> str | None:
    with connect() as cn:
        row = cn.execute('''SELECT status FROM career_word_ai_run
                            WHERE id=%s AND tenant_id=%s''', (run_id, TENANT)).fetchone()
    return row['status'] if row else None


def _check_cancel(run_id: uuid.UUID) -> None:
    if _get_status(run_id) in ('CANCELLING', 'CANCELLED'):
        raise JobCancelled()


def _finish_cancelled(run_id: uuid.UUID) -> None:
    # Never turn an already successful run into CANCELLED, nor overwrite a new run.
    with connect() as cn:
        cn.execute('''UPDATE career_word_ai_run
                      SET status='CANCELLED',finished_at=NOW(),
                          error_text='사용자가 AI 분석을 취소했습니다.'
                      WHERE id=%s AND tenant_id=%s
                        AND status IN ('QUEUED','RUNNING','CANCELLING')''',
                   (run_id, TENANT))
    log.warning('[WORD-AI] run=%s CANCELLED', run_id)


def _run_job(import_id: uuid.UUID, run_id: uuid.UUID, source: dict[str, Any], focus: str) -> None:
    # Runs in FastAPI BackgroundTasks. Cancellation is durable in PostgreSQL and
    # cooperative in career-agent's per-token Ollama streaming loop.
    import time
    started = time.monotonic()
    with _active_lock:
        _active_runs.add(run_id)
    try:
        with connect() as cn:
            row = cn.execute('''UPDATE career_word_ai_run
                          SET status='RUNNING',started_at=NOW(),meta=%s
                          WHERE id=%s AND tenant_id=%s AND status='QUEUED'
                          RETURNING id''',
                       (Jsonb({'progress': {'stage':'PREPARING', 'percent':0,
                                            'completedChunks':0, 'totalChunks':0}}), run_id, TENANT)).fetchone()
        if not row:
            _check_cancel(run_id)
            return  # Finished or not runnable; do not start the Agent.
        _check_cancel(run_id)
        payload = {'blocks': source['blocks'], 'focus': focus, 'runId': str(run_id)}
        total_timeout = float(os.getenv('WORD_AI_JOB_TIMEOUT_SECONDS', '3600'))
        url = os.getenv('AGENT_BASE_URL', 'http://career-agent:8001').rstrip('/') + '/reinterpret-word-stream'
        data: dict[str, Any] | None = None
        with httpx.Client(timeout=httpx.Timeout(connect=10, read=120, write=30, pool=10)) as client:
            with client.stream('POST', url, json=payload) as response:
                if response.status_code != 200:
                    raise RuntimeError(f'Agent AI 처리 실패: HTTP {response.status_code}')
                for line in response.iter_lines():
                    _check_cancel(run_id)
                    if time.monotonic() - started > total_timeout:
                        raise TimeoutError(f'AI 전체 분석 제한시간 {total_timeout:.0f}초를 초과했습니다.')
                    if not line:
                        continue
                    if len(line) > 3_000_000:
                        raise RuntimeError('AI 응답 크기 제한을 초과했습니다.')
                    event = json.loads(line)
                    if event.get('event') == 'heartbeat':
                        continue
                    if event.get('event') == 'cancelled':
                        raise JobCancelled()
                    if event.get('event') == 'progress':
                        _update_progress(run_id, event['progress'])
                        log.info('[WORD-AI] run=%s progress=%s', run_id, event['progress'])
                    elif event.get('event') == 'error':
                        raise RuntimeError('Agent 추출 오류: ' + str(event.get('message', 'unknown'))[:250])
                    elif event.get('event') == 'result':
                        data = event.get('data')
        _check_cancel(run_id)
        if not isinstance(data, dict) or not isinstance(data.get('structured'), dict) or not isinstance(data.get('evidence'), dict):
            raise RuntimeError('Agent AI 응답에 최종 구조화 결과가 없습니다.')
        fresh = _load(import_id)['reviewed']
        status = 'DONE' if _sha(fresh) == _sha(source) else 'STALE'
        meta = data.get('meta') or {}
        meta['progress'] = {'stage': 'COMPLETE', 'percent': 100,
                            'completedChunks': meta.get('chunkCount', 0),
                            'totalChunks': meta.get('chunkCount', 0)}
        with connect() as cn:
            saved = cn.execute('''UPDATE career_word_ai_run
                          SET status=%s,model=%s,structured=%s,evidence=%s,warnings=%s,
                              meta=%s,finished_at=NOW()
                          WHERE id=%s AND tenant_id=%s AND status='RUNNING'
                          RETURNING id''',
                       (status, str(meta.get('model', ''))[:100],
                        Jsonb(data['structured']), Jsonb(data['evidence']),
                        Jsonb(data.get('warnings', [])), Jsonb(meta), run_id, TENANT)).fetchone()
        if not saved:
            _check_cancel(run_id)  # Cancellation won the commit race.
            return
        log.info('[WORD-AI] run=%s ended status=%s', run_id, status)
    except JobCancelled:
        _finish_cancelled(run_id)
    except Exception as exc:
        if _get_status(run_id) in ('CANCELLING', 'CANCELLED'):
            _finish_cancelled(run_id)
        else:
            log.exception('Word AI run failed, run=%s', run_id)
            try:
                with connect() as cn:
                    cn.execute('''UPDATE career_word_ai_run
                                  SET status='FAILED',error_text=%s,finished_at=NOW()
                                  WHERE id=%s AND tenant_id=%s AND status IN ('QUEUED','RUNNING')''',
                               (str(exc)[:350], run_id, TENANT))
            except Exception:
                log.exception('Failed to persist Word AI error, run=%s', run_id)
    finally:
        with _active_lock:
            _active_runs.discard(run_id)


@router.post('/{import_id}/reinterpret', status_code=202)
def start_reinterpret(import_id: uuid.UUID, body: ReinterpretOptions,
                      background_tasks: BackgroundTasks, request: Request):
    _same_site(request)
    record = _load(import_id)
    reviewed = record['reviewed']
    if not isinstance(reviewed, dict) or not reviewed.get('blocks'):
        raise HTTPException(422, '먼저 Word 원문 블록을 등록하거나 저장해 주세요.')
    # End abandoned jobs; background tasks do not survive container restarts.
    run_id = uuid.uuid4()
    try:
        with connect() as cn:
            cn.execute('''UPDATE career_word_ai_run SET status='FAILED',
                             error_text='이전 실행 중단 또는 제한시간 초과', finished_at=NOW()
                          WHERE import_id=%s AND tenant_id=%s
                            AND status IN ('QUEUED','RUNNING')
                            AND created_at < NOW() - INTERVAL '2 hours' ''',
                       (import_id, TENANT))
            running = cn.execute('''SELECT 1 FROM career_word_ai_run
                                    WHERE import_id=%s AND tenant_id=%s
                                      AND status IN ('QUEUED','RUNNING','CANCELLING') LIMIT 1''',
                                 (import_id, TENANT)).fetchone()
            if running:
                raise HTTPException(409, '이미 AI 재해석이 진행 중입니다. 완료 후 다시 실행하세요.')
            cn.execute('''INSERT INTO career_word_ai_run
                          (id,import_id,tenant_id,source_sha256,status,focus)
                          VALUES (%s,%s,%s,%s,'QUEUED',%s)''',
                       (run_id, import_id, TENANT, _sha(reviewed), body.focus))
    except HTTPException:
        raise
    background_tasks.add_task(_run_job, import_id, run_id, reviewed, body.focus)
    return {'runId': str(run_id), 'status': 'QUEUED', 'message': 'Ollama 재해석 요청을 등록했습니다.'}


@router.get('/{import_id}/reinterpret/latest')
def get_latest_run(import_id: uuid.UUID):
    _load(import_id)
    with connect() as cn:
        row = cn.execute('''SELECT id FROM career_word_ai_run
                            WHERE import_id=%s AND tenant_id=%s
                            ORDER BY created_at DESC LIMIT 1''',
                         (import_id, TENANT)).fetchone()
    return get_run(import_id, row['id']) if row else {'runId': None}


@router.post('/{import_id}/reinterpret/{run_id}/cancel')
def cancel_reinterpret(import_id: uuid.UUID, run_id: uuid.UUID, request: Request):
    _same_site(request)
    _load(import_id)  # preserve existing tenant and record access checks
    with _active_lock:
        active_here = run_id in _active_runs
    with connect() as cn:
        row = cn.execute('''SELECT status FROM career_word_ai_run
                            WHERE id=%s AND import_id=%s AND tenant_id=%s
                            FOR UPDATE''', (run_id, import_id, TENANT)).fetchone()
        if not row:
            raise HTTPException(404, 'AI 재해석 실행 내역이 없습니다.')
        if row['status'] == 'QUEUED' or (row['status'] in ('RUNNING','CANCELLING') and not active_here):
            # QUEUED has no Ollama request yet. Local single-worker orphan jobs
            # (after container restart) also have no active BackgroundTask.
            cn.execute('''UPDATE career_word_ai_run
                          SET status='CANCELLED',finished_at=NOW(),
                              error_text='사용자가 AI 분석을 취소했습니다.'
                          WHERE id=%s AND tenant_id=%s''', (run_id, TENANT))
            target = 'CANCELLED'
        elif row['status'] == 'RUNNING':
            cn.execute('''UPDATE career_word_ai_run SET status='CANCELLING'
                          WHERE id=%s AND tenant_id=%s''', (run_id, TENANT))
            target = 'CANCELLING'
        else:
            target = row['status']
    if target == 'CANCELLING':
        url = (os.getenv('AGENT_BASE_URL', 'http://career-agent:8001').rstrip('/') +
               f'/reinterpret-word-stream/{run_id}/cancel')
        try:
            response = httpx.post(url, timeout=3)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # DB state stays CANCELLING; the worker also checks status on heartbeat
            # and will stop before the next chunk even if the Agent call fails.
            log.warning('[WORD-AI] Agent cancellation signal failed for %s: %s', run_id, type(exc).__name__)
    return get_run(import_id, run_id)


@router.get('/{import_id}/reinterpret/{run_id}')
def get_run(import_id: uuid.UUID, run_id: uuid.UUID):
    record = _load(import_id)
    # A restarted single-worker API has no local task to finish a pending cancel.
    with _active_lock:
        active_here = run_id in _active_runs
    with connect() as cn:
        if not active_here:
            cn.execute('''UPDATE career_word_ai_run
                          SET status='CANCELLED',finished_at=NOW(),
                              error_text='취소 요청이 완료되었습니다.'
                          WHERE id=%s AND import_id=%s AND tenant_id=%s
                            AND status='CANCELLING' ''',
                       (run_id, import_id, TENANT))
        # FastAPI BackgroundTasks can be interrupted when the container restarts.
        cn.execute('''UPDATE career_word_ai_run
                      SET status='FAILED',error_text='AI 처리 중단 또는 제한시간 초과',finished_at=NOW()
                      WHERE id=%s AND import_id=%s AND tenant_id=%s
                        AND status IN ('QUEUED','RUNNING')
                        AND created_at < NOW() - INTERVAL '2 hours' ''',
                   (run_id, import_id, TENANT))
        cn.execute('''UPDATE career_word_ai_run
                      SET status='CANCELLED',error_text='취소 요청 후 작업 연결이 종료되었습니다.',finished_at=NOW()
                      WHERE id=%s AND import_id=%s AND tenant_id=%s
                        AND status='CANCELLING'
                        AND created_at < NOW() - INTERVAL '2 hours' ''',
                   (run_id, import_id, TENANT))
        row = cn.execute('''SELECT id, status, model, focus, source_sha256, structured,
                                   evidence, warnings, meta, error_text, created_at, started_at, finished_at
                            FROM career_word_ai_run
                            WHERE id=%s AND import_id=%s AND tenant_id=%s''',
                         (run_id, import_id, TENANT)).fetchone()
    if not row:
        raise HTTPException(404, 'AI 재해석 실행 내역이 없습니다.')
    stale = row['source_sha256'] != _sha(record['reviewed'])
    meta = row['meta'] or {}
    progress = dict(meta.get('progress') or {})
    if row['status'] in ('CANCELLING', 'CANCELLED'):
        progress['stage'] = row['status']
    elapsed = max(0, int(((row['finished_at'] or datetime.now(timezone.utc)) -
                          (row['started_at'] or row['created_at'])).total_seconds()))
    return {'runId': str(row['id']), 'status': 'STALE' if stale and row['status']=='DONE' else row['status'],
            'stale': stale, 'model': row['model'], 'focus': row['focus'],
            'structured': row['structured'] if not stale and row['status']=='DONE' else None,
            'evidence': row['evidence'] if not stale and row['status']=='DONE' else None,
            'warnings': row['warnings'] or [], 'meta': meta,
            'progress': progress, 'elapsedSeconds': elapsed,
            'error': row['error_text'] or '',
            'createdAt': row['created_at'].isoformat(),
            'finishedAt': row['finished_at'].isoformat() if row['finished_at'] else None}
