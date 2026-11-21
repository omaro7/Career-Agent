import { useEffect, useState, type ChangeEvent } from 'react';
import './career-documents.css';

export type CareerProject = {
  project_name: string | null; customer: string | null;
  start_date: string | null; end_date: string | null; role: string | null;
  responsibilities: string[]; technologies: string[]; source_evidence: string | null;
};
export type CareerExtraction = { person_name: string | null; projects: CareerProject[]; notes: string[] };
export type CareerDocument = {
  id: string; filename: string; status: 'needs_review' | 'approved';
  version: number; extraction: CareerExtraction; blocks: Array<{ kind: string; text: string }>;
  created_at: string; updated_at: string;
};
export type CareerDocumentItem = Pick<CareerDocument, 'id' | 'filename' | 'status' | 'version' | 'created_at'>;
const BASE = '/api/v1/career-documents';
const emptyProject = (): CareerProject => ({
  project_name: null, customer: null, start_date: null, end_date: null,
  role: null, responsibilities: [], technologies: [], source_evidence: null,
});
const strToList = (value: string) => value.split('\n').map(s => s.trim()).filter(Boolean);

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE}${path}`, { credentials: 'include', ...init });
  if (!response.ok) {
    let message = `요청 실패 (HTTP ${response.status})`;
    try { const body = await response.json(); message = typeof body.detail === 'string' ? body.detail : message; } catch { /* no JSON */ }
    throw new Error(response.status === 409 ? '다른 변경 사항이 있습니다. 다시 조회한 후 수정하세요.' : message);
  }
  return response.json() as Promise<T>;
}

export default function CareerDocumentsPage() {
  const [items, setItems] = useState<CareerDocumentItem[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [doc, setDoc] = useState<CareerDocument | null>(null);
  const [draft, setDraft] = useState<CareerExtraction | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const locked = !doc || doc.status === 'approved' || busy;

  async function refresh() {
    try { setItems(await api<CareerDocumentItem[]>('')); }
    catch (e) { setError((e as Error).message); }
  }
  useEffect(() => { void refresh(); }, []);

  function selectDocument(next: CareerDocument) {
    setDoc(next); setDraft(structuredClone(next.extraction)); setError(''); setMessage('');
  }
  async function openDocument(id: string) {
    setBusy(true); setError('');
    try { selectDocument(await api<CareerDocument>(`/${id}`)); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function upload() {
    if (!file || busy) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const form = new FormData(); form.append('file', file);
      const result = await api<CareerDocument>('/upload', { method: 'POST', body: form });
      selectDocument(result); setFile(null); setMessage('Word 분석과 저장이 완료되었습니다. 검토 후 확정하세요.');
      await refresh();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function save() {
    if (!doc || !draft || locked) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const result = await api<CareerDocument>(`/${doc.id}/review`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version: doc.version, extraction: draft }),
      });
      selectDocument(result); setMessage('수정사항이 저장되었습니다.'); await refresh();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function approve() {
    if (!doc || !draft || locked) return;
    if (!window.confirm('마지막 저장본을 검토 완료로 확정하시겠습니까? 확정 후 수정할 수 없습니다.')) return;
    setBusy(true); setError(''); setMessage('');
    try {
      // Save current unsaved edits before approval, preventing accidental loss of changes.
      const saved = await api<CareerDocument>(`/${doc.id}/review`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version: doc.version, extraction: draft }),
      });
      const result = await api<CareerDocument>(`/${doc.id}/approve`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version: saved.version }),
      });
      selectDocument(result); setMessage('검토 완료로 확정했습니다.'); await refresh();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  function updateProject(index: number, patch: Partial<CareerProject>) {
    setDraft(prev => prev && ({ ...prev, projects: prev.projects.map((p, i) => i === index ? { ...p, ...patch } : p) }));
  }
  function addProject() { setDraft(prev => prev && ({ ...prev, projects: [...prev.projects, emptyProject()] })); }
  function removeProject(index: number) {
    setDraft(prev => prev && ({ ...prev, projects: prev.projects.filter((_, i) => i !== index) }));
  }
  const projectFields: Array<[keyof Pick<CareerProject, 'project_name'|'customer'|'start_date'|'end_date'|'role'>, string]> = [
    ['project_name', '프로젝트명'], ['customer', '고객사'], ['start_date', '시작일'],
    ['end_date', '종료일'], ['role', '역할'],
  ];
  return (
    <main className="career-documents">
      <header><h1>경력기술서 관리</h1><p>Word 경력기술서를 분석하고 경력 정보를 검토·수정·확정합니다. 회사 프로파일 Excel 등록과 별개입니다.</p></header>
      <section className="career-card" aria-label="Word 등록">
        <h2>Word 경력기술서 등록</h2>
        <div className="career-actions">
          <input aria-label="Word 파일 선택" type="file" accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            onChange={(e: ChangeEvent<HTMLInputElement>) => setFile(e.target.files?.[0] ?? null)} />
          <button disabled={busy || !file || !file.name.toLowerCase().endsWith('.docx') || file.size > 10 * 1024 * 1024} onClick={() => void upload()}>
            {busy ? '처리 중...' : '분석 및 등록'}
          </button>
        </div><small>DOCX만 지원 · 최대 10MB · 분석 후 검토 필요</small>
      </section>
      {error && <p className="career-error" role="alert">{error}</p>}
      {message && <p className="career-success" role="status">{message}</p>}
      <section className="career-card"><h2>등록된 경력기술서</h2>
        {items.length === 0 ? <p>등록된 문서가 없습니다.</p> : (
          <div className="career-list">{items.map(item => <button className={doc?.id === item.id ? 'active' : ''}
            key={item.id} onClick={() => void openDocument(item.id)} disabled={busy}>
            {item.filename} <span>{item.status === 'approved' ? '검토 완료' : '검토 필요'}</span>
          </button>)}</div>
        )}
      </section>
      {doc && draft && <section className="career-card" aria-label="분석 결과 검토">
        <div className="career-review-heading"><div><h2>분석 결과 검토</h2><p>{doc.filename} · 버전 {doc.version} · {doc.status === 'approved' ? '검토 완료' : '검토 필요'}</p></div>
          <div className="career-actions"><button disabled={locked} onClick={() => void save()}>수정 저장</button>
            <button disabled={locked} onClick={() => void approve()}>검토 완료</button></div></div>
        <label>이름<input disabled={locked} value={draft.person_name ?? ''}
          onChange={e => setDraft({ ...draft, person_name: e.target.value || null })} /></label>
        {draft.projects.map((p, index) => <fieldset key={`${doc.id}-${index}`} disabled={locked}>
          <legend>프로젝트 {index + 1}</legend>
          <div className="career-fields">{projectFields.map(([key, label]) => <label key={key}>{label}
            <input value={p[key] ?? ''} onChange={e => updateProject(index, { [key]: e.target.value || null })} /></label>)}</div>
          <label>담당 업무 (한 줄에 하나씩)<textarea rows={3} value={p.responsibilities.join('\n')}
            onChange={e => updateProject(index, { responsibilities: strToList(e.target.value) })} /></label>
          <label>기술 스택 (한 줄에 하나씩)<textarea rows={2} value={p.technologies.join('\n')}
            onChange={e => updateProject(index, { technologies: strToList(e.target.value) })} /></label>
          <label>원문 근거 (참고용)<textarea rows={2} readOnly value={p.source_evidence ?? ''} /></label>
          <button type="button" disabled={locked} onClick={() => removeProject(index)}>프로젝트 삭제</button>
        </fieldset>)}
        <button type="button" disabled={locked} onClick={addProject}>+ 프로젝트 추가</button>
        <label>추가 메모 (한 줄에 하나씩)<textarea rows={3} disabled={locked} value={draft.notes.join('\n')}
          onChange={e => setDraft({ ...draft, notes: strToList(e.target.value) })} /></label>
        <details><summary>Word 원문 추출 내용</summary><div className="career-original">{doc.blocks.map((b, i) => <p key={i}>{b.text}</p>)}</div></details>
      </section>}
    </main>
  );
}
