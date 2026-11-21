"""Read-only PPTX options + export port; template mutations remain gated until parity tested."""
import json
import os
import re
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
import httpx
from fastapi import APIRouter, HTTPException, Request, Form
from fastapi.responses import Response
from .db import connect
from .profiles import TENANT, KEY_RE

router=APIRouter(prefix='/api/v1/career',tags=['pptx'])
PPTX_TYPE='application/vnd.openxmlformats-officedocument.presentationml.presentation'
VALID_STATUS=('PARSED','NEEDS_REVIEW','APPROVED','INDEX_PENDING','INDEXED')


def options_data(cn,key):
    if not KEY_RE.fullmatch(key): raise HTTPException(400,'직원키 형식이 올바르지 않습니다.')
    row=cn.execute('''SELECT e.employee_id,e.full_name,e.birth_date,e.position_title,e.engineer_grade,
       p.import_id,p.status,p.issue_count FROM employee e JOIN profile_import p
       ON p.employee_id=e.employee_id AND p.tenant_id=e.tenant_id
       WHERE e.tenant_id=%s AND e.employee_key=%s ORDER BY p.created_at DESC,p.import_id DESC LIMIT 1''',(TENANT,key)).fetchone()
    if not row: raise HTTPException(404,'등록된 직원 프로파일이 없습니다.')
    if row['status'] not in VALID_STATUS: raise HTTPException(409,'PPTX 출력이 허용되지 않는 프로파일 상태입니다.')
    eid,imp=row['employee_id'],row['import_id']
    edu=cn.execute('''SELECT school_name,major FROM employee_education WHERE tenant_id=%s AND employee_id=%s AND import_id=%s AND is_current=TRUE ORDER BY source_row''',(TENANT,eid,imp)).fetchall()
    certs=cn.execute('''SELECT certification_name FROM employee_certification WHERE tenant_id=%s AND employee_id=%s AND import_id=%s AND is_current=TRUE ORDER BY source_row''',(TENANT,eid,imp)).fetchall()
    projs=cn.execute('''SELECT project_id,project_name,start_date,end_date,source_duration_text,role_text,client_name,source_row,duplicate_suspected,approval_status FROM career_project
       WHERE tenant_id=%s AND employee_id=%s AND import_id=%s AND is_current=TRUE ORDER BY start_date DESC NULLS LAST,source_row ASC''',(TENANT,eid,imp)).fetchall()
    projects=[dict(projectId=p['project_id'].strip(),projectName=p['project_name'],startDate=str(p['start_date'] or ''),endDate=str(p['end_date'] or ''),periodText=p['source_duration_text'] or '',roleText=p['role_text'] or '',clientName=p['client_name'] or '',sourceRow=p['source_row'],duplicateSuspected=p['duplicate_suspected'],approvalStatus=p['approval_status']) for p in projs]
    education=(edu[0]['school_name'] or '') + (' / '+edu[0]['major'] if edu and edu[0]['major'] else '') if edu else ''
    return dict(employeeKey=key,importId=imp.strip(),status=row['status'],issueCount=row['issue_count'],name=row['full_name'],birthDate=str(row['birth_date'] or ''),positionTitle=row['position_title'] or '',engineerGrade=row['engineer_grade'] or '',educationText=education.strip(),certificationsText=', '.join(c['certification_name'] for c in certs),projects=projects,templateCode='CUSTOMER_PROFILE_V1',firstPageCapacity=9,continuationPageCapacity=15)


@router.get('/employees/{employee_key}/pptx/options')
def options(employee_key:str):
    with connect() as cn: return options_data(cn,employee_key)


def limit(value,label,maxlen,required=False):
    val=str(value or '').strip()
    if len(val)>maxlen or (required and not val): raise HTTPException(400,f'{label}은(는) 1~{maxlen}자여야 합니다.')
    return val


def rate_normalize(value):
    value=''.join(value.split())
    if not re.fullmatch(r'\d{1,3}(?:\.\d{1,2})?%?',value): raise HTTPException(400,'투입율 형식이 올바르지 않습니다.')
    num=value.rstrip('%')
    if Decimal(num)>100: raise HTTPException(400,'투입율은 100%를 초과할 수 없습니다.')
    return value if value.endswith('%') else value+'%'


async def export_data(key,request):
    with connect() as cn:
        info=options_data(cn,key)
        template_id=limit(request.get('templateId'),'고객사 템플릿',180) or 'customer-profile.pptx'
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,180}\.pptx',template_id,re.I) or template_id.startswith('.'):
            raise HTTPException(400,'템플릿 ID 형식이 올바르지 않습니다.')
        registered=cn.execute('''SELECT is_active FROM career_pptx_template_registry WHERE tenant_id=%s AND template_id=%s''',(TENANT,template_id)).fetchone()
        if registered and not registered['is_active']: raise HTTPException(409,'비활성화된 템플릿입니다.')
    confirm=request.get('confirmDraft') is True
    if info['status']!='APPROVED' and not confirm: raise HTTPException(409,"검토용 경력입니다. 내용 확인 후 '검토용 출력 동의'를 선택하세요.")
    customer=limit(request.get('customerName'),'고객사명',100,True)
    affiliation=limit(request.get('affiliation'),'소속',120,True)
    business_role=limit(request.get('businessRole'),'본 사업 담당업무',120,True)
    assignment=limit(request.get('assignmentPeriod'),'투입기간',120,True)
    months=limit(request.get('assignmentMonths'),'투입개월',8)
    if months:
        if not re.fullmatch(r'(?:0\.5|[1-9][0-9]{0,2}(?:\.[05])?)',months) or not (.5<=Decimal(months)<=600):
            raise HTTPException(400,'투입개월은 0.5~600 범위의 0.5 단위여야 합니다.')
        if '개월' in assignment: raise HTTPException(400,'투입기간에는 날짜만 입력하세요.')
        months=str(Decimal(months).normalize())
    rate=rate_normalize(limit(request.get('allocationRate'),'투입율',16,True))
    period=limit(request.get('projectPeriodFormat'),'참여기간 형식',12) or 'YYYY_MM'
    scope=limit(request.get('careerPageScope'),'경력 출력 범위',20) or 'ALL'
    if period not in ('YYYY_MM','YY_MM') or scope not in ('ALL','FIRST_PAGE'): raise HTTPException(400,'프로젝트 표시 형식 또는 출력 범위가 올바르지 않습니다.')
    try: asof=date.fromisoformat(request['asOfDate']) if request.get('asOfDate') else datetime.now(ZoneInfo('Asia/Seoul')).date()
    except (ValueError,TypeError): raise HTTPException(400,'기준일이 올바르지 않습니다.')
    if asof<date(1900,1,1) or asof>datetime.now(ZoneInfo('Asia/Seoul')).date()+timedelta(days=365*5+2): raise HTTPException(400,'기준일이 허용된 범위를 벗어났습니다.')
    try:
        birth=date.fromisoformat(info['birthDate'])
        age=asof.year-birth.year-((asof.month,asof.day)<(birth.month,birth.day))
    except ValueError: raise HTTPException(422,'생년월일이 올바르지 않습니다.')
    if age<0 or age>120: raise HTTPException(400,'기준일에 해당하는 연령이 올바르지 않습니다.')
    selections=request.get('selectedProjects') or []
    if not isinstance(selections,list) or not 1<=len(selections)<=100: raise HTTPException(400,'프로젝트는 1~100건 선택해야 합니다.')
    allowed={p['projectId']:p for p in info['projects']}
    used=set(); outgoing=[]
    for s in selections:
        if not isinstance(s,dict) or not s.get('projectId') or s['projectId'] in used: raise HTTPException(400,'중복되거나 비어 있는 프로젝트 ID가 있습니다.')
        pid=s['projectId'];used.add(pid);src=allowed.get(pid)
        if not src or src['approvalStatus']=='EXCLUDED': raise HTTPException(400,'선택할 수 없는 프로젝트가 포함되어 있습니다.')
        if src['approvalStatus']!='APPROVED' and not confirm: raise HTTPException(409,'미승인 프로젝트는 검토용 출력 동의가 필요합니다.')
        outgoing.append({**src,'submittedRole':limit(s.get('submittedRole'),'고객사 제출 담당업무',250) or src['roleText'],'workDescription':limit(s.get('workDescription'),'수행업무',300)})
    payload=dict(name=info['name'],affiliation=affiliation,positionTitle=info['positionTitle'],ageYears=age,
                 educationText=info['educationText'],degree=limit(request.get('degree'),'학위',80),
                 certificationsText=info['certificationsText'],displayGrade=limit(request.get('displayGrade'),'제출 등급',80) or info['engineerGrade'],
                 businessRole=business_role,assignmentPeriod=assignment,assignmentMonths=months,allocationRate=rate,
                 projectPeriodFormat=period,careerPageScope=scope,projects=outgoing,templateId=template_id,customerName=customer)
    agent=os.getenv('AGENT_BASE_URL','http://career-agent:8001').rstrip('/')
    try:
        async with httpx.AsyncClient(timeout=155) as cli:
            r=await cli.post(agent+'/render-customer-pptx',json=payload)
            if r.status_code!=200 or not r.content or len(r.content)>65_000_000: raise HTTPException(502,'Python PPTX 생성 실패')
    except httpx.RequestError: raise HTTPException(502,'PPTX Agent 연결 실패')
    return Response(content=r.content,media_type=PPTX_TYPE,headers={'Content-Disposition':f'attachment; filename=career-profile-{key}.pptx','Cache-Control':'no-store'})


@router.post('/employees/{employee_key}/pptx')
async def export_json(employee_key:str,request:dict):
    return await export_data(employee_key,request)


@router.post('/employees/{employee_key}/pptx/download')
async def export_form(employee_key:str,req:Request,payload:str=Form(...)):
    if req.headers.get('sec-fetch-site','none') not in ('same-origin','none'): raise HTTPException(403,'다른 사이트에서 PPTX 생성을 요청할 수 없습니다.')
    if len(payload)>120_000: raise HTTPException(400,'PPTX 요청 데이터가 너무 큽니다.')
    try: data=json.loads(payload)
    except ValueError: raise HTTPException(400,'PPTX 요청 JSON이 올바르지 않습니다.')
    if not isinstance(data,dict): raise HTTPException(400,'PPTX 요청 JSON이 올바르지 않습니다.')
    return await export_data(employee_key,data)
