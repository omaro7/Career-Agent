import React, { useCallback, useEffect, useState } from 'react';

type TemplateInfo = {
  templateId: string;
  fileName: string;
  displayName: string;
  status: 'READY' | 'NEEDS_MAPPING' | 'INVALID' | 'DISABLED';
  message: string;
  layoutMode: string | null;
  firstPageCapacity: number;
  continuationPageCapacity: number;
  registered?: boolean;
  customerName?: string;
  originalFileName?: string;
  templateVersion?: number;
  isActive?: boolean;
};
type AutoMappingProposal = {
  status: 'CANDIDATE_READY' | 'REVIEW_REQUIRED';
  autoApplicable: boolean;
  confidence: number;
  reason: string;
  warnings: string[];
  mapping: Record<string, unknown> | null;
  detected?: { personalFields: string[]; careerFields: string[]; firstPageCapacity: number; continuationPageCapacity: number };
};
type TemplateList = { templates: TemplateInfo[]; defaultTemplateId: string | null };

async function getError(response: Response): Promise<string> {
  const raw = await response.text().catch(() => '');
  try {
    const body = JSON.parse(raw) as { detail?: string; message?: string; title?: string };
    return body.detail || body.message || body.title || `HTTP ${response.status}`;
  } catch { return `HTTP ${response.status}: ${raw.slice(0, 200)}`; }
}

export default function TemplateManagement() {
  const [items, setItems] = useState<TemplateInfo[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState('');
  const [customer, setCustomer] = useState('');
  const [mappingFile, setMappingFile] = useState<File | null>(null);
  const [selectedId, setSelectedId] = useState('');
  const [mappingJson, setMappingJson] = useState('');
  const [autoDraft, setAutoDraft] = useState<AutoMappingProposal | null>(null);
  const [analysis, setAnalysis] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [adoptCustomer, setAdoptCustomer] = useState('');
  const [adoptName, setAdoptName] = useState('');

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/v1/career/pptx/templates', { cache: 'no-store' });
      if (!response.ok) throw new Error(await getError(response));
      const data = await response.json() as TemplateList;
      if (!Array.isArray(data.templates)) throw new Error('템플릿 목록 응답 형식이 올바르지 않습니다.');
      setItems(data.templates);
      setSelectedId(prev => data.templates.some(t => t.templateId === prev) ? prev : (data.defaultTemplateId || data.templates[0]?.templateId || ''));
    } catch (exc) { setError(exc instanceof Error ? exc.message : '템플릿 조회 실패'); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void reload(); }, [reload]);
  const chosen = items.find(t => t.templateId === selectedId);

  async function upload(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!file || !name.trim() || !customer.trim()) return;
    if (file.size > 40 * 1024 * 1024) { setError('PPTX 파일은 최대 40MB입니다.'); return; }
    setBusy(true); setError(''); setMessage('');
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('displayName', name.trim());
      form.append('customerName', customer.trim());
      if (mappingFile) {
        if (mappingFile.size > 64_000) throw new Error('매핑 JSON은 64KB 이하여야 합니다.');
        form.append('mappingJson', await mappingFile.text());
      }
      const response = await fetch('/api/v1/career/pptx/templates', { method: 'POST', body: form });
      if (!response.ok) throw new Error(await getError(response));
      const data = await response.json() as TemplateInfo & { autoMappingApplied?: boolean };
      setFile(null); setMappingFile(null); setName(''); setCustomer('');
      setMessage(data.autoMappingApplied ? `자동 매핑 및 등록 완료: ${data.displayName} (READY). 생성 결과를 검토하세요.` : `등록 완료: ${data.displayName} (${data.status}). 자동 매핑을 실행하여 사용할 수 있습니다.`);
      await reload(); setSelectedId(data.templateId);
    } catch (exc) { setError(exc instanceof Error ? exc.message : '등록 실패'); }
    finally { setBusy(false); }
  }
  async function applyMapping(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault(); if (!chosen || !mappingJson.trim()) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const response = await fetch(`/api/v1/career/pptx/templates/${encodeURIComponent(chosen.templateId)}/mapping`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mappingJson })
      });
      if (!response.ok) throw new Error(await getError(response));
      setMessage('매핑 검증 및 저장 완료. 이제 PPTX 생성 화면에서 선택할 수 있습니다.');
      await reload();
    } catch (exc) { setError(exc instanceof Error ? exc.message : '매핑 등록 실패'); }
    finally { setBusy(false); }
  }
  async function adopt() {
    if (!chosen) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const response = await fetch(`/api/v1/career/pptx/templates/${encodeURIComponent(chosen.templateId)}/adopt`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ customerName: adoptCustomer.trim() || '기존 고객사', displayName: adoptName.trim() || chosen.displayName })
      });
      if (!response.ok) throw new Error(await getError(response));
      setMessage('기존 폴더 템플릿을 MySQL 관리 대상으로 등록했습니다.');
      await reload();
    } catch (exc) { setError(exc instanceof Error ? exc.message : 'DB 관리 등록 실패'); }
    finally { setBusy(false); }
  }
  async function toggleActive() {
    if (!chosen || !chosen.registered) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const response = await fetch(`/api/v1/career/pptx/templates/${encodeURIComponent(chosen.templateId)}/active`, {
        method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ active: chosen.status === 'DISABLED' })
      });
      if (!response.ok) throw new Error(await getError(response));
      setMessage('활성화 상태를 변경했습니다.'); await reload();
    } catch (exc) { setError(exc instanceof Error ? exc.message : '상태 변경 실패'); }
    finally { setBusy(false); }
  }
  async function removeUploadedTemplate() {
    if (!chosen || !chosen.registered || !/^tpl-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}\.pptx$/i.test(chosen.templateId)) return;
    const { templateId, displayName, originalFileName } = chosen;
    const confirmed = window.confirm(
      `등록된 템플릿 “${displayName}”을(를) 삭제하시겠습니까?\n` +
      `업로드 파일: ${originalFileName || chosen.fileName}\n\n` +
      '해당 등록 정보, 업로드된 PPTX와 매핑 JSON만 삭제됩니다. 기존 고객사 기본 파일과 다른 등록 템플릿은 유지됩니다.\n삭제 후 복구할 수 없습니다.'
    );
    if (!confirmed) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const response = await fetch(`/api/v1/career/pptx/templates/${encodeURIComponent(templateId)}`, {
        method: 'DELETE', headers: { 'X-Confirm-Template-Id': templateId }
      });
      if (!response.ok) throw new Error(await getError(response));
      const result = await response.json() as { deleted?: boolean; templateId?: string };
      if (!result.deleted || result.templateId !== templateId) throw new Error('삭제 응답을 확인할 수 없습니다. 목록을 새로고침해 주세요.');
      setSelectedId(''); setAnalysis(null); setMappingJson('');
      await reload();
      setMessage(`“${displayName}” 템플릿 삭제가 완료되었습니다. 기존 고객사 템플릿은 유지됩니다.`);
    } catch (exc) { setError(exc instanceof Error ? exc.message : '템플릿 삭제 실패'); }
    finally { setBusy(false); }
  }
  async function generateAutoMapping() {
    if (!chosen) return;
    setBusy(true); setError(''); setMessage(''); setAutoDraft(null);
    try {
      const response = await fetch(`/api/v1/career/pptx/templates/${encodeURIComponent(chosen.templateId)}/auto-mapping`, { cache: 'no-store' });
      if (!response.ok) throw new Error(await getError(response));
      const draft = await response.json() as AutoMappingProposal;
      setAutoDraft(draft);
      if (draft.mapping) {
        setMappingJson(JSON.stringify(draft.mapping, null, 2));
        setMessage(draft.autoApplicable
          ? '자동 매핑을 생성하고 검증했습니다. 항목을 확인한 뒤 아래에서 저장하세요.'
          : '자동 매핑 후보를 생성했습니다. 경고 항목을 확인하고 필요한 셀을 수정한 뒤 저장하세요.');
      } else {
        setError('자동으로 표 구조를 특정하지 못했습니다. 구조 분석 결과를 확인해 주세요.');
      }
    } catch (exc) { setError(exc instanceof Error ? exc.message : '자동 매핑 생성 실패'); }
    finally { setBusy(false); }
  }

  async function inspect() {
    if (!chosen) return;
    setBusy(true); setError(''); setAnalysis(null);
    try {
      const response = await fetch(`/api/v1/career/pptx/templates/${encodeURIComponent(chosen.templateId)}/analysis`, { cache: 'no-store' });
      if (!response.ok) throw new Error(await getError(response));
      setAnalysis(await response.json());
    } catch (exc) { setError(exc instanceof Error ? exc.message : '구조 분석 실패'); }
    finally { setBusy(false); }
  }
  return <section className="panel template-management">
    <div className="section-header"><div><div className="eyebrow">STAGE 4 · TEMPLATE MANAGEMENT</div>
      <h2>고객사 제안서 템플릿 관리</h2>
      <p>고객사 PPTX 업로드 → 호환성 분석 → MySQL 메타데이터 등록 → 생성 화면에 즉시 반영</p>
    </div><span className="step">관리</span></div>
    <form onSubmit={e => void upload(e)} className="template-upload-form">
      <h3>신규 PPTX 등록</h3>
      <div className="formgrid">
        <label>고객사명<input required maxLength={120} value={customer} onChange={e=>setCustomer(e.target.value)} placeholder="예: 우리은행" /></label>
        <label>템플릿 표시명<input required maxLength={120} value={name} onChange={e=>setName(e.target.value)} placeholder="예: 우리은행 투입인력 프로필" /></label>
      </div>
      <div className="formgrid">
        <label>PPTX 파일 (최대 40MB)<input required type="file" accept=".pptx" onChange={e => setFile(e.target.files?.[0] || null)} /></label>
        <label>수동 매핑 JSON (선택 · 자동 생성 지원)<input type="file" accept=".json,application/json" onChange={e => setMappingFile(e.target.files?.[0] || null)} /></label>
      </div>
      <div className="actions"><button className="primary" type="submit" disabled={busy || !file || !name.trim() || !customer.trim()}>{busy?'처리 중…':'템플릿 업로드 및 분석'}</button>
        <span className="muted">파일 내용은 템플릿 폴더에 저장하고, 관리 정보는 MySQL에 저장합니다.</span></div>
    </form>
    <div className="section-header template-list-header"><div><h3>템플릿 목록</h3><p>READY 양식만 PPTX 생성에 사용됩니다. 다른 구조의 양식은 매핑 후 사용하세요.</p></div>
      <button className="secondary" type="button" onClick={() => void reload()} disabled={loading || busy}>목록 새로고침</button></div>
    <div className="template-manager-list">
      {items.map(t => <button type="button" key={t.templateId} className={`template-manager-item ${selectedId === t.templateId ? 'selected' : ''}`}
        onClick={() => { setSelectedId(t.templateId); setAnalysis(null); setAutoDraft(null); setMappingJson(''); setAdoptName(t.displayName); setAdoptCustomer(t.customerName || ''); }}>
        <span><strong>{t.displayName}</strong><small>{t.originalFileName || t.fileName} · {t.customerName || '기존 파일'} · {t.registered ? 'DB 등록' : '기존 폴더 파일'}</small></span>
        <span className={`template-state ${t.status === 'READY' ? 'ready' : 'pending'}`}>{t.status}</span>
      </button>)}
      {!loading && !items.length && <div className="empty">등록된 템플릿이 없습니다.</div>}
    </div>
    {chosen && <div className="template-detail">
      <h3>{chosen.displayName}</h3>
      <p className="muted">ID: {chosen.templateId} · 분석: {chosen.message} · 첫 장 {chosen.firstPageCapacity}건 / 이후 {chosen.continuationPageCapacity}건</p>
      {!chosen.registered && <div className="template-adopt"><p>기존 폴더에서 발견된 파일입니다. MySQL에 등록해야 매핑 및 상태를 관리할 수 있습니다.</p>
        <div className="formgrid"><label>고객사명<input value={adoptCustomer} onChange={e=>setAdoptCustomer(e.target.value)} placeholder="고객사명" /></label>
        <label>표시명<input value={adoptName} onChange={e=>setAdoptName(e.target.value)} placeholder={chosen.displayName} /></label></div>
        <button type="button" className="secondary" disabled={busy} onClick={() => void adopt()}>기존 템플릿 DB 등록</button></div>}
      <div className="actions">
        <button type="button" className="secondary" disabled={busy} onClick={() => void inspect()}>표·필드 구조 분석</button>
        <button type="button" className="secondary" disabled={busy || !chosen.registered} onClick={() => void generateAutoMapping()}>
          자동 매핑 생성
        </button>
        {chosen.registered && <button type="button" className="secondary" disabled={busy || (chosen.status !== 'READY' && chosen.status !== 'DISABLED')}
          onClick={() => void toggleActive()}>{chosen.status === 'DISABLED' ? '활성화' : '비활성화'}</button>}
        {chosen.registered && /^tpl-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}\.pptx$/i.test(chosen.templateId) &&
          <button type="button" className="secondary template-delete" disabled={busy} onClick={() => void removeUploadedTemplate()}>
            등록 템플릿 삭제
          </button>}
      </div>
      {analysis != null && <details open className="template-inspection"><summary>분석 결과 (shape 이름 · 표 행/열)</summary><pre>{JSON.stringify(analysis, null, 2)}</pre></details>}
      {autoDraft && <div className="template-auto-summary" role="status">
        <strong>자동 매핑 결과 · {autoDraft.status === 'CANDIDATE_READY' ? '검증 가능' : '검토 필요'}</strong>
        <p className="muted">규칙 기반 분석 · 신뢰도 {Math.round(autoDraft.confidence * 100)}% · {autoDraft.reason}</p>
        {autoDraft.detected && <p className="muted">
          인적사항 {autoDraft.detected.personalFields.length}개 · 경력 열 {autoDraft.detected.careerFields.length}개 ·
          첫 장 {autoDraft.detected.firstPageCapacity}건 · 이후 {autoDraft.detected.continuationPageCapacity}건
        </p>}
        {autoDraft.warnings.length > 0 && <ul>{autoDraft.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul>}
        <small>자동 생성된 매핑은 아래 입력창에 반영됩니다. 저장 전 실제 출력 위치를 확인하세요.</small>
      </div>}
      {chosen.registered && <form className="template-mapping-form" onSubmit={e=>void applyMapping(e)}>
        <details className="template-inspection">
          <summary>매핑 JSON 직접 편집 (고급)</summary>
          <label>필드 매핑 JSON
            <textarea rows={9} value={mappingJson} onChange={e=>setMappingJson(e.target.value)} placeholder={'{\n  "version": 1,\n  "displayName": "고객사 양식",\n  ...\n}'} /></label>
          <label>JSON 파일에서 읽기<input type="file" accept=".json,application/json" onChange={e => {
            const f = e.target.files?.[0]; if (f) void f.text().then(setMappingJson).catch(() => setError('JSON 파일을 읽을 수 없습니다.'));
          }} /></label>
        </details>
        <button type="submit" className="primary" disabled={busy || !mappingJson.trim()}>자동 매핑 검증 및 저장</button>
      </form>}
    </div>}
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    {message && <div className="alert alert-success" role="status">{message}</div>}
  </section>;
}
