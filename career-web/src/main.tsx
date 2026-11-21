import React, { useCallback, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import PptxExport from './PptxExport';
import TemplateManagement from './TemplateManagement';
import WordCareerImport from './components/WordCareerImport';
import CareerAiChat from './components/CareerAiChat';
import GroupPptxExport from './components/GroupPptxExport';

import CareerDashboard from './components/CareerDashboard';
import './adminex.css';

type Check = 'UP' | 'DOWN' | 'UNKNOWN';
type Status = {
  stage?: string; api?: Check; mysql?: Check; pythonWorker?: Check;
  postgresql?: Check; postgres?: Check; db?: Check;
  agent?: Check; careerAgent?: Check; worker?: Check;
  checkedAt?: string;
};
type Tab = 'dashboard' | 'import' | 'word' | 'ai' | 'list' | 'export' | 'group' | 'templates' | 'system';
type Issue = { code: string; severity: 'ERROR' | 'WARNING' | 'INFO'; source_cell: string; message: string; project_source_row?: number | null };
type Education = { school_name: string; major?: string | null; level?: string | null; source_row: number };
type Certification = { certification_name: string; issuer?: string | null; obtained_date?: string | null };
type Project = { project_name: string; start_date?: string | null; end_date?: string | null; role_text?: string | null; technology_text?: string | null; client_name?: string | null; source_row: number; duplicate_suspected: boolean };
type Profile = { name: string; birth_date: string; position_title?: string | null; engineer_grade?: string | null; education: Education[]; certifications: Certification[]; projects: Project[] };
type Parsed = { profile: Profile; computed: { age_years?: number | null; as_of_date: string }; validation_issues: Issue[]; summary: { project_rows: number; error_count: number; warning_count: number } };
type ImportResult = { importId?: string; employeeKey?: string; status?: string; alreadyImported?: boolean; result: Parsed };
type Employee = { employeeKey: string; name: string; positionTitle?: string; engineerGrade?: string };

const dateToday = () => {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};
const month = (d?: string | null) => d ? d.slice(0, 7).replace('-', '.') : '미기재';
const dateDisplay = (d?: string | null) => d ? d.replaceAll('-', '.') : '미기재';
const value = (s?: string | null) => s && s.trim() ? s : '미기재';
const issuesSorted = (issues: Issue[]) => [...issues].sort((a,b) => Number(b.severity==='ERROR') - Number(a.severity==='ERROR'));

async function apiJson<T>(uri: string): Promise<T> {
  const response = await fetch(uri, { cache: 'no-store' });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

const FINAL_SCOPED_CSS = '/* Word 화면 전용 가로폭 확장. 다른 탭에는 적용되지 않습니다. */\n.ca-admin .ca-body > .ca-word-wide { display:block; flex: 0 0 auto; align-self: stretch; box-sizing: border-box; min-width: 0; width:100% !important; max-width:none !important; margin:0 !important; padding:0 !important; }\n.ca-admin .ca-body > .ca-word-wide > :is(div, section, main, article, form),\n.ca-admin .ca-body > .ca-word-wide > :is(div, section, main, article) > :is(div, section, main, article, form),\n.ca-admin .ca-body > .ca-word-wide > :is(div, section, main, article) > :is(div, section, main, article) > :is(div, section, main, article, form) { box-sizing:border-box; min-width:0; width:100% !important; max-width:none !important; margin-inline:0 !important; }\n.ca-admin .ca-word-wide :is(div, section, main, article, form)[style*="max-width"],\n.ca-admin .ca-word-wide :is(div, section, main, article, form)[style*="maxWidth"],\n.ca-admin .ca-word-wide :is(.panel, .card, .container, .upload-panel, .word-import, .word-import-card, .word-import-panel) { max-width:none !important; }\n.ca-admin .ca-ai-page { width:100%; min-width:0; max-width:none; }\n.ca-service-strip { display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin-left:auto; }\n.ca-service-label { font-size:11px; font-weight:650; color:#64748b; margin-right:4px; white-space:nowrap; }\n.ca-service-chip { white-space:nowrap; display:inline-flex; align-items:center; gap:5px; border:1px solid #e3ebf5; border-radius:999px; padding:5px 8px; font-size:11px; font-weight:650; color:#40516c; background:#fff; }\n.ca-status-up { color:#16a34a; }.ca-status-down { color:#dc2626; }.ca-status-unknown { color:#94a3b8; }\n.ca-refresh-btn { width:31px; height:31px; background:#fff; border:1px solid #d9e4f1; border-radius:8px; color:#2b65a5; cursor:pointer; }\n.ca-admin .employee-list .ca-employee-row { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:14px 18px; cursor:default; box-sizing:border-box; width:100%; }\n.ca-admin .ca-profile-summary { display:flex; flex-direction:column; gap:5px; min-width:0; }\n.ca-admin .ca-profile-summary small { font-size:12px; color:#64748b; }\n.ca-admin .ca-profile-actions { display:flex; align-items:center; gap:8px; flex-shrink:0; }\n.ca-admin .ca-profile-actions button { padding:8px 14px; border-radius:9px; font-size:13px; font-weight:650; cursor:pointer; white-space:nowrap; }\n.ca-admin .ca-profile-actions .ca-profile-open { color:#195ab4; background:#eff6ff; border:1px solid #bfdbfe; }\n.ca-admin .ca-profile-actions .ca-profile-delete { color:#b42318; background:#fff; border:1px solid #f3b6b3; }\n.ca-admin .ca-profile-actions .ca-profile-delete:hover:not(:disabled) { background:#fff1f0; }\n.ca-admin .ca-profile-actions button:disabled { opacity:0.45; cursor:not-allowed; }\n@media (max-width:680px) { .ca-admin .employee-list .ca-employee-row { align-items:flex-start; flex-direction:column; } }\n@media (max-width:1350px) { .ca-service-strip { display:none; } }\n';

function App() {
  const [tab, setTab] = useState<Tab>('dashboard');
  const [quickSearch, setQuickSearch] = useState('');
  const [system, setSystem] = useState<Status | null>(null);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [employeeKey, setEmployeeKey] = useState('LOCAL-001');
  const [asOfDate, setAsOfDate] = useState(dateToday());
  const [result, setResult] = useState<ImportResult | null>(null);
  const [errorResult, setErrorResult] = useState<Parsed | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [deletingEmployeeKey, setDeletingEmployeeKey] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [feedback, setFeedback] = useState('');

  const refreshStatus = useCallback(async () => {
    try { setSystem(await apiJson<Status>('/api/v1/system/status')); }
    catch { setSystem(null); }
  }, []);
  const refreshEmployees = useCallback(async () => {
    try { setEmployees(await apiJson<Employee[]>('/api/v1/career/employees')); }
    catch { /* Show server messages during actual actions. */ }
  }, []);
  useEffect(() => { void refreshStatus(); void refreshEmployees(); }, [refreshStatus, refreshEmployees]);

  async function upload(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!file) { setError('업로드할 Excel 파일을 선택하세요.'); return; }
    if (file.size > 10*1024*1024) { setError('파일 크기는 10MB 이하여야 합니다.'); return; }
    setBusy(true); setError(''); setFeedback(''); setErrorResult(null);
    const form = new FormData();
    form.append('file', file); form.append('employeeKey', employeeKey.trim()); form.append('asOfDate', asOfDate);
    try {
      const response = await fetch('/api/v1/career/profile-imports', { method: 'POST', body: form });
      const data = await response.json();
      if (!response.ok) {
        setError(data.message || data.detail || `업로드 오류 (HTTP ${response.status})`);
        if (data.result?.validation_issues) setErrorResult(data.result as Parsed);
        return;
      }
      setResult(data as ImportResult); setShowAll(false);
      setFeedback(data.alreadyImported ? '같은 파일이 이미 등록되어 기존 내역을 표시합니다.' : '7개 항목을 추출하고 PostgreSQL에 검토용 초안을 저장했습니다.');
      await refreshEmployees();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Excel 업로드 중 오류가 발생했습니다.');
    } finally { setBusy(false); }
  }

  async function loadEmployee(key: string) {
    setBusy(true); setError(''); setFeedback(''); setErrorResult(null);
    try {
      const data = await apiJson<ImportResult>(`/api/v1/career/employees/${encodeURIComponent(key)}`);
      setEmployeeKey(key); setResult(data); setTab('list'); setShowAll(false);
    } catch(e) { setError(e instanceof Error ? e.message : '프로파일 조회 실패'); }
    finally { setBusy(false); }
  }

  async function deleteEmployee(person: Employee) {
    if (busy || deletingEmployeeKey !== null) return;
    if (!window.confirm(`프로파일 "${person.name}" (${person.employeeKey})을(를) 삭제하시겠습니까?\n\n해당 프로파일의 프로젝트 경력·학력·자격증·연결된 Word/AI 데이터가 삭제됩니다.\n제안서 템플릿은 유지됩니다.`)) return;
    const typed = window.prompt(`삭제 확인: 직원 식별키를 그대로 입력하세요.\n${person.employeeKey}`);
    if (typed === null) return;
    if (typed !== person.employeeKey) {
      setError('직원 식별키가 일치하지 않아 삭제하지 않았습니다.');
      return;
    }

    setDeletingEmployeeKey(person.employeeKey);
    setError('');
    setFeedback('');
    try {
      const response = await fetch(`/api/v1/career/employees/${encodeURIComponent(person.employeeKey)}`, {
        method: 'DELETE',
        headers: { 'X-Career-Delete-Intent': 'delete-profile' },
        cache: 'no-store',
      });
      const payload: { detail?: unknown; warnings?: string[] } = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(typeof payload.detail === 'string' ? payload.detail : `프로파일 삭제 실패 (HTTP ${response.status})`);
      }
      setEmployees(previous => previous.filter(item => item.employeeKey !== person.employeeKey));
      if (result?.employeeKey === person.employeeKey) {
        setResult(null);
        setErrorResult(null);
      }
      const warnings = Array.isArray(payload.warnings) ? payload.warnings : [];
      setFeedback(`${person.name} (${person.employeeKey}) 프로파일 삭제 완료.` +
        (warnings.length ? ` 후속 확인: ${warnings.join(' / ')}` : ''));
      await refreshEmployees();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '프로파일 삭제 도중 오류가 발생했습니다.');
    } finally {
      setDeletingEmployeeKey(null);
    }
  }

  function downloadMarkdown() {
    if (!result) return;
    const p = result.result.profile;
    const lines = [
      `# ${p.name} 프로젝트 수행 경력`, '',
      '| 프로젝트명 | 참여기간 | 담당업무/역할 | 발주처 |',
      '|---|---|---|---|',
      ...p.projects.map(x => `| ${[x.project_name, `${month(x.start_date)} ~ ${month(x.end_date)}`, value(x.role_text), value(x.client_name)].map(v=>v.replaceAll('|', '\\|').replaceAll('\n', ' ')).join(' | ')} |`),
      '', '원본에서 중복 의심 프로젝트는 자동 삭제하지 않았습니다.',
    ];
    const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = `career-projects-${result.employeeKey || 'local'}.md`; link.click();
    URL.revokeObjectURL(url);
  }

  const parsed = result?.result;
  const filteredEmployees = employees.filter(person =>
    `${person.name} ${person.employeeKey} ${person.positionTitle ?? ''} ${person.engineerGrade ?? ''}`.toLocaleLowerCase().includes(quickSearch.trim().toLocaleLowerCase())
  );
  const counts = [
    {label:'API', state: system?.api ?? 'UNKNOWN'},
    {label:'PostgreSQL', state: system?.postgresql ?? system?.postgres ?? system?.db ?? system?.mysql ?? 'UNKNOWN'},
    {label:'Agent', state: system?.agent ?? system?.careerAgent ?? system?.worker ?? system?.pythonWorker ?? 'UNKNOWN'},
  ];
  const visibleProjects = showAll ? parsed?.profile.projects || [] : parsed?.profile.projects.slice(0, 8) || [];

  return <main className="shell ca-admin">
    <style>{FINAL_SCOPED_CSS}</style>
    <div className="ca-sidebar-logo" aria-label="Career Agent"><span className="ca-logo-mark">B</span><span className="ca-logo-name">Career Agent<small>CAREER MANAGEMENT</small></span></div>
    <header className="topbar">
      <div className="brandcopy"><h1>Career Agent v1.0</h1><p>직원 프로파일 관리 및 이력서 변환 시스템</p></div>
      <div className="ca-service-strip" aria-label="서비스 연결 상태">
        <span className="ca-service-label">서비스 상태</span>
        {counts.map(x => <span key={x.label} className="ca-service-chip" title={`${x.label}: ${x.state}`}>
          <span className={x.state === 'UP' ? 'ca-status-up' : x.state === 'DOWN' ? 'ca-status-down' : 'ca-status-unknown'}>●</span>
          {x.label} {x.state === 'UP' ? '정상' : x.state === 'DOWN' ? '오류' : '미확인'}
        </span>)}
        <button type="button" className="ca-refresh-btn" onClick={() => { void refreshStatus(); void refreshEmployees(); }} title="상태 새로고침" aria-label="상태 새로고침">↻</button>
      </div>
      <div className="ca-top-actions">
        <label className="ca-top-search" aria-label="프로파일 검색"><span aria-hidden="true">⌕</span><input type="search" placeholder="프로파일 검색..." value={quickSearch} onChange={e => setQuickSearch(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { setTab('list'); void refreshEmployees(); } }} /></label>
        <button type="button" className="ca-btn-primary" onClick={() => setTab('import')}>＋ 새 등록</button>
      </div>
    </header>

    <nav className="tabs" aria-label="기능 메뉴">
      <span className="ca-sidebar-heading">DASHBOARD</span>
      <button type="button" data-ca-tab="dashboard" className={tab==='dashboard'?'active':''} onClick={()=>setTab('dashboard')}>Overview</button>
      <span className="ca-sidebar-heading">PROFILES & TOOLS</span>
      <button type="button" data-ca-tab="import" className={tab==='import'?'active':''} onClick={()=>setTab('import')}>프로파일 등록</button>
      <button type="button" data-ca-tab="word" className={tab==='word'?'active':''} onClick={()=>setTab('word')}>비정형 Word 등록</button>
      <button type="button" data-ca-tab="ai" className={tab==='ai'?'active':''} onClick={()=>setTab('ai')}>AI 경력 챗봇</button>
      <button type="button" data-ca-tab="list" className={tab==='list'?'active':''} onClick={()=>{setTab('list'); void refreshEmployees();}}>저장된 프로파일</button>
      <button type="button" data-ca-tab="export" className={tab==='export'?'active':''} onClick={()=>{setTab('export'); void refreshEmployees();}}>PPTX 생성</button>
      <button type="button" data-ca-tab="group" className={tab==='group'?'active':''} onClick={()=>{setTab('group'); void refreshEmployees();}}>그룹 PPTX 생성</button>
      <button type="button" data-ca-tab="templates" className={tab==='templates'?'active':''} onClick={()=>setTab('templates')}>제안서 템플릿 관리</button>
      <button type="button" data-ca-tab="system" className={tab==='system'?'active':''} onClick={()=>setTab('system')}>서비스 상태</button>
    </nav>
    <div className="ca-body">
      {tab==='dashboard' && <CareerDashboard
        profileCount={employees.length}
        profiles={employees}
        availableTabs={['dashboard', 'import', 'word', 'ai', 'list', 'export', 'group', 'templates', 'system']}
        onNavigate={(target) => { setTab(target as Tab); if (target === 'list' || target === 'export' || target === 'group') void refreshEmployees(); }}
        onOpenProfile={(key) => { void loadEmployee(key); }}
      />}


    {tab==='import' && <section className="panel upload-panel">
      <div className="section-header"><div><div className="eyebrow">PROFILE IMPORT</div><h2>회사 기본 프로파일 등록</h2><p>기본 Excel에서 7개 항목과 기술자분류 체크박스를 자동 추출합니다.</p></div><span className="step">01 / 03</span></div>
      <form onSubmit={e=>void upload(e)}>
        <div className="formgrid">
          <label>직원 식별키 <span>성명 대신 사용할 고정 키</span><input required value={employeeKey} onChange={e=>setEmployeeKey(e.target.value)} pattern="[A-Za-z0-9_-]{1,80}" maxLength={80} placeholder="LOCAL-001" /></label>
          <label>만 나이 계산 기준일<input required type="date" value={asOfDate} onChange={e=>setAsOfDate(e.target.value)} /></label>
        </div>
        <label className="filebox"><span className="file-icon">XLSX</span><span><strong>{file ? file.name : 'Excel 프로파일 파일 선택'}</strong><small>회사 기본 Channel Unit 양식 · 최대 10 MB · .xlsx</small></span><input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={e=>setFile(e.target.files?.[0] || null)}/></label>
        <div className="actions"><button type="submit" className="primary" disabled={busy || !file}>{busy?'추출 및 저장 중…':'Excel 분석 및 PostgreSQL 저장'}</button><span className="muted">원본 Excel 파일 자체는 DB에 보관하지 않습니다.</span></div>
      </form>
    </section>}

    {tab === 'word' && <section className="ca-word-wide" aria-label="비정형 Word 등록" style={{width:'100%',maxWidth:'none',margin:0,padding:0,minWidth:0}}>
      <WordCareerImport />
    </section>}
    {tab === 'ai' && <section className="ca-ai-page" aria-label="AI 경력 챗봇"><CareerAiChat /></section>}

    {tab==='list' && <section className="panel">
      <div className="section-header"><div><div className="eyebrow">LOCAL DATABASE</div><h2>저장된 프로파일</h2><p>PostgreSQL에 등록된 프로파일을 조회합니다.</p></div><button className="secondary" onClick={()=>void refreshEmployees()}>목록 새로고침</button></div>
      {filteredEmployees.length===0 ? <div className="empty">등록된 프로파일이 없습니다. 먼저 Excel 또는 Word 파일을 등록하세요.</div> : <div className="employee-list">{filteredEmployees.map(person =>
        <div className="employee-item ca-employee-row" key={person.employeeKey}>
          <div className="ca-profile-summary"><strong>{person.name}</strong><small>{person.employeeKey} · {value(person.positionTitle)} · {value(person.engineerGrade)}</small></div>
          <div className="ca-profile-actions">
            <button type="button" className="ca-profile-open" disabled={busy || deletingEmployeeKey !== null} onClick={() => void loadEmployee(person.employeeKey)}>조회</button>
            <button type="button" className="ca-profile-delete" disabled={busy || deletingEmployeeKey !== null} onClick={() => void deleteEmployee(person)} aria-label={`${person.name} 프로파일 삭제`}>
              {deletingEmployeeKey === person.employeeKey ? '삭제 중…' : '삭제'}
            </button>
          </div>
        </div>
      )}</div>}
    </section>}

    {tab==='export' && <PptxExport employees={employees} />}
    {tab==='group' && <GroupPptxExport employees={employees} />}
    {tab==='templates' && <TemplateManagement />}

    {tab==='system' && <section className="panel">
      <div className="section-header"><div><div className="eyebrow">LOCAL INSTALLATION</div><h2>서비스 연결 상태</h2><p>API · PostgreSQL · Agent 연결 상태를 조회합니다.</p></div><button className="secondary" onClick={()=>void refreshStatus()}>상태 새로고침</button></div>
      <div className="cards">{counts.map(x=><article className="card" key={x.label}><p>{x.label}</p><strong className={x.state==='UP'?'up':'down'}>{x.state==='UP'?'● 정상':x.state==='DOWN'?'● 오류':'○ 미확인'}</strong></article>)}</div>
      <p className="muted">응답 API: /api/v1/system/status · 상태를 확인할 수 없는 항목은 미확인으로 표시합니다.</p>
    </section>}

    {error && <section className="alert alert-error" role="alert"><strong>처리 실패</strong><p>{error}</p>{errorResult && <IssueList issues={errorResult.validation_issues}/>}</section>}
    {feedback && <div className="alert alert-success" role="status">{feedback}</div>}

    {(tab === 'import' || tab === 'list') && parsed && <section className="results" aria-label="프로파일 결과">
      <div className="result-title"><div><div className="eyebrow">PARSING RESULT</div><h2>프로파일 추출 결과</h2><p>저장 상태: <strong>{result?.status}</strong> · 직원키: <strong>{result?.employeeKey}</strong> · 경력 {parsed.summary.project_rows}건</p></div><span className="result-state">검토용 초안</span></div>
      <div className="person-grid">
        <Data label="1. 이름" val={parsed.profile.name}/>
        <Data label="2. 생년월일 / 만 나이" val={`${dateDisplay(parsed.profile.birth_date)} / 만 ${parsed.computed.age_years ?? '—'}세`} sub={`기준일 ${parsed.computed.as_of_date}`}/>
        <Data label="3. 직급" val={value(parsed.profile.position_title)}/>
        <Data label="4. 기술자분류" val={value(parsed.profile.engineer_grade)} highlighted />
        <Data label="5. 대학교 / 전공" val={parsed.profile.education.map(x=>`${x.school_name} · ${value(x.major)}`).join(' / ') || '미기재'}/>
        <Data label="6. 자격증" val={parsed.profile.certifications.map(x=>x.certification_name).join(', ') || '미기재'}/>
        <Data label="7. 프로젝트 수행 경력" val={`${parsed.profile.projects.length}건 (중복 의심 포함)`}/>
      </div>
      <div className="project-header"><div><h3>프로젝트 수행 경력</h3><p>시작일 최신순 · 날짜 YYYY.MM · 4개 항목</p></div><button type="button" className="secondary" onClick={downloadMarkdown}>Markdown 다운로드</button></div>
      <div className="tablewrap"><table><thead><tr><th>프로젝트명</th><th>참여기간</th><th>담당업무/역할</th><th>발주처</th></tr></thead><tbody>
        {visibleProjects.map(x=><tr key={x.source_row}><td>{x.project_name}{x.duplicate_suspected && <span className="tag-warn">중복 의심</span>}</td><td className="dates">{month(x.start_date)} ~ {month(x.end_date)}</td><td>{value(x.role_text)}</td><td>{value(x.client_name)}</td></tr>)}
      </tbody></table></div>
      {parsed.profile.projects.length>8 && <button className="showmore" onClick={()=>setShowAll(!showAll)}>{showAll?'상위 8건만 보기':`전체 ${parsed.profile.projects.length}건 보기`}</button>}
      <div className="issue-header"><h3>검증 결과</h3><span>{parsed.summary.error_count}개 오류 · {parsed.summary.warning_count}개 경고</span></div>
      <IssueList issues={issuesSorted(parsed.validation_issues)}/>
      <p className="muted fine">기존 Excel 등록은 검토용 초안을 생성합니다. Stage 4 PPTX 생성에는 검토용 출력 확인이 필요합니다. 중복 의심 경력은 자동 삭제하지 않습니다.</p>
    </section>}
    <footer>Career Agent v1.0 · 경력정보 관리 · Excel / Word / PPTX / AI</footer>
      </div>
  </main>;
}
function Data({label,val,sub,highlighted=false}:{label:string,val:string,sub?:string,highlighted?:boolean}) {
  return <article className="datum"><p>{label}</p><strong className={highlighted?'highlighted':''}>{val}</strong>{sub && <small>{sub}</small>}</article>;
}
function IssueList({issues}:{issues:Issue[]}) {
  if(issues.length===0) return <p className="muted">추가 검증 경고가 없습니다.</p>;
  return <div className="issues">{issues.map((x,i)=><div className="issue" key={`${x.code}-${i}`}><span className={x.severity==='ERROR'?'severity error':'severity warning'}>{x.severity}</span><span><strong>{x.code}</strong> · {x.source_cell}<br/><small>{x.message}</small></span></div>)}</div>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);