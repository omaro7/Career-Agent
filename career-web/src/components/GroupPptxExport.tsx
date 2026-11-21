import React, { useEffect, useState } from 'react';
import './GroupPptxExport.css';

type Employee = {
  employeeKey: string;
  name: string;
  positionTitle?: string;
  engineerGrade?: string;
  source?: string;
  sourceFileName?: string;
};
type Template = { templateId: string; name: string; customerName?: string; status?: string };
type StaffRow = {
  id: string;
  employeeKey: string;
  affiliation: string;
  displayGrade: string;
  businessRole: string;
  assignmentPeriod: string;
  assignmentMonths: string;
  allocationRate: string;
};
type Job = {
  jobId: string; status: 'QUEUED' | 'RUNNING' | 'COMPLETE' | 'FAILED';
  totalRows: number; doneRows: number; currentRow?: number; currentEmployee?: string;
  slides?: number; error?: string | null; downloadUrl?: string | null;
};
type Props = { employees?: Employee[] };
const API = '/api/v1/career/group-pptx';
// 한국시간 기준 날짜 (클라이언트 OS 타임존에 의존하지 않음).
const seoulToday = (): string => {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit'
  }).formatToParts(new Date());
  const part = (key: string) => parts.find(p => p.type === key)?.value || '';
  return `${part('year')}-${part('month')}-${part('day')}`;
};
const newRow = (): StaffRow => ({
  id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
  employeeKey: '', affiliation: '', displayGrade: '', businessRole: '',
  assignmentPeriod: '', assignmentMonths: '', allocationRate: '100%'
});

async function readJson(response: Response): Promise<any> {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload?.detail;
    const message = typeof detail === 'string' ? detail : Array.isArray(detail)
      ? detail.map((x: any) => x.msg).join(', ') : `HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

export default function GroupPptxExport({ employees = [] }: Props) {
  const [staff, setStaff] = useState<Employee[]>(employees);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [optionsBusy, setOptionsBusy] = useState(false);
  const [projectName, setProjectName] = useState('');
  const [templateId, setTemplateId] = useState('');
  const [projectPeriod, setProjectPeriod] = useState('');
  const [periodFormat, setPeriodFormat] = useState<'YYYY_MM' | 'YY_MM'>('YYYY_MM');
  const [pageRange, setPageRange] = useState('ALL');
  const [asOfDate, setAsOfDate] = useState(seoulToday);
  const [rows, setRows] = useState<StaffRow[]>([newRow()]);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');

  useEffect(() => { if (employees.length) setStaff(employees); }, [employees]);
  async function loadOptions() {
    setOptionsBusy(true);
    try {
      const x = await readJson(await fetch(`${API}/options`, {cache:'no-store'}));
      if (Array.isArray(x.employees)) setStaff(x.employees);
      const valid: Template[] = Array.isArray(x.templates)
        ? x.templates.filter((item: any) => typeof item.templateId === 'string' && item.status === 'READY') : [];
      setTemplates(valid);
      setTemplateId(previous => valid.some(item => item.templateId === previous) ? previous : '');
      setInfo(valid.length ? `등록된 READY 고객사 템플릿 ${valid.length}건을 불러왔습니다.`
        : 'READY·활성 고객사 템플릿이 조회되지 않습니다. 제안서 템플릿 관리 화면에서 상태를 확인하세요.');
    } catch (err) {
      setTemplates([]);
      setTemplateId('');
      setError(`고객사 템플릿 조회 실패: ${(err as Error).message}`);
    } finally { setOptionsBusy(false); }
  }
  useEffect(() => { void loadOptions(); }, []);

  useEffect(() => {
    if (!job || (job.status !== 'QUEUED' && job.status !== 'RUNNING')) return;
    let disposed = false;
    const check = async () => {
      try {
        const result: Job = await readJson(await fetch(`${API}/jobs/${job.jobId}`, {cache:'no-store'}));
        if (!disposed) { setJob(result); if (result.status === 'FAILED') setError(result.error || '그룹 PPTX 생성에 실패했습니다.'); }
      } catch (err) { if (!disposed) setInfo(`상태 확인 실패: ${(err as Error).message}. 다음 조회 때 재시도합니다.`); }
    };
    void check();
    const interval = window.setInterval(() => void check(), 1500);
    return () => { disposed = true; window.clearInterval(interval); };
  }, [job?.jobId, job?.status]);

  const update = (id: string, field: keyof StaffRow, value: string) => {
    setRows(prev => prev.map(row => row.id === id ? {...row, [field]: value} : row));
  };
  const choose = (id: string, employeeKey: string) => {
    const emp = staff.find(e => e.employeeKey === employeeKey);
    setRows(prev => prev.map(row => row.id === id ? {
      ...row, employeeKey, displayGrade: emp?.engineerGrade || row.displayGrade
    } : row));
  };
  const move = (index: number, step: number) => setRows(prev => {
    const target = index + step;
    if (target < 0 || target >= prev.length) return prev;
    const result = [...prev];
    [result[index], result[target]] = [result[target], result[index]];
    return result;
  });

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(''); setInfo('');
    if (!rows.length || rows.some(r => !r.employeeKey)) {
      setError('모든 Row의 직원을 선택하세요.'); return;
    }
    if (!templateId || !templates.some(t => t.templateId === templateId)) { setError('등록된 READY 고객사 템플릿을 선택하세요.'); return; }
    if (!['1', '1-2', 'ALL'].includes(pageRange)) {
      setError('경력사항 출력 범위는 1장, 2장 또는 전체 중에서 선택하세요.'); return;
    }
    if (!window.confirm(`${rows.length}명의 PPTX를 설정한 순서대로 생성하고 1개 파일로 합치시겠습니까?`)) return;
    setBusy(true); setJob(null);
    try {
      const res = await readJson(await fetch(`${API}/jobs`, {
        method: 'POST', headers: {'Content-Type': 'application/json; charset=utf-8'},
        body: JSON.stringify({
          common: {projectName, templateId: templateId.trim(), projectPeriod,
                   projectPeriodFormat: periodFormat, pageRange, asOfDate},
          rows: rows.map(({id, ...rest}) => rest)
        })
      }));
      setJob({jobId: res.jobId, status: 'QUEUED', doneRows: 0, totalRows: rows.length});
    } catch (err) {
      setError((err as Error).message);
    } finally { setBusy(false); }
  }

  const running = busy || job?.status === 'QUEUED' || job?.status === 'RUNNING';
  return <section className="group-pptx panel">
    <div className="group-pptx__heading">
      <div><div className="eyebrow">GROUP PPTX EXPORT</div><h2>그룹 PPTX 생성</h2>
        <p>공통 프로젝트 정보를 기준으로 직원별 PPTX를 Row 순서대로 생성하고, 1개 파일로 합칩니다.</p>
      </div>
      <span className="group-pptx__count">참여 직원 {rows.length}명</span>
    </div>
    <form onSubmit={e => void submit(e)}>
      <fieldset className="group-pptx__field" disabled={running}>
        <legend>공통 설정</legend>
        <div className="group-pptx__common">
          <label>프로젝트명 <input required maxLength={160} value={projectName} onChange={e => setProjectName(e.target.value)} placeholder="예: 차세대 금융시스템 구축"/></label>
          <label>고객사 템플릿
            <select
              className="group-pptx__template-select"
              required
              value={templateId}
              onChange={e => setTemplateId(e.target.value)}
              aria-label="고객사 템플릿 선택"
            >
              <option value="">{optionsBusy ? '템플릿 불러오는 중…' : templates.length ? '템플릿 선택' : '등록된 템플릿 없음'}</option>
              {templates.map(t => (
                <option key={t.templateId} value={t.templateId}>
                  {t.name} ({t.templateId})
                </option>
              ))}
            </select>
          </label>
          <label>프로젝트기간 <input required maxLength={100} value={projectPeriod} onChange={e => setProjectPeriod(e.target.value)} placeholder="2026.12 ~ 2027.12"/></label>
          <label>참여기간 표시형식
            <select value={periodFormat} onChange={e => setPeriodFormat(e.target.value as 'YYYY_MM'|'YY_MM')}>
              <option value="YYYY_MM">YYYY/MM (2026/12)</option>
              <option value="YY_MM">YY/MM (26/12)</option>
            </select>
          </label>
          <label>경력사항 출력 범위 (직원별)
            <select
              value={pageRange}
              onChange={e => setPageRange(e.target.value)}
              aria-label="경력사항 출력 범위"
            >
              <option value="1">1장 (첫 페이지)</option>
              <option value="1-2">2장 (첫 2페이지)</option>
              <option value="ALL">전체</option>
            </select>
            <small>직원별 생성된 PPTX에서 선택한 장수만 병합합니다. 그룹 공통 개요 1장은 별도입니다.</small>
          </label>
          <label>나이 계산 기준일
            <input type="date" required value={asOfDate}
              onChange={e => setAsOfDate(e.target.value)} aria-label="나이 계산 기준일" />
            <small>기본값: 오늘(한국시간). 생년월일이 있으면 기준일의 만 나이를 다시 계산합니다.</small>
          </label>
        </div>
      </fieldset>
      <div className="group-pptx__row-heading">
        <div><h3>참여 직원</h3><p>Row 순서대로 생성됩니다. 직원별 입력값은 저장된 프로파일에 덮어쓰지 않습니다.</p></div>
        <button type="button" className="secondary" disabled={running || rows.length >= 20} onClick={() => setRows(prev => [...prev, newRow()])}>+ Row 추가</button>
      </div>
      <div className="group-pptx__rows">
        {rows.map((row, index) => <fieldset className="group-pptx__row" key={row.id} disabled={running}>
          <legend>Row {index+1}</legend>
          <div className="group-pptx__fields">
            <label>직원선택 <select required value={row.employeeKey} onChange={e => choose(row.id,e.target.value)}>
              <option value="">선택하세요</option>
              {staff.map(e => <option key={e.employeeKey} value={e.employeeKey}>{e.name} · {e.source || (e.employeeKey.startsWith('word_') ? 'Word' : 'Excel')} · {e.sourceFileName || e.employeeKey}</option>)}
            </select></label>
            <label>소속 <input maxLength={120} value={row.affiliation} onChange={e => update(row.id,'affiliation',e.target.value)} placeholder="저장된 값 사용"/></label>
            <label>등급 <input maxLength={80} value={row.displayGrade} onChange={e => update(row.id,'displayGrade',e.target.value)} placeholder="기술 등급"/></label>
            <label>담당업무 <input maxLength={120} value={row.businessRole} onChange={e => update(row.id,'businessRole',e.target.value)} placeholder="예: PL / 설계"/></label>
            <label>본사업투입기간 <input maxLength={120} value={row.assignmentPeriod} onChange={e => update(row.id,'assignmentPeriod',e.target.value)} placeholder="2026.12 ~ 2027.12"/></label>
            <label>개월수 <input maxLength={10} inputMode="decimal" value={row.assignmentMonths} onChange={e => update(row.id,'assignmentMonths',e.target.value)} placeholder="예: 12 / 10.5"/></label>
            <label>투입율 <input maxLength={16} value={row.allocationRate} onChange={e => update(row.id,'allocationRate',e.target.value)} placeholder="100%"/></label>
          </div>
          <div className="group-pptx__actions">
            <button type="button" className="secondary" disabled={index===0} onClick={() => move(index,-1)} aria-label={`Row ${index+1} 위로 이동`}>↑ 위</button>
            <button type="button" className="secondary" disabled={index===rows.length-1} onClick={() => move(index,1)} aria-label={`Row ${index+1} 아래로 이동`}>↓ 아래</button>
            <button type="button" className="secondary group-pptx__delete" disabled={rows.length===1} onClick={() => setRows(prev => prev.filter(r => r.id!==row.id))}>삭제</button>
          </div>
        </fieldset>)}
      </div>
      <div className="group-pptx__submit">
        <button type="submit" className="primary" disabled={running || optionsBusy || !templateId || rows.some(r=>!r.employeeKey)}>{running?'순차 생성 중…':'전체 생성'}</button>
        <span>결과는 1개의 .pptx 파일입니다. 공통 개요 1장 뒤에 Row별 페이지가 이어집니다.</span>
      </div>
    </form>
    {info && <p className="group-pptx__note" role="status">{info}</p>}
    {error && <div className="group-pptx__error" role="alert">{error}</div>}
    {job && <div className="group-pptx__status" role="status" aria-live="polite">
      <div><strong>{job.status === 'COMPLETE' ? '전체 생성 완료' : job.status === 'FAILED' ? '생성 실패' : '직원별 PPTX 생성 중'}</strong>
        <span>{job.doneRows} / {job.totalRows}명 완료 {job.slides ? `· ${job.slides}장 처리` : ''}</span></div>
      <progress max={Math.max(1,job.totalRows)} value={job.doneRows}/>
      {job.currentEmployee && <p>현재 직원: {job.currentEmployee}</p>}
      {job.status === 'COMPLETE' && job.downloadUrl && <a className="group-pptx__download" href={job.downloadUrl}>그룹 PPTX 다운로드</a>}
      {job.status === 'FAILED' && <p className="group-pptx__error">{job.error}</p>}
    </div>}
  </section>;
}
