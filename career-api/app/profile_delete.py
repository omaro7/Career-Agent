"""Delete one saved profile and its dependent data, never proposal templates.

Routes are scoped by tenant and employee key. All PostgreSQL mutations happen in
one transaction; Qdrant and Word file cleanup happen after commit and are reported
as warnings on failure. This module is intended for the local_single_user deployment.

Protected: career_pptx_template_registry, resume_template, tenant, ./templates.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from psycopg import DatabaseError

from .db import connect
from .profiles import TENANT

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/career/employees", tags=["saved-profile-delete"])
KEY = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
COLLECTION = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")

# Known BWG tables. Do not use CASCADE, wildcard table names, or DELETE without a scope.
# Reverse-dependency order matters; PostgreSQL also rejects any missed FK rows.
DEPENDENTS = (
    "career_ai_edit_proposal",
    "career_word_ai_run",
    "career_word_rag_sync",
    "vector_index_outbox",
    "profile_validation_issue",
    "employee_education",
    "employee_certification",
    "career_project",
)
REQUIRED = frozenset({
    "employee", "profile_import", "career_project", "vector_index_outbox",
    "employee_education", "employee_certification", "profile_validation_issue",
})


def _check_request(request: Request) -> None:
    if os.getenv("CAREER_ACCESS_MODE", "") != "local_single_user":
        raise HTTPException(403, "현재 접근 모드에서는 프로파일 삭제 기능이 비활성화되어 있습니다.")
    if request.headers.get("x-career-delete-intent") != "delete-profile":
        raise HTTPException(403, "삭제 확인 헤더가 없습니다.")
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site not in ("same-origin", "none"):
        raise HTTPException(403, "다른 사이트에서 삭제를 요청할 수 없습니다.")
    # Sec-Fetch-Site/intent-header are CSRF mitigations, NOT user authentication.


def _columns(cn) -> dict[str, set[str]]:
    rows = cn.execute("""
        SELECT table_name,column_name FROM information_schema.columns
        WHERE table_schema='public'
          AND table_name = ANY(%s::text[])
    """, (list(REQUIRED | set(DEPENDENTS) | {"career_word_import", "profile_import"}),)).fetchall()
    result: dict[str, set[str]] = {}
    for row in rows:
        result.setdefault(row["table_name"], set()).add(row["column_name"])
    missing = REQUIRED - result.keys()
    if missing:
        raise HTTPException(409, "DB 구조를 확인해야 합니다. 필수 테이블 누락: " + ", ".join(sorted(missing)))
    for name in ("employee", "profile_import", "career_project"):
        if "tenant_id" not in result[name]:
            raise HTTPException(409, f"{name}: tenant_id 필드가 없어 삭제를 중단했습니다.")
    return result


def _delete_scoped(cn, table: str, columns: set[str], ids: dict[str, list[str]]) -> int:
    """Delete only rows matched by concrete employee/import/project identifiers."""
    allow = ("employee_id", "employee_key", "import_id", "word_import_id",
             "source_import_id", "project_id", "profile_id")
    clauses: list[str] = []
    values: list[object] = []
    for column in allow:
        if column in columns and ids.get(column):
            # Cast enables matching UUID or text identifiers with a safely bound text[].
            clauses.append(f'"{column}"::text = ANY(%s::text[])')
            values.append(ids[column])
    if not clauses:
        # A populated, unrecognized table must be reviewed rather than silently orphaned.
        row = cn.execute(f'SELECT EXISTS (SELECT 1 FROM "public"."{table}") AS present').fetchone()
        if row["present"]:
            raise HTTPException(409, f"{table}: 직원 연관 열을 확인할 수 없어 삭제를 중단했습니다.")
        return 0
    tenant_filter = '"tenant_id"=%s AND ' if "tenant_id" in columns else ""
    params = ([TENANT] if tenant_filter else []) + values
    cursor = cn.execute(
        f'DELETE FROM "public"."{table}" WHERE ' + tenant_filter + '(' + ' OR '.join(clauses) + ')',
        tuple(params),
    )
    return cursor.rowcount


def _clear_word_files(import_ids: list[str]) -> list[str]:
    warnings: list[str] = []
    base = Path(os.getenv("WORD_IMPORT_DIR", "/app/uploads/word-imports")).resolve()
    for import_id in import_ids:
        try:
            parsed = uuid.UUID(import_id)
            folder = base / str(parsed)
            if folder.is_symlink() or folder.resolve().parent != base:
                raise ValueError("예상 경로에서 벗어난 Word 폴더")
            if folder.is_dir():
                shutil.rmtree(folder)
        except (OSError, ValueError) as exc:
            logger.warning("Word files cleanup failed for %s: %s", import_id, exc)
            warnings.append(f"Word 파일 {import_id} 정리 실패: 운영자 확인 필요")
    return warnings


def _clear_qdrant(employee_key: str) -> list[str]:
    warnings: list[str] = []
    collection = os.getenv("CAREER_RAG_COLLECTION", "career_profiles_v1")
    if not COLLECTION.fullmatch(collection):
        return ["Qdrant 컬렉션명 확인 필요"]
    base = os.getenv("QDRANT_URL", "http://career-qdrant:6333").rstrip("/")
    try:
        with httpx.Client(timeout=12) as client:
            status = client.get(f"{base}/collections/{collection}")
            if status.status_code == 404:
                return []
            status.raise_for_status()
            response = client.post(
                f"{base}/collections/{collection}/points/delete",
                params={"wait": "true"},
                json={"filter": {"must": [{"key": "profile_id", "match": {"value": employee_key}}]}},
            )
            response.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Qdrant delete failed for %s: %s", employee_key, exc)
        warnings.append("Qdrant 색인 삭제 확인 필요: DB 삭제는 완료됨")
    return warnings


@router.delete("/{employee_key}")
def delete_saved_profile(employee_key: str, request: Request):
    _check_request(request)
    if not KEY.fullmatch(employee_key):
        raise HTTPException(422, "잘못된 직원 식별키입니다.")
    try:
        with connect() as cn:
            columns = _columns(cn)
            row = cn.execute(
                """SELECT employee_id::text AS employee_id FROM employee
                   WHERE tenant_id=%s AND employee_key=%s FOR UPDATE""",
                (TENANT, employee_key),
            ).fetchone()
            if not row:
                raise HTTPException(404, "등록된 프로파일을 찾을 수 없습니다.")
            employee_id = row["employee_id"]
            imports = [str(x["import_id"]) for x in cn.execute(
                """SELECT import_id FROM profile_import
                   WHERE tenant_id=%s AND employee_id=%s""", (TENANT, employee_id),
            ).fetchall()]
            projects = [str(x["project_id"]) for x in cn.execute(
                """SELECT project_id FROM career_project
                   WHERE tenant_id=%s AND employee_id=%s""", (TENANT, employee_id),
            ).fetchall()]
            word_import_ids: list[str] = []
            if "career_word_import" in columns and imports:
                if not {"id", "tenant_id"} <= columns["career_word_import"]:
                    raise HTTPException(409, "Word 테이블 구조가 변경되어 삭제를 중단했습니다.")
                word_import_ids = [str(x["id"]) for x in cn.execute(
                    """SELECT id FROM career_word_import
                       WHERE tenant_id=%s AND id::text = ANY(%s::text[])""",
                    (TENANT, imports),
                ).fetchall()]
            # A generated Word employee key must map exactly to that Word UUID.
            if employee_key.startswith("word_"):
                try:
                    expected_word_id = str(uuid.UUID(hex=employee_key[5:]))
                except ValueError as exc:
                    raise HTTPException(409, "Word 프로파일 식별키 형식이 올바르지 않습니다.") from exc
                if expected_word_id not in word_import_ids:
                    raise HTTPException(409, "Word 원본 연결 관계가 일치하지 않아 삭제를 중단했습니다.")
            ids = {
                "employee_id": [employee_id],
                "employee_key": [employee_key],
                "import_id": imports,
                "word_import_id": word_import_ids,
                "source_import_id": word_import_ids,
                "project_id": projects,
                "profile_id": [employee_key],
            }
            affected: dict[str, int] = {}
            for table in DEPENDENTS:
                if table in columns:
                    affected[table] = _delete_scoped(cn, table, columns[table], ids)
            affected["profile_import"] = _delete_scoped(cn, "profile_import", columns["profile_import"], ids)
            if word_import_ids:
                cn.execute("""DELETE FROM career_word_import
                              WHERE tenant_id=%s AND id::text=ANY(%s::text[])""",
                           (TENANT, word_import_ids))
            # No CASCADE, no whole-table delete, no templates touched.
            cn.execute("DELETE FROM employee WHERE tenant_id=%s AND employee_id=%s",
                       (TENANT, employee_id))
    except HTTPException:
        raise
    except DatabaseError as exc:
        logger.exception("Profile delete rolled back for %s", employee_key)
        raise HTTPException(409, "DB 연관 관계로 인해 삭제할 수 없습니다. 관리자 로그를 확인하세요. DB 변경은 취소되었습니다.") from exc

    # These external cleanups are best effort; PostgreSQL transaction has committed.
    warnings = _clear_word_files(word_import_ids)
    warnings += _clear_qdrant(employee_key)
    return {
        "deleted": True,
        "employeeKey": employee_key,
        "deletedRowCounts": affected,
        "wordImportsDeleted": len(word_import_ids),
        "warnings": warnings,
    }
