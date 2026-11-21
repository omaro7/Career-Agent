import React, { useCallback, useEffect, useMemo, useState } from 'react';

type Employee = { employeeKey: string; name: string; positionTitle?: string; engineerGrade?: string };
type CareerRow = {
  projectId: string; projectName: string; startDate: string; endDate: string;
  roleText: string; clientName: string; sourceRow: number;
  duplicateSuspected: boolean; approvalStatus: string;
};
type Options = {
  employeeKey: string; importId: string; status: string; issueCount: number;
  name: string; birthDate: string; positionTitle: string; engineerGrade: string;
  educationText: string; certificationsText: string; projects: CareerRow[];
  firstPageCapacity: number; continuationPageCapacity: number;
};
type RowEdit = { submittedRole: string; workDescription: string };
type ProjectPeriodFormat = 'YYYY_MM' | 'YY_MM';
type CareerPageScope = 'ALL' | 'FIRST_PAGE';
type CustomerTemplate = { templateId: string; displayName: string; fileName: string; status: 'READY' | 'NEEDS_MAPPING' | 'INVALID' | 'DISABLED'; message: string; layoutMode: 'AUTO' | 'MAPPED' | null; firstPageCapacity: number; continuationPageCapacity: number }; 
type TemplateResponse = { templates: CustomerTemplate[]; defaultTemplateId: string | null };

const nowLocal = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
};
const formatProjectMonth = (value: string, format: ProjectPeriodFormat) => {
  const match = /^(\d{4})[-./](\d{1,2})(?:[-./]\d{1,2})?$/.exec((value || '').trim());
  if (!match) return value || '미기재';
  const year = format === 'YY_MM' ? match[1].slice(-2) : match[1];
  return `${year}.${match[2].padStart(2, '0')}`;
};

// Normalize a numeric allocation rate once, without adding a duplicate '%' sign.
// Validation of the final submitted value is repeated on the API/Worker side.
const normalizeAllocationRate = (value: string): string => {
  const compact = value.trim().replace(/\s+/g, '');
  if (!/^\d{1,3}(?:\.\d{1,2})?%?$/.test(compact)) return compact;
  const number = Number(compact.replace(/%$/, ''));
  if (!Number.isFinite(number) || number < 0 || number > 100) return compact;
  return compact.endsWith('%') ? compact : `${compact}%`;
};

export default function PptxExport({ employees }: { employees: Employee[] }) {
  const [employeeKey, setEmployeeKey] = useState('');
  const [options, setOptions] = useState<Options | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [edits, setEdits] = useState<Record<string, RowEdit>>({});
  const [templates, setTemplates] = useState<CustomerTemplate[]>([]);
  const [templateId, setTemplateId] = useState('');
  const [templateLoading, setTemplateLoading] = useState(true);
  const [templateRefreshKey, setTemplateRefreshKey] = useState(0);
  const [templateError, setTemplateError] = useState('');
  const [customerName, setCustomerName] = useState('');
  const [affiliation, setAffiliation] = useState('');
  const [degree, setDegree] = useState('');
  const [displayGrade, setDisplayGrade] = useState('');
  const [businessRole, setBusinessRole] = useState('');
  const [assignmentPeriod, setAssignmentPeriod] = useState('');
  const [assignmentMonths, setAssignmentMonths] = useState('');
  const [projectPeriodFormat, setProjectPeriodFormat] = useState<ProjectPeriodFormat>('YYYY_MM');
  const [careerPageScope, setCareerPageScope] = useState<CareerPageScope>('ALL');
  const [allocationRate, setAllocationRate] = useState('');
  const [asOfDate, setAsOfDate] = useState(nowLocal());
  const [confirmDraft, setConfirmDraft] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  useEffect(() => {
    if (employees.length && !employees.some(e => e.employeeKey === employeeKey)) {
      setEmployeeKey(employees[0].employeeKey);
    }
  }, [employees, employeeKey]);

  useEffect(() => {
    let active = true;
    async function loadTemplates() {
      setTemplateLoading(true);
      try {
        const response = await fetch('/api/v1/career/pptx/templates', {cache:'no-store'});
        if (!response.ok) throw new Error(`템플릿 목록 조회 실패 (HTTP ${response.status})`);
        const data = await response.json() as TemplateResponse;
        if (!Array.isArray(data.templates)) throw new Error('템플릿 목록 응답 형식 오류');
        if (!active) return;
        setTemplates(data.templates);
        setTemplateId(prev => data.templates.some(t => t.templateId === prev && t.status === 'READY')
          ? prev : (data.defaultTemplateId || data.templates.find(t => t.status === 'READY')?.templateId || ''));
        setTemplateError('');
      } catch (e) {
        if (active) setTemplateError(e instanceof Error ? e.message : '템플릿 목록 조회 오류');
      } finally {
        if (active) setTemplateLoading(false);
      }
    }
    void loadTemplates();
    return () => { active = false; };
  }, [templateRefreshKey]);

  const loadOptions = useCallback(async (key: string) => {
    if (!key) return;
    setBusy(true); setOptions(null); setError(''); setMessage(''); setConfirmDraft(false);
    try {
      const response = await fetch(`/api/v1/career/employees/${encodeURIComponent(key)}/pptx/options`, {cache:'no-store'});
      if (!response.ok) throw new Error(`프로파일 조회 실패 (HTTP ${response.status})`);
      const data = await response.json() as Options;
      setOptions(data);
      // Do not automatically include suspected duplicates or excluded projects.
      setSelectedIds(data.projects.filter(x=> !x.duplicateSuspected && x.approvalStatus !== 'EXCLUDED').map(x=>x.projectId));
      setEdits({});
      setDisplayGrade(data.engineerGrade || '');
      setDegree('');
    } catch (e) { setError(e instanceof Error ? e.message : '프로파일 조회 실패'); }
    finally { setBusy(false); }
  }, []);

  useEffect(() => { void loadOptions(employeeKey); }, [employeeKey, loadOptions]);

  const byId = useMemo(() => new Map<string, CareerRow>(options?.projects.map(x => [x.projectId, x] as [string, CareerRow]) || []), [options]);
  const selectedRows = selectedIds.map(id=>byId.get(id)).filter((x): x is CareerRow=>Boolean(x));
  const selectedTemplate = templates.find(t => t.templateId === templateId && t.status === 'READY');
  const firstCapacity = selectedTemplate?.firstPageCapacity ?? 9;
  const nextCapacity = selectedTemplate?.continuationPageCapacity ?? 15;
  const printedProjectCount = careerPageScope === 'FIRST_PAGE' ? Math.min(selectedRows.length, firstCapacity) : selectedRows.length;
  const omittedProjectCount = selectedRows.length - printedProjectCount;
  const pageCount = careerPageScope === 'FIRST_PAGE' || selectedRows.length <= firstCapacity
    ? 1 : 1 + Math.ceil((selectedRows.length - firstCapacity) / nextCapacity);

  function toggle(id: string, checked: boolean) {
    setSelectedIds(v => checked ? [...v, id] : v.filter(x => x !== id));
    setConfirmDraft(false);
  }
  function move(id: string, dir: -1 | 1) {
    setSelectedIds(v => {
      const copy = [...v]; const i = copy.indexOf(id); const j = i + dir;
      if (i < 0 || j < 0 || j >= copy.length) return v;
      [copy[i],copy[j]] = [copy[j],copy[i]];
      return copy;
    });
  }
  function updateEdit(id: string, field: keyof RowEdit, value: string) {
    setEdits(prev => ({...prev, [id]: {submittedRole: prev[id]?.submittedRole ?? '', workDescription: prev[id]?.workDescription ?? '', [field]: value}}));
    setConfirmDraft(false);
  }

  function download(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!options || !selectedRows.length) { setError('출력할 프로젝트를 선택하세요.'); return; }
    if (!templateId || !templates.some(t => t.templateId === templateId && t.status === 'READY')) {
      setError('고객사 템플릿을 먼저 선택하세요.'); return;
    }
    if (options.status !== 'APPROVED' && !confirmDraft) {
      setError('검토용 경력의 내용 확인에 동의해야 생성할 수 있습니다.'); return;
    }

    const normalizedRate = normalizeAllocationRate(allocationRate);
    if (!/^\d{1,3}(?:\.\d{1,2})?%$/.test(normalizedRate) ||
        Number(normalizedRate.slice(0, -1)) > 100) {
      setError('투입율은 0~100 범위의 숫자 또는 % 값으로 입력하세요. (예: 70, 70%, 70.5%)');
      return;
    }
    setAllocationRate(normalizedRate);

    // Native browser download: avoid showSaveFilePicker and fetch()+Blob URL.
    // The submit is synchronous in the user's click event, so Chrome handles
    // Content-Disposition: attachment itself (including the normal download UI).
    const payload = {
      templateId, customerName, affiliation, degree, displayGrade, businessRole, assignmentPeriod, assignmentMonths, allocationRate: normalizedRate, asOfDate,
      projectPeriodFormat, careerPageScope,
      confirmDraft,
      selectedProjects: selectedRows.map(row => ({
        projectId: row.projectId,
        submittedRole: edits[row.projectId]?.submittedRole ?? '',
        workDescription: edits[row.projectId]?.workDescription ?? '',
      })),
    };
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = `/api/v1/career/employees/${encodeURIComponent(options.employeeKey)}/pptx/download`;
    form.enctype = 'application/x-www-form-urlencoded';
    form.target = '_self';
    form.style.display = 'none';

    const field = document.createElement('input');
    field.type = 'hidden';
    field.name = 'payload';
    field.value = JSON.stringify(payload);
    form.appendChild(field);
    document.body.appendChild(form);
    setError('');
    setMessage('PPTX 생성 요청을 전송했습니다. 생성 후 Chrome의 다운로드 목록(Ctrl+J)을 확인하세요. 저장 완료 여부는 브라우저가 관리합니다.');
    try {
      // Do not fetch binary data into JavaScript; browser saves the attachment.
      HTMLFormElement.prototype.submit.call(form);
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      setMessage('');
      setError(`브라우저 다운로드 요청 실패: ${detail}`);
    } finally {
      // Let the browser finish serializing the form POST before removing it.
      window.setTimeout(() => form.remove(), 1000);
    }
  }

  return <section className="panel pptx-panel">
    <div className="section-header"><div><div className="eyebrow">STAGE 4 · PPTX EXPORT</div><h2>고객사별 경력기술서 생성</h2>
      <p>MySQL에 등록된 프로파일을 사용합니다. Excel 재업로드 및 PowerShell 실행은 필요하지 않습니다.</p>
    </div><span className="step">PPTX</span></div>
    {!employees.length ? <p className="empty">등록된 직원이 없습니다. 먼저 프로파일을 등록하세요.</p> : <>
      <div className="formgrid"><label>직원 선택
        <select value={employeeKey} onChange={e=>setEmployeeKey(e.target.value)} disabled={busy}>
          {employees.map(x=><option value={x.employeeKey} key={x.employeeKey}>{x.name} · {x.employeeKey}</option>)}
        </select></label>
        <label>고객사 템플릿
          <select required value={templateId} onChange={e => {setTemplateId(e.target.value); setError('');}}
                  disabled={templateLoading || !templates.some(t => t.status === 'READY')}>
            <option value="">{templateLoading ? '템플릿 목록 불러오는 중…' : '템플릿 선택'}</option>
            {templates.map(t => <option key={t.templateId} value={t.templateId} disabled={t.status !== 'READY'}>
              {t.displayName} ({t.fileName}){t.status === 'NEEDS_MAPPING' ? ' — 매핑 필요' : t.status === 'INVALID' ? ' — 파일/매핑 오류' : t.status === 'DISABLED' ? ' — 비활성화' : ''}
            </option>)}
          </select>
          <small className="template-help">등록된 모든 PPTX를 표시하며, 사용 가능 상태의 양식만 선택할 수 있습니다.</small>
          {selectedTemplate && <small className="template-help">선택 양식: {selectedTemplate.message} · 첫 페이지 {firstCapacity}건 / 이후 {nextCapacity}건</small>}
          {templates.some(t => t.status !== 'READY') && <small className="template-help">매핑 필요: {templates.filter(t => t.status !== 'READY').map(t => `${t.fileName} (${t.message})`).join(', ')}</small>}
        </label>

      </div>
      {templateError && <div className="alert alert-error" role="alert">
        <span>{templateError}</span>
        <div style={{ marginTop: 10 }}>
          <button type="button" className="secondary" onClick={() => setTemplateRefreshKey(v => v + 1)} disabled={templateLoading}>
            목록 다시 시도
          </button>
        </div>
      </div>}
      {!templateLoading && !templateError && !templates.some(t => t.status === 'READY') && <div className="alert alert-error">사용 가능한 템플릿이 없습니다. 고객사 양식에 맞는 mapping.json을 등록하세요.</div>}
      {options && <>
        <p className="pptx-summary">{options.name} · {options.positionTitle || '직급 미기재'} · 기술등급 {options.engineerGrade || '미기재'} · 원본 {options.projects.length}건 · <strong>{options.status}</strong></p>
        <form onSubmit={download}>
          <div className="formgrid">
            <label>고객사명<input required maxLength={100} value={customerName} onChange={e=>setCustomerName(e.target.value)} placeholder="예: 고객사 A (제출 관리용)" /></label>
            <label>소속<input required maxLength={120} value={affiliation} onChange={e=>setAffiliation(e.target.value)} placeholder="예: 뱅크웨어글로벌" /></label>
            <label>본 사업 담당업무<input required maxLength={120} value={businessRole} onChange={e=>setBusinessRole(e.target.value)} placeholder="예: Android 개발" /></label>
            <div className="assignment-period-fields" style={{display:'grid', gridTemplateColumns:'minmax(0, 2fr) minmax(104px, 1fr)', gap:12, alignItems:'start', minWidth:0}}>
              <label>본 사업 투입기간
                <input required maxLength={120} value={assignmentPeriod} onChange={e=>setAssignmentPeriod(e.target.value)} placeholder="2026.12 ~ 2027.12" />
              </label>
              <label>투입개월
                <input required type="number" inputMode="decimal" min="0.5" max="600" step="0.5" value={assignmentMonths}
                  onChange={e=>setAssignmentMonths(e.target.value)} placeholder="12" />
              </label>
            </div>
            <label>프로젝트 참여기간 표시 형식
              <select value={projectPeriodFormat} onChange={e=>setProjectPeriodFormat(e.target.value as ProjectPeriodFormat)}>
                <option value="YYYY_MM">YYYY.MM ~ YYYY.MM (2026.02 ~ 2026.04)</option>
                <option value="YY_MM">YY.MM ~ YY.MM (26.02 ~ 26.04)</option>
              </select>
              <small className="template-help">경력사항 표와 아래 프로젝트 기간 미리보기에만 적용됩니다. 원본 DB 날짜는 변경하지 않습니다.</small>
            </label>
            <label>경력사항 출력 범위
              <select value={careerPageScope} onChange={e => setCareerPageScope(e.target.value as CareerPageScope)}>
                <option value="ALL">전체 경력사항 출력 (필요 시 2페이지 이상)</option>
                <option value="FIRST_PAGE">경력사항 1장만 출력 (첫 페이지 최대 {firstCapacity}건)</option>
              </select>
              <small className="template-help">1장만 출력하면 선택 순서 상위 {firstCapacity}건만 포함하며, 인적사항이 있는 첫 페이지 하나만 생성합니다. 원본 경력은 삭제되지 않습니다.</small>
            </label>
            <label>투입율<input required maxLength={16} value={allocationRate}
              onChange={e=>setAllocationRate(e.target.value)}
              onBlur={()=>setAllocationRate(v=>normalizeAllocationRate(v))}
              placeholder="예: 70 또는 70%" />
              <small className="template-help">70 입력 시 70%로 자동 보정되며, 70%는 그대로 유지됩니다.</small>
            </label>
            <label>나이 계산 기준일<input required type="date" value={asOfDate} onChange={e=>setAsOfDate(e.target.value)} /></label>
            <label>학위 (원본에 없으면 직접 입력)<input maxLength={80} value={degree} onChange={e=>setDegree(e.target.value)} placeholder="예: 학사" /></label>
            <label>제출 기술등급<input maxLength={80} value={displayGrade} onChange={e=>setDisplayGrade(e.target.value)} /></label>
          </div>
          {displayGrade !== options.engineerGrade && <div className="alert alert-error">원본 기술등급과 제출 기술등급이 다릅니다. 해당 고객사 기준을 확인하세요.</div>}
          <div className="project-header pptx-project-header"><div><h3>출력 프로젝트 및 순서</h3>
            <p>중복 의심 항목은 기본 선택에서 제외됩니다. 필요하면 직접 선택하세요.</p></div>
            <span className="pptx-summary"><strong>{selectedRows.length}건 선택 · 실제 출력 {printedProjectCount}건 · 예상 {pageCount}페이지</strong>
              {careerPageScope === 'FIRST_PAGE' ? ` (첫 장 최대 ${firstCapacity}건)` : ` (첫 장 ${firstCapacity}건, 이후 ${nextCapacity}건)`}</span></div>
          {omittedProjectCount > 0 && <div className="pptx-summary" role="status" style={{marginBottom:14}}>
            1장만 출력 조건으로 선택된 경력 중 뒤쪽 {omittedProjectCount}건은 PPTX에 포함되지 않습니다. 출력할 경력을 위로 이동하여 순서를 조정하세요.
          </div>}
          <div className="pptx-project-list">
            {options.projects.map(p => {
              const checked = selectedIds.includes(p.projectId);
              const idx = selectedIds.indexOf(p.projectId);
              return <div className={`pptx-project-item ${checked?'chosen':''}`} key={p.projectId}>
                <div className="pptx-project-line"><label className="pptx-choice"><input type="checkbox" checked={checked} disabled={p.approvalStatus==='EXCLUDED'} onChange={e=>toggle(p.projectId,e.target.checked)} />
                  <span><strong>{p.projectName}</strong><small>{formatProjectMonth(p.startDate, projectPeriodFormat)} ~ {formatProjectMonth(p.endDate, projectPeriodFormat)} · {p.clientName || '발주처 미기재'} · {p.roleText || '역할 미기재'}</small></span></label>
                  {p.duplicateSuspected && <span className="tag-warn">중복 의심</span>}
                  {p.approvalStatus==='EXCLUDED' && <span className="tag-warn">제외됨</span>}
                  {checked && <div className="order-buttons"><button type="button" title="위로" disabled={idx===0} onClick={()=>move(p.projectId,-1)}>↑</button><button type="button" title="아래로" disabled={idx===selectedIds.length-1} onClick={()=>move(p.projectId,1)}>↓</button></div>}
                </div>
                {checked && <div className="pptx-edits">
                  <label>고객사 제출 담당업무<input maxLength={250} value={edits[p.projectId]?.submittedRole ?? ''} placeholder={p.roleText || '역할 입력'} onChange={e=>updateEdit(p.projectId,'submittedRole',e.target.value)} /></label>
                  <label>수행업무 (원본에는 별도 항목 없음)<input maxLength={300} value={edits[p.projectId]?.workDescription ?? ''} placeholder="검증된 수행업무만 입력 · 미입력 시 공란" onChange={e=>updateEdit(p.projectId,'workDescription',e.target.value)} /></label>
                </div>}
              </div>;
            })}
          </div>
          <label className="pptx-review"><input type="checkbox" checked={confirmDraft} onChange={e=>setConfirmDraft(e.target.checked)} />
            <span>프로젝트, 중복 의심, 나이·기술등급·고객사 제출 문구를 확인했습니다. <strong>검토용 출력</strong>이며 DB 최종 승인은 아닙니다.</span>
          </label>
          <div className="actions"><button className="primary" type="submit" disabled={busy || templateLoading || !selectedTemplate || !selectedRows.length || !confirmDraft}>{busy?'프로파일 조회 중…':'PPTX 생성 및 다운로드'}</button>
            <span className="muted">고객사 양식은 서버에서 읽기 전용으로 사용합니다.</span></div>
        </form>
      </>}
    </>}
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    {message && <div className="alert alert-success" role="status">{message}</div>}
  </section>;
}
