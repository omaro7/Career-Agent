"""Port of CareerProfileController + CareerProfileImportService.
Preserves legacy endpoint shapes and no-automatic-approval policy.
"""
import copy
import hashlib
import os
import re
import uuid
from datetime import date
from zoneinfo import ZoneInfo
import httpx
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from psycopg.types.json import Jsonb
from .db import connect

router = APIRouter(prefix='/api/v1/career', tags=['career'])
TENANT = '00000000-0000-4000-8000-000000000001'
KEY_RE = re.compile(r'[A-Za-z0-9_-]{1,80}\Z')
MAX_FILE = 10 * 1024 * 1024


def today():
    return date.today()  # query-time age uses server-local UTC/KST only for date; see below


def computed_age(result):
    result = copy.deepcopy(result)
    birth = (result.get('profile') or {}).get('birth_date')
    if birth:
        from datetime import datetime
        now = datetime.now(ZoneInfo('Asia/Seoul')).date()
        b = date.fromisoformat(birth)
        result.setdefault('computed', {}).update(as_of_date=now.isoformat(), age_years=now.year-b.year-((now.month,now.day)<(b.month,b.day)))
    return result


def get_import(cn, import_id):
    row = cn.execute('''SELECT p.import_id,p.status,p.parsed_json,e.employee_key
       FROM profile_import p JOIN employee e ON e.employee_id=p.employee_id AND e.tenant_id=p.tenant_id
       WHERE p.tenant_id=%s AND p.import_id=%s''', (TENANT, import_id)).fetchone()
    if not row:
        raise HTTPException(404, '등록 내역을 찾을 수 없습니다.')
    return dict(importId=row['import_id'].strip(), employeeKey=row['employee_key'], status=row['status'],
                alreadyImported=False, result=computed_age(row['parsed_json']))


def _date(v):
    return date.fromisoformat(v) if v else None


def save_draft(cn, key, filename, parsed):
    profile = parsed.get('profile') or {}
    if not profile.get('name') or not profile.get('birth_date'):
        raise HTTPException(422, '이름과 생년월일은 필수입니다.')
    if (parsed.get('summary') or {}).get('error_count', 1) > 0:
        raise HTTPException(422, '검증 오류가 남아 있습니다.')
    digest = (parsed.get('source') or {}).get('sha256', '')
    if not re.fullmatch('[a-f0-9]{64}', digest):
        raise HTTPException(422, '잘못된 파일 해시입니다.')
    cn.execute('''INSERT INTO tenant(tenant_id,tenant_code,tenant_name)
                  VALUES (%s,'LOCAL','로컬 사용자') ON CONFLICT DO NOTHING''', (TENANT,))
    # Serialize employeeKey imports with transaction-scoped advisory lock (also works for new employees).
    cn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (TENANT+':'+key,))
    old = cn.execute('SELECT employee_id,full_name,birth_date FROM employee WHERE tenant_id=%s AND employee_key=%s FOR UPDATE', (TENANT,key)).fetchone()
    if old is None:
        employee_id = str(uuid.uuid4())
        cn.execute('''INSERT INTO employee(employee_id,tenant_id,employee_key,full_name,birth_date,position_title,engineer_grade,profile_version)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,1)''', (employee_id,TENANT,key,profile['name'],_date(profile['birth_date']),profile.get('position_title'),profile.get('engineer_grade')))
    else:
        employee_id = old['employee_id'].strip()
        if old['full_name'] != profile['name'] or str(old['birth_date']) != profile['birth_date']:
            raise HTTPException(409, '직원 식별키에 다른 사람의 프로파일이 등록되어 있습니다. 별도 직원 키를 사용하세요.')
    dup = cn.execute('SELECT import_id FROM profile_import WHERE tenant_id=%s AND employee_id=%s AND source_sha256=%s', (TENANT,employee_id,digest)).fetchone()
    if dup:
        result = get_import(cn, dup['import_id'])
        result['alreadyImported'] = True
        return result
    if old:
        cn.execute('UPDATE employee SET position_title=%s, engineer_grade=%s, profile_version=profile_version+1, updated_at=CURRENT_TIMESTAMP WHERE tenant_id=%s AND employee_id=%s', (profile.get('position_title'),profile.get('engineer_grade'),TENANT,employee_id))
        for table in ('employee_education','employee_certification','career_project'):
            cn.execute(f'UPDATE {table} SET is_current=FALSE WHERE tenant_id=%s AND employee_id=%s AND is_current=TRUE', (TENANT,employee_id))
    import_id = str(uuid.uuid4())
    status = 'NEEDS_REVIEW' if (parsed.get('summary') or {}).get('warning_count',0) else 'PARSED'
    cn.execute('''INSERT INTO profile_import(import_id,tenant_id,employee_id,source_file_name,source_sha256,source_storage_key,template_version,status,parsed_json,issue_count)
       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''', (import_id,TENANT,employee_id,filename,digest,'not-retained:sha256:'+digest,
       (parsed.get('source') or {}).get('template_version') or 'unknown',status,Jsonb(parsed),len(parsed.get('validation_issues') or [])))
    for x in profile.get('education') or []:
        cn.execute('''INSERT INTO employee_education(education_id,tenant_id,employee_id,import_id,level_name,school_name,major,admission_date,graduation_date,completion_status,source_row)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''', (str(uuid.uuid4()),TENANT,employee_id,import_id,x.get('level'),x.get('school_name'),x.get('major'),_date(x.get('admission_date')),_date(x.get('graduation_date')),x.get('completion_status'),x.get('source_row',0)))
    for x in profile.get('certifications') or []:
        cn.execute('''INSERT INTO employee_certification(certification_id,tenant_id,employee_id,import_id,certification_name,issuer,obtained_date,source_row)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''', (str(uuid.uuid4()),TENANT,employee_id,import_id,x.get('certification_name'),x.get('issuer'),_date(x.get('obtained_date')),x.get('source_row',0)))
    for x in profile.get('projects') or []:
        cn.execute('''INSERT INTO career_project(project_id,tenant_id,employee_id,import_id,project_name,start_date,end_date,source_duration_text,role_text,technology_text,client_name,source_row,duplicate_suspected)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''', (str(uuid.uuid4()),TENANT,employee_id,import_id,x.get('project_name'),_date(x.get('start_date')),_date(x.get('end_date')),x.get('source_duration_text'),x.get('role_text'),x.get('technology_text'),x.get('client_name'),x.get('source_row',0),bool(x.get('duplicate_suspected'))))
    for x in parsed.get('validation_issues') or []:
        cn.execute('''INSERT INTO profile_validation_issue(issue_id,tenant_id,import_id,issue_code,severity,source_cell,source_row,message)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''', (str(uuid.uuid4()),TENANT,import_id,x.get('code') or 'UNKNOWN',x.get('severity') or 'WARNING',x.get('source_cell'),x.get('project_source_row'),x.get('message') or ''))
    return dict(importId=import_id,employeeKey=key,status=status,alreadyImported=False,result=parsed)


@router.post('/profile-imports')
async def upload(file: UploadFile=File(...), employeeKey: str=Form(...), asOfDate: str|None=Form(None)):
    key=employeeKey.strip()
    if not KEY_RE.fullmatch(key):
        raise HTTPException(400, '직원키는 영문·숫자·_·-만 사용할 수 있습니다(최대 80자).')
    filename=(file.filename or 'uploaded.xlsx').replace('\\','/').rsplit('/',1)[-1].strip()
    if not filename or len(filename)>240 or not filename.lower().endswith('.xlsx'):
        raise HTTPException(400, '확장자가 .xlsx인 파일만 업로드할 수 있습니다.')
    try:
        day=date.fromisoformat(asOfDate) if asOfDate else __import__('datetime').datetime.now(ZoneInfo('Asia/Seoul')).date()
    except ValueError:
        raise HTTPException(400, '기준일 형식은 YYYY-MM-DD입니다.')
    raw=await file.read(MAX_FILE+1)
    if len(raw)==0 or len(raw)>MAX_FILE:
        raise HTTPException(413, 'Excel 파일은 최대 10MB입니다.')
    agent_url=os.environ.get('AGENT_BASE_URL','http://career-agent:8001').rstrip('/')
    try:
        async with httpx.AsyncClient(timeout=50) as cli:
            res=await cli.post(f'{agent_url}/parse-profile', params={'as_of':str(day)}, content=raw, headers={'Content-Type':'application/octet-stream'})
    except httpx.RequestError:
        raise HTTPException(502, 'Python Parser에 연결할 수 없습니다.')
    if res.status_code!=200:
        try: detail=res.json().get('detail','Excel 파일 분석 실패')
        except Exception: detail='Excel 파일 분석 실패'
        raise HTTPException(422, detail)
    try:
        parsed=res.json()
        parsed['source']['file_name']=filename
    except (ValueError,KeyError,TypeError):
        raise HTTPException(502, 'Parser 응답의 JSON 형식이 올바르지 않습니다.')
    if (parsed.get('summary') or {}).get('error_count',1)>0:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=422,content={'message':'검증 오류가 있어 저장하지 않았습니다. 원본 Excel을 수정한 뒤 다시 업로드하세요.','result':parsed})
    with connect() as cn:
        return save_draft(cn,key,filename,parsed)


@router.get('/profile-imports/{import_id}')
def read_import(import_id:str):
    with connect() as cn:
        return get_import(cn,import_id)


@router.get('/employees/{employee_key}')
def read_employee(employee_key:str):
    with connect() as cn:
        row=cn.execute('''SELECT p.import_id FROM profile_import p JOIN employee e ON e.employee_id=p.employee_id AND e.tenant_id=p.tenant_id
          WHERE p.tenant_id=%s AND e.employee_key=%s ORDER BY p.created_at DESC,p.import_id DESC LIMIT 1''',(TENANT,employee_key)).fetchone()
        if not row: raise HTTPException(404,'등록된 직원 프로파일이 없습니다.')
        return get_import(cn,row['import_id'])


@router.get('/employees')
def employees():
    with connect() as cn:
        rows=cn.execute('''SELECT employee_key AS "employeeKey",full_name AS name,position_title AS "positionTitle",engineer_grade AS "engineerGrade",profile_version AS "profileVersion" FROM employee
          WHERE tenant_id=%s ORDER BY updated_at DESC LIMIT 100''',(TENANT,)).fetchall()
        return rows
