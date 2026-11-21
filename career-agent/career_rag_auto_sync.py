"""Stage03: existing PostgreSQL Word import -> Qdrant automatic incremental sync.

Does not parse DOCX again or mutate existing Word/Employee/Excel tables.
Only the structured document produced by the EXISTING Word import/parser is indexed.
Fail-closed on unknown source schemas. A missing/empty structured field is deferred.

Commands:
  python career_rag_auto_sync.py inspect   (read-only DB schema and data readiness)
  python career_rag_auto_sync.py once      (process existing eligible imports once)
  python career_rag_auto_sync.py daemon    (continuously process updates)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import signal
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

LOG = logging.getLogger("career-rag-sync")
SOURCE_TABLE = "public.career_word_import"
STATE_TABLE = "public.career_word_rag_sync"
MAX_SCAN = max(1, min(int(os.getenv("CAREER_RAG_SYNC_MAX_SCAN", "5000")), 50000))
BATCH_SIZE = 100
STOP = False


def connect_db():
    import psycopg
    return psycopg.connect(
        host=os.getenv("DATABASE_HOST", "career-db"),
        port=int(os.getenv("DATABASE_PORT", "5432")),
        dbname=os.environ["DATABASE_NAME"],
        user=os.environ["DATABASE_USER"],
        password=os.environ["DATABASE_PASSWORD"],
        connect_timeout=8,
        autocommit=True,
    )


def get_columns(conn, table_name: str) -> set[str]:
    schema, name = table_name.split(".")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s", (schema, name),
        )
        return {r[0] for r in cur.fetchall()}


def choose_source_columns(columns: set[str]) -> tuple[str, str | None]:
    if "structured" not in columns:
        raise RuntimeError("career_word_import.structured 컬럼이 없습니다. 기존 API 스키마 확인 필요")
    ids = [v for v in ("id", "import_id", "word_import_id") if v in columns]
    if not ids:
        raise RuntimeError("career_word_import ID 컬럼을 확인할 수 없습니다")
    ordered_by = next((v for v in ("updated_at", "created_at") if v in columns), None)
    return ids[0], ordered_by


def decode_structured(raw: Any) -> dict[str, Any] | list[dict[str, Any]] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        if not raw.strip():
            return None
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise ValueError("structured JSON 파싱 실패") from exc
    if not isinstance(raw, (dict, list)):
        raise ValueError("structured는 JSON 객체/목록이어야 합니다")
    return raw


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")).hexdigest()


def profile_id_for_import(import_id: str) -> str:
    try:
        return "word_" + UUID(str(import_id)).hex
    except (ValueError, AttributeError) as exc:
        raise ValueError("기존 Word import ID가 UUID가 아닙니다. 프로파일 연결을 중단합니다") from exc


def fetch_rows(conn, id_col: str, order_col: str | None, limit: int, offset: int):
    from psycopg import sql
    query = sql.SQL('SELECT {}::text, structured FROM {}').format(
        sql.Identifier(id_col), sql.SQL(SOURCE_TABLE),
    )
    if order_col:
        query += sql.SQL(' ORDER BY {} DESC NULLS LAST, {} DESC').format(
            sql.Identifier(order_col), sql.Identifier(id_col),
        )
    else:
        query += sql.SQL(' ORDER BY {} DESC').format(sql.Identifier(id_col))
    query += sql.SQL(' LIMIT %s OFFSET %s')
    with conn.cursor() as cur:
        cur.execute(query, (limit, offset))
        return cur.fetchall()


def get_state(conn, import_id: str):
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT source_hash, status, attempts, next_retry_at FROM {STATE_TABLE} "
            "WHERE import_id=%s", (import_id,),
        )
        return cur.fetchone()


def state_needed(state, source_hash: str, now: datetime) -> bool:
    if not state:
        return True
    old_hash, status, attempts, retry_at = state
    if old_hash != source_hash:
        return True
    if status in ("INDEXED", "WAITING_FOR_STRUCTURED", "WAITING_FOR_PROJECTS"):
        return False
    if status == "FAILED":
        return retry_at is None or retry_at <= now
    return False


def mark(conn, import_id: str, source_hash: str, profile_id: str, status: str,
         attempts: int, message: str | None = None, retry_seconds: int = 0):
    with conn.cursor() as cur:
        cur.execute(f"""
            INSERT INTO {STATE_TABLE} (import_id, source_hash, profile_id, status,
                attempts, last_error, next_retry_at, last_indexed_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s,
                CASE WHEN %s > 0 THEN NOW() + (%s * INTERVAL '1 second') ELSE NULL END,
                CASE WHEN %s='INDEXED' THEN NOW() ELSE NULL END, NOW())
            ON CONFLICT (import_id) DO UPDATE SET
                source_hash=EXCLUDED.source_hash,
                profile_id=EXCLUDED.profile_id,
                status=EXCLUDED.status,
                attempts=EXCLUDED.attempts,
                last_error=EXCLUDED.last_error,
                next_retry_at=EXCLUDED.next_retry_at,
                last_indexed_at=COALESCE(EXCLUDED.last_indexed_at, {STATE_TABLE}.last_indexed_at),
                updated_at=NOW()
        """, (import_id, source_hash, profile_id, status, attempts, message,
              retry_seconds, retry_seconds, status))


def inspect() -> dict:
    with connect_db() as conn:
        cols = get_columns(conn, SOURCE_TABLE)
        ident, order_by = choose_source_columns(cols)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE structured IS NOT NULL) "
                        "FROM public.career_word_import")
            total, with_structured = cur.fetchone()
            cur.execute("SELECT to_regclass('public.career_word_rag_sync')")
            state_table_exists = cur.fetchone()[0] is not None
        return {
            "source": SOURCE_TABLE, "id_column": ident, "order_column": order_by,
            "total_imports": total, "non_null_structured": with_structured,
            "state_table_exists": state_table_exists,
            "rag_collection": os.getenv("CAREER_RAG_COLLECTION", "career_profiles_v1"),
            "auto_sync_enabled": os.getenv("CAREER_RAG_AUTO_SYNC", "false"),
        }


def run_once() -> dict:
    # Single-worker advisory lock: avoid overlapping Qdrant indexing across processes.
    from career_rag_indexer import prepare_chunks, index_profile, EmptyProjectsError
    counts = {"scanned": 0, "indexed": 0, "waiting": 0, "skipped": 0, "failed": 0}
    with connect_db() as conn:
        cols = get_columns(conn, SOURCE_TABLE)
        ident, order_by = choose_source_columns(cols)
        if not get_columns(conn, STATE_TABLE):
            raise RuntimeError("먼저 sql/003_word_rag_sync.sql 마이그레이션을 적용하세요")
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (708003,))
            if not cur.fetchone()[0]:
                return {**counts, "busy": True}
        try:
            for offset in range(0, MAX_SCAN, BATCH_SIZE):
                rows = fetch_rows(conn, ident, order_by, min(BATCH_SIZE, MAX_SCAN - offset), offset)
                if not rows:
                    break
                for import_id, raw in rows:
                    counts["scanned"] += 1
                    hashval = digest(raw)
                    try:
                        profile_id = profile_id_for_import(import_id)
                        parsed = decode_structured(raw)
                        hashval = digest(parsed)
                        state = get_state(conn, import_id)
                        if not state_needed(state, hashval, datetime.now(timezone.utc)):
                            counts["skipped"] += 1
                            continue
                        attempts = (state[2] if state and state[0] == hashval else 0) + 1
                        if parsed is None:
                            mark(conn, import_id, hashval, profile_id,
                                 "WAITING_FOR_STRUCTURED", attempts)
                            counts["waiting"] += 1
                            continue
                        # If no recognized projects, leave prior index untouched and retry on edit.
                        chunks, _ = prepare_chunks(profile_id, parsed,
                                                          revision=hashval[:16])
                        if not chunks:
                            raise ValueError("색인 가능한 프로젝트가 없습니다")
                        result = index_profile(profile_id, parsed,
                                               revision=hashval[:16])
                        mark(conn, import_id, hashval, profile_id, "INDEXED", attempts)
                        counts["indexed"] += 1
                        LOG.info("INDEXED import=%s profile=%s projects=%s chunks=%s",
                                 import_id, profile_id, result["projects"], result["chunks_upserted"])
                    except EmptyProjectsError as exc:
                        mark(conn, import_id, hashval, profile_id,
                             "WAITING_FOR_PROJECTS", attempts, str(exc)[:500])
                        counts["waiting"] += 1
                        LOG.warning("WAITING_FOR_PROJECTS import=%s: %s", import_id, str(exc)[:300])
                    except (Exception,) as exc:
                        counts["failed"] += 1
                        LOG.error("FAILED import=%s type=%s: %s", import_id,
                                  type(exc).__name__, str(exc)[:300])
                        try:
                            profile_id = profile_id_for_import(import_id)
                            old = get_state(conn, import_id)
                            tries = (old[2] if old and old[0] == hashval else 0) + 1
                            retry = min(3600, 30 * 2 ** min(tries - 1, 7))
                            mark(conn, import_id, hashval, profile_id, "FAILED", tries,
                                 str(exc)[:500], retry)
                        except Exception:
                            LOG.exception("FAILED recording error state for import=%s", import_id)
                if len(rows) < BATCH_SIZE:
                    break
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (708003,))
    return counts


def _stop(*_):
    global STOP
    STOP = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["inspect", "once", "daemon"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "inspect":
        print(json.dumps(inspect(), ensure_ascii=False, indent=2))
        return
    if args.command == "once":
        print(json.dumps(run_once(), ensure_ascii=False, indent=2))
        return
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, _stop)
    wait = max(10, min(int(os.getenv("CAREER_RAG_SYNC_INTERVAL_SECONDS", "30")), 3600))
    LOG.info("Auto sync enabled; interval=%ss", wait)
    while not STOP:
        try:
            LOG.info("SYNC %s", json.dumps(run_once(), ensure_ascii=False))
        except Exception:
            LOG.exception("SYNC LOOP ERROR (will retry)")
        for _ in range(wait):
            if STOP:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
