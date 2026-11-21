"""Sync a reviewed Word record into the existing saved career profiles.

The Excel import flow is NOT changed. Word records remain the editing source of
truth; this module writes their reviewed snapshot to the existing employee,
profile_import, employee_education, employee_certification, and career_project
schema inside the caller's DB transaction.

One Word import UUID maps to one stable employeeKey; names are *never* used to
merge people. No automatic profile approval is performed.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from psycopg.types.json import Jsonb

MAX_SOURCE_BYTES = 15 * 1024 * 1024
WORD_NAMESPACE = uuid.UUID('8fe73602-52a5-4653-9e0d-7cabf8cdd2ce')


def word_employee_key(import_id: uuid.UUID) -> str:
    """Stable, collision-resistant key; prevents unverified merges by name."""
    return 'word_' + import_id.hex


def _date_or_none(value: object, field: str) -> date | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, f'{field}: 날짜는 YYYY-MM-DD 형식이어야 합니다.') from exc


def _text(obj: dict, key: str, limit: int) -> str:
    return str(obj.get(key) or '').strip()[:limit]


def _source_sha(import_id: uuid.UUID, source_path: str) -> str:
    # Do not trust source_path blindly. Word upload stores files beneath a
    # directory named after the UUID. Reject symlinks and nonregular files.
    import os
    base = Path(os.getenv('WORD_IMPORT_DIR', '/app/uploads/word-imports')).resolve()
    expected = base / str(import_id)
    path = Path(source_path)
    if path.is_symlink() or path.resolve().parent != expected.resolve() or not path.is_file():
        raise HTTPException(409, 'Word 원본 파일 경로를 확인할 수 없어 프로파일 등록을 중단했습니다.')
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise HTTPException(413, 'Word 원본 파일 크기가 허용 범위를 초과했습니다.')
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def convert_word_profile(structured: dict, *, filename: str, source_sha256: str,
                         import_id: uuid.UUID) -> dict[str, Any]:
    """Produce the same parsed_json shape used by the existing Excel listing."""
    name = _text(structured, 'name', 100)
    if not name:
        raise HTTPException(422, '프로파일 등록에는 성명이 필요합니다.')

    born = _date_or_none(structured.get('birthDate'), '생년월일')
    raw_projects = structured.get('projects') or []
    if not isinstance(raw_projects, list) or len(raw_projects) > 100:
        raise HTTPException(422, '프로젝트는 최대 100건까지 등록할 수 있습니다.')

    education_text = _text(structured, 'educationText', 250)
    education = []
    if education_text:
        parts = [p.strip() for p in education_text.split(' / ')]
        education.append({
            'level': _text(structured, 'degree', 80),
            'school_name': parts[0][:200],
            'major': parts[1][:200] if len(parts) > 1 else '',
            'completion_status': ' / '.join(parts[2:])[:100] if len(parts) > 2 else '',
            'admission_date': None, 'graduation_date': None, 'source_row': 0,
        })

    certificates = []
    raw_certs = _text(structured, 'certificationsText', 300)
    if raw_certs:
        for part in re.split(r'[,;\n]+', raw_certs):
            part = part.strip()
            if part:
                certificates.append({'certification_name': part[:200], 'issuer': '',
                                     'obtained_date': None, 'source_row': len(certificates)})

    projects = []
    for idx, item in enumerate(raw_projects):
        if not isinstance(item, dict):
            raise HTTPException(422, f'{idx+1}번째 프로젝트 데이터가 잘못되었습니다.')
        title = _text(item, 'projectName', 500)
        if not title:
            continue  # incomplete draft stays in Word; not a saved project yet
        start = _date_or_none(item.get('startDate'), f'{idx+1}번째 프로젝트 시작일')
        end = _date_or_none(item.get('endDate'), f'{idx+1}번째 프로젝트 종료일')
        if start is not None and end is not None and end < start:
            raise HTTPException(422, f'{idx+1}번째 프로젝트 종료일이 시작일보다 빠릅니다.')
        projects.append({
            'project_name': title,
            'start_date': start.isoformat() if start else None,
            'end_date': end.isoformat() if end else None,
            'source_duration_text': _text(item, 'periodText', 100),
            'role_text': _text(item, 'submittedRole', 500),
            'technology_text': _text(item, 'technologiesText', 500),
            'client_name': _text(item, 'clientName', 250),
            'source_row': idx, 'duplicate_suspected': False,
            'include': item.get('include') is not False,
            # Keep all fields in parsed_json, not just relational columns.
            'reviewed': dict(item),
        })

    warnings = list(structured.get('extractionWarnings') or [])
    return {
        'source': {'file_name': filename, 'sha256': source_sha256,
                   'template_version': 'word-stage02-8', 'word_import_id': str(import_id)},
        'profile': {
            'name': name,
            'birth_date': born.isoformat() if born else None,
            'position_title': _text(structured, 'positionTitle', 100),
            'engineer_grade': _text(structured, 'displayGrade', 100),
            'affiliation': _text(structured, 'affiliation', 120),
            'degree': _text(structured, 'degree', 80),
            'education_text': education_text,
            'certifications_text': raw_certs,
            'education': education, 'certifications': certificates,
            'projects': projects,
        },
        'summary': {'error_count': 0, 'warning_count': len(warnings),
                    'project_count': len(projects)},
        'validation_issues': [
            {'code': 'WORD_REVIEW', 'severity': 'WARNING', 'message': str(w)[:1000]}
            for w in warnings[:30]
        ],
    }


def sync_word_saved_profile(cn, import_id: uuid.UUID, structured: dict, tenant_id: str) -> str:
    """Upsert saved profile in the same transaction as the Word /structured PUT.

    Repeated saves of the same Word use one employee and one profile_import row.
    The source profile keeps status NEEDS_REVIEW; no silent approval.
    """
    row = cn.execute(
        '''SELECT original_filename,source_path FROM career_word_import
           WHERE id=%s AND tenant_id=%s FOR UPDATE''', (import_id, tenant_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, 'Word 등록 데이터를 찾지 못했습니다.')

    digest = _source_sha(import_id, row['source_path'])
    parsed = convert_word_profile(structured, filename=row['original_filename'],
                                  source_sha256=digest, import_id=import_id)
    person = parsed['profile']
    employee_id = str(uuid.uuid5(WORD_NAMESPACE, str(import_id)))
    employee_key = word_employee_key(import_id)
    profile_import_id = str(import_id)

    old = cn.execute('''SELECT e.employee_id,p.parsed_json
        FROM employee e LEFT JOIN profile_import p
          ON p.tenant_id=e.tenant_id AND p.employee_id=e.employee_id AND p.import_id=%s
        WHERE e.tenant_id=%s AND e.employee_key=%s FOR UPDATE OF e''',
        (profile_import_id, tenant_id, employee_key)).fetchone()
    if old and str(old['employee_id']).strip() != employee_id:
        raise HTTPException(409, 'Word 직원 키에 다른 직원이 연결되어 있어 등록을 중단했습니다.')
    if old and old.get('parsed_json') == parsed:
        return employee_key  # identical resave, no duplicate rows or version churn

    cn.execute('''INSERT INTO employee
        (employee_id,tenant_id,employee_key,full_name,birth_date,position_title,
         engineer_grade,profile_version)
        VALUES (%s,%s,%s,%s,%s,%s,%s,1)
        ON CONFLICT (tenant_id,employee_key) DO UPDATE SET
          full_name=EXCLUDED.full_name,birth_date=EXCLUDED.birth_date,
          position_title=EXCLUDED.position_title,engineer_grade=EXCLUDED.engineer_grade,
          profile_version=employee.profile_version+1,updated_at=CURRENT_TIMESTAMP''',
        (employee_id, tenant_id, employee_key, person['name'], person['birth_date'],
         person['position_title'] or None, person['engineer_grade'] or None))

    status = 'NEEDS_REVIEW' if parsed['summary']['warning_count'] else 'PARSED'
    cn.execute('''INSERT INTO profile_import
        (import_id,tenant_id,employee_id,source_file_name,source_sha256,
         source_storage_key,template_version,status,parsed_json,issue_count)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (import_id) DO UPDATE SET
          source_file_name=EXCLUDED.source_file_name,
          source_sha256=EXCLUDED.source_sha256,
          status=EXCLUDED.status,parsed_json=EXCLUDED.parsed_json,
          issue_count=EXCLUDED.issue_count''',
        (profile_import_id, tenant_id, employee_id, row['original_filename'], digest,
         'word-import:' + str(import_id), 'word-stage02-8', status,
         Jsonb(parsed), len(parsed['validation_issues'])))

    # Existing revisions remain source-scoped; Excel imports and projects belonging
    # to other imports are never modified. Re-activate only current Word rows.
    for table in ('employee_education', 'employee_certification', 'career_project'):
        cn.execute(f'UPDATE {table} SET is_current=FALSE WHERE tenant_id=%s AND import_id=%s',
                   (tenant_id, profile_import_id))

    for item in person['education']:
        cn.execute('''INSERT INTO employee_education
            (education_id,tenant_id,employee_id,import_id,level_name,school_name,major,
             admission_date,graduation_date,completion_status,source_row,is_current)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE)
            ON CONFLICT (import_id,source_row) DO UPDATE SET
              level_name=EXCLUDED.level_name,school_name=EXCLUDED.school_name,
              major=EXCLUDED.major,completion_status=EXCLUDED.completion_status,
              is_current=TRUE''',
            (str(uuid.uuid4()), tenant_id, employee_id, profile_import_id,
             item['level'], item['school_name'], item['major'],
             None, None, item['completion_status'], item['source_row']))

    for item in person['certifications']:
        cn.execute('''INSERT INTO employee_certification
            (certification_id,tenant_id,employee_id,import_id,certification_name,
             issuer,obtained_date,source_row,is_current)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,TRUE)
            ON CONFLICT (import_id,source_row) DO UPDATE SET
              certification_name=EXCLUDED.certification_name,issuer=EXCLUDED.issuer,
              obtained_date=EXCLUDED.obtained_date,is_current=TRUE''',
            (str(uuid.uuid4()), tenant_id, employee_id, profile_import_id,
             item['certification_name'], None, None, item['source_row']))

    for item in person['projects']:
        cn.execute('''INSERT INTO career_project
            (project_id,tenant_id,employee_id,import_id,project_name,start_date,end_date,
             source_duration_text,role_text,technology_text,client_name,source_row,
             duplicate_suspected,approval_status,is_current)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE,%s,TRUE)
            ON CONFLICT (import_id,source_row) DO UPDATE SET
              project_name=EXCLUDED.project_name,start_date=EXCLUDED.start_date,
              end_date=EXCLUDED.end_date,source_duration_text=EXCLUDED.source_duration_text,
              role_text=EXCLUDED.role_text,technology_text=EXCLUDED.technology_text,
              client_name=EXCLUDED.client_name,approval_status=EXCLUDED.approval_status,
              is_current=TRUE,project_version=career_project.project_version+1''',
            (str(uuid.uuid4()), tenant_id, employee_id, profile_import_id,
             item['project_name'], item['start_date'], item['end_date'],
             item['source_duration_text'], item['role_text'],
             item['technology_text'], item['client_name'], item['source_row'],
             'PENDING' if item['include'] else 'EXCLUDED'))

    # Keep validation issues in sync; these contain warning text only.
    cn.execute('DELETE FROM profile_validation_issue WHERE tenant_id=%s AND import_id=%s',
               (tenant_id, profile_import_id))
    for item in parsed['validation_issues']:
        cn.execute('''INSERT INTO profile_validation_issue
          (issue_id,tenant_id,import_id,issue_code,severity,source_cell,source_row,message)
          VALUES (%s,%s,%s,%s,%s,NULL,NULL,%s)''',
          (str(uuid.uuid4()), tenant_id, profile_import_id,
           item['code'], item['severity'], item['message']))
    return employee_key
