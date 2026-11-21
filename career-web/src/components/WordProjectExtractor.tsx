import React, { useEffect, useState } from 'react';

export type WordProjectProposal = {
  projectName: string;
  startDate: string;
  endDate: string;
  periodText: string;
  submittedRole: string;
  clientName: string;
  workDescription: string;
  include: boolean;
  employerName?: string;
  technologiesText?: string;
  technologiesRaw?: string;
  dbmsText?: string;
  toolsText?: string;
  osText?: string;
  industryText?: string;
  applicationText?: string;
  hardwareText?: string;
  communicationText?: string;
  otherText?: string;
  sourceBlockIds: string[];
  extractionMethod: string;
  confidence: number;
};

type ExtractResponse = {
  projects: WordProjectProposal[];
  warnings: string[];
  diagnostics: {
    blockCount: number;
    projectCount: number;
    headerRows: number;
    tableRows: number;
    paragraphProjects: number;
    engine: string;
    llmCalled: boolean;
  };
};

type Props = {
  importId: string;
  reviewedBlocks: { kind: string; text: string }[];
  disabled?: boolean;
  onApply: (projects: WordProjectProposal[], mode: 'merge' | 'replace') => void;
};

const panel: React.CSSProperties = {
  marginTop: 18, border: '1px solid #a5b4fc', borderRadius: 10,
  padding: 14, background: '#f8faff', color: '#0f172a',
};
const button: React.CSSProperties = {
  border: 0, borderRadius: 7, padding: '10px 14px',
  background: '#1556ad', color: '#fff', cursor: 'pointer',
};
const field: React.CSSProperties = {
  boxSizing: 'border-box', width: '100%', border: '1px solid #cbd5e1',
  borderRadius: 6, padding: 7, marginTop: 4, background: '#fff',
};
const edits: { key: keyof WordProjectProposal; label: string }[] = [
  { key: 'projectName', label: '프로젝트명' },
  { key: 'periodText', label: '원문 기간' },
  { key: 'startDate', label: '시작일 (YYYY-MM-DD)' },
  { key: 'endDate', label: '종료일 (YYYY-MM-DD)' },
  { key: 'clientName', label: '고객사' },
  { key: 'employerName', label: '근무회사' },
  { key: 'submittedRole', label: '역할' },
  { key: 'workDescription', label: '수행업무' },
  { key: 'technologiesText', label: '사용 언어·프레임워크' },
  { key: 'dbmsText', label: 'DBMS' },
  { key: 'toolsText', label: '개발 도구' },
  { key: 'osText', label: 'OS' },
  { key: 'communicationText', label: '통신' },
  { key: 'otherText', label: '기타' },
];

export default function WordProjectExtractor({ importId, reviewedBlocks, disabled, onApply }: Props) {
  const [data, setData] = useState<ExtractResponse | null>(null);
  const [items, setItems] = useState<WordProjectProposal[]>([]);
  const [selected, setSelected] = useState<Record<number, boolean>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  // A proposal is only valid for the exact currently reviewed Word blocks.
  useEffect(() => {
    setData(null);
    setItems([]);
    setSelected({});
    setNotice('');
    setError('');
  }, [importId, reviewedBlocks]);

  async function extract() {
    if (disabled || loading) return;
    setLoading(true); setError(''); setNotice('');
    try {
      const response = await fetch(
        `/api/v1/career/word-imports/${encodeURIComponent(importId)}/projects/extract`,
        { cache: 'no-store' },
      );
      if (!response.ok) {
        const message = await response.text();
        throw new Error(`HTTP ${response.status}: ${message.slice(0, 300)}`);
      }
      const received = await response.json() as ExtractResponse;
      setData(received);
      setItems(received.projects);
      setSelected({});
      // STAGE02_7_AUTO_APPLY_TO_REVIEW: values only populate local review fields.
      // User approval is still required before persisting via PostgreSQL save.
      if (received.projects.length > 0) onApply(received.projects, 'merge');
      setNotice(`프로젝트 ${received.projects.length}건을 추출해 아래 경력 검토 영역에 임시 반영했습니다. 검토 후 PostgreSQL 저장을 누르세요.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }

  function edit(index: number, key: keyof WordProjectProposal, value: string) {
    setItems(previous => previous.map((item, i) => i === index ? { ...item, [key]: value } : item));
  }

  function apply(mode: 'merge' | 'replace') {
    if (disabled || loading) return;
    const chosen = items.filter((item, index) => selected[index] !== false && item.projectName.trim());
    if (!chosen.length) { setError('반영할 프로젝트를 한 건 이상 선택하세요.'); return; }
    if (chosen.length > 100) { setError('프로젝트는 100건까지 반영할 수 있습니다.'); return; }
    if (mode === 'replace' && !window.confirm('기존 프로젝트 목록을 선택한 추출 결과로 대체할까요? 저장 전에는 되돌릴 수 있습니다.')) return;
    onApply(chosen, mode);
    setNotice(`선택한 프로젝트 ${chosen.length}건을 검토 화면에 반영했습니다. PostgreSQL 저장 버튼을 별도로 눌러야 확정됩니다.`);
    setError('');
  }

  const selectedCount = items.filter((item, i) => selected[i] !== false && item.projectName.trim()).length;

  return <section style={panel}>
    <h3 style={{ color: '#1556ad', margin: '0 0 8px' }}>프로젝트 자동 추출 (Stage 02-7)</h3>
    <p style={{ fontSize: 13, margin: '0 0 12px', color: '#475569' }}>
      Word 표 헤더·수행기간·고객사 등을 규칙으로 인식합니다. Ollama를 호출하지 않으며,
      검토한 프로젝트만 기존 경력정보에 반영합니다.
    </p>
    <button type="button" style={button} disabled={disabled || loading} onClick={() => void extract()}>
      {loading ? '원문 분석 중...' : '프로젝트 자동 추출 (AI 미사용)'}
    </button>
    {disabled && <p style={{ fontSize: 12, color: '#b45309' }}>원문이 수정됐다면 먼저 ‘원문 재분석’을 눌러 저장해 주세요.</p>}
    {data && <>
      <p style={{ fontSize: 13 }}>
        <strong>{items.length}건 발견</strong> · 원문 {data.diagnostics.blockCount}블록 ·
        표 기반 {data.diagnostics.tableRows}건 · 문단 기반 {data.diagnostics.paragraphProjects}건 ·
        선택 {selectedCount}건
      </p>
      {data.warnings.map((warning, index) =>
        <p key={index} style={{ fontSize: 12, color: '#92400e' }}>{warning}</p>)}
      <div style={{ maxHeight: 460, overflowY: 'auto', borderTop: '1px solid #cbd5e1' }}>
        {items.map((item, index) => <div key={`${index}-${item.sourceBlockIds.join('-')}`}
          style={{ padding: '10px 0', borderBottom: '1px solid #e2e8f0' }}>
          <label style={{ fontSize: 13, display: 'block' }}>
            <input type="checkbox" checked={selected[index] !== false}
              disabled={disabled || loading}
              onChange={event => setSelected(prev => ({ ...prev, [index]: event.target.checked }))} />{' '}
            <strong>{index + 1}. {item.projectName}</strong>
          </label>
          <div style={{ marginLeft: 22, fontSize: 12, color: '#475569' }}>
            {item.periodText || '기간 미기재'} · 고객사: {item.clientName || '미기재'} ·
            근무회사: {item.employerName || '미기재'} · 역할: {item.submittedRole || '미기재'}
          </div>
          <div style={{ marginLeft: 22, fontSize: 12, color: '#64748b' }}>
            근거 블록: {item.sourceBlockIds.join(', ')} · 규칙 커버리지: {Math.round(item.confidence * 100)}%
          </div>
          {/* STAGE02_7_TECH_MAPPING: visible source-to-field preview */}
          <div style={{ marginLeft: 22, fontSize: 12, color: '#1556ad', marginTop: 4 }}>
            사용 언어·프레임워크: <strong>{item.technologiesText || '미기재'}</strong>
            {item.technologiesRaw && item.technologiesRaw !== item.technologiesText &&
              <span style={{ color: '#64748b' }}> · 원문: {item.technologiesRaw}</span>}
            {item.dbmsText ? ` · DBMS: ${item.dbmsText}` : ''}
            {item.toolsText ? ` · TOOL: ${item.toolsText}` : ''}
          </div>
          <details style={{ margin: '6px 0 0 22px' }}>
            <summary style={{ cursor: 'pointer', color: '#1556ad', fontSize: 12 }}>추출값 검토·수정</summary>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 8, marginTop: 8 }}>
              {edits.map(({ key, label }) => <label key={key} style={{ fontSize: 12, fontWeight: 500 }}>
                {label}
                <input style={field} value={String(item[key] ?? '')} disabled={disabled || loading}
                  onChange={event => edit(index, key, event.target.value)} />
              </label>)}
            </div>
          </details>
        </div>)}
      </div>
      {items.length > 0 && <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
        <button type="button" style={button} disabled={disabled || loading || !selectedCount}
          onClick={() => apply('merge')}>선택 프로젝트 병합 (기존 수정값 유지)</button>
        <button type="button" style={{ ...button, background: '#475569' }}
          disabled={disabled || loading || !selectedCount} onClick={() => apply('replace')}>
          선택 프로젝트로 목록 대체
        </button>
      </div>}
      <p style={{ fontSize: 12, color: '#475569' }}>병합·대체는 화면에 임시 반영됩니다. 아래 ‘검토 내용 PostgreSQL 저장’이 최종 저장입니다.</p>
    </>}
    {error && <p role="alert" style={{ color: '#b91c1c', whiteSpace: 'pre-wrap' }}>{error}</p>}
    {notice && <p role="status" style={{ color: '#166534' }}>{notice}</p>}
  </section>;
}
