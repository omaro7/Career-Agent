// STAGE02_7_REGISTER_AUTOFILL_UNSAVED
import React, { useEffect, useState, type ChangeEvent } from 'react';
import WordProjectExtractor, { type WordProjectProposal } from './WordProjectExtractor';

type Block = { kind: 'heading' | 'paragraph' | 'table'; text: string };
type Draft = { title: string; blocks: Block[] };
type Project = { projectName: string; startDate: string; endDate: string; periodText: string;
  submittedRole: string; clientName: string; workDescription: string; include: boolean;
  employerName?: string; technologiesText?: string; technologiesRaw?: string; dbmsText?: string; toolsText?: string;
  osText?: string; industryText?: string; applicationText?: string;
  hardwareText?: string; communicationText?: string; otherText?: string;
  sourceBlockIds?: string[]; extractionMethod?: string; confidence?: number };
type Profile = {
  name: string; affiliation: string; positionTitle: string; birthDate: string; ageYears: string;
  educationText: string; degree: string; certificationsText: string; displayGrade: string;
  businessRole: string; assignmentPeriod: string; assignmentMonths: string; allocationRate: string;
  customerName: string; skillsText?: string; careerSummary?: string; projects: Project[]; extractionWarnings: string[];
};
type Import = { id: string; filename: string; reviewed: Draft; structured: Profile;
  status: string; templateId?: string; hasOutput: boolean; autoExtractedUnsaved?: boolean };
type Template = { templateId: string; customerName?: string; displayName?: string;
  status: string; isActive?: boolean; layoutMode?: string; message?: string };
type Catalog = { templates: Template[]; defaultTemplateId?: string };
type Rendered = { status: string; downloadUrl: string; renderMeta?: { pageCount?: string; projectCount?: string } };
type EvidenceValue = {value: string; sourceBlockIds: string[]};
type AIEvidence = {fields?: Record<string, EvidenceValue>; projects?: Record<string, EvidenceValue>[]};
type AIProgress = { stage?: string; completedChunks?: number; currentChunk?: number;
  totalChunks?: number; percent?: number };
type AIRun = { runId: string; status: 'QUEUED' | 'RUNNING' | 'CANCELLING' | 'CANCELLED' | 'DONE' | 'FAILED' | 'STALE';
  stale?: boolean; structured?: Profile | null; evidence?: AIEvidence | null;
  warnings?: string[]; model?: string; error?: string; elapsedSeconds?: number;
  progress?: AIProgress;
  meta?: {chunkCount?: number; projectCount?: number; progress?: AIProgress} };

const ROOT = '/api/v1/career';

// STAGE02_7_BIRTHDATE_PATCH
// Keep the existing API/PPTX ISO date contract; present YYYY.MM.DD in React.
function seoulToday(): {year: number; month: number; day: number} {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date());
  const get = (key: 'year' | 'month' | 'day') => Number(parts.find(p => p.type === key)?.value ?? 0);
  return {year: get('year'), month: get('month'), day: get('day')};
}

function normalizeBirthDate(raw: string): string {
  const match = /(?:^|[^0-9])([0-9]{4}|[0-9]{2})\s*[.\/\-년]\s*([0-9]{1,2})\s*[.\/\-월]\s*([0-9]{1,2})\s*일?(?![0-9])/.exec(raw.trim());
  if (!match) return '';
  const today = seoulToday();
  let year = Number(match[1]);
  if (match[1].length === 2) year += year <= today.year % 100 ? 2000 : 1900;
  const month = Number(match[2]);
  const day = Number(match[3]);
  const actual = new Date(Date.UTC(year, month - 1, day));
  if (actual.getUTCFullYear() !== year || actual.getUTCMonth() + 1 !== month || actual.getUTCDate() !== day) return '';
  const age = today.year - year - (today.month < month || (today.month === month && today.day < day) ? 1 : 0);
  if (age < 0 || age > 120) return '';
  return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

function fullAge(birthDate: string): string {
  const iso = normalizeBirthDate(birthDate);
  if (!iso) return '';
  const [year, month, day] = iso.split('-').map(Number);
  const now = seoulToday();
  const age = now.year - year - (now.month < month || (now.month === month && now.day < day) ? 1 : 0);
  return age >= 0 && age <= 120 ? String(age) : '';
}

function birthFromBlocks(reviewed: Draft): string {
  for (const block of reviewed.blocks) {
    const cells = block.text.split('|').map(part => part.trim());
    for (let index = 0; index + 1 < cells.length; index++) {
      if (/^(?:생\s*년\s*월\s*일|출\s*생\s*일)$/.test(cells[index])) {
        const extracted = normalizeBirthDate(cells[index + 1]);
        if (extracted) return extracted;
      }
    }
    const inline = /(?:생\s*년\s*월\s*일|출\s*생\s*일)\s*[:：]?\s*([0-9]{2,4}\s*[.\/\-년]\s*[0-9]{1,2}\s*[.\/\-월]\s*[0-9]{1,2}\s*일?)/.exec(block.text);
    if (inline) {
      const extracted = normalizeBirthDate(inline[1]);
      if (extracted) return extracted;
    }
  }
  return '';
}

function completeBirthFields(profile: Profile, reviewed: Draft): Profile {
  const iso = normalizeBirthDate(profile.birthDate) || birthFromBlocks(reviewed);
  if (!iso) return profile;
  const age = fullAge(iso);
  return {...profile, birthDate: iso, ageYears: age};
}


const profileFields: { key: keyof Omit<Profile, 'projects' | 'extractionWarnings'>; label: string; example?: string }[] = [
  {key: 'name', label: '성명 *'}, {key: 'affiliation', label: '소속'},
  {key: 'positionTitle', label: '직급/직책'}, {key: 'birthDate', label: '생년월일', example: 'YYYY-MM-DD'},
  {key: 'ageYears', label: '만 나이 (미입력 시 생년월일로 계산)'},
  {key: 'educationText', label: '최종학력 / 전공'}, {key: 'degree', label: '학위'},
  {key: 'certificationsText', label: '자격증'}, {key: 'displayGrade', label: '기술등급'},
  {key: 'businessRole', label: '본 사업 담당업무'}, {key: 'assignmentPeriod', label: '본 사업 투입기간', example: '2026.12 ~ 2027.12'},
  {key: 'assignmentMonths', label: '투입개월', example: '12'},
  {key: 'allocationRate', label: '투입율', example: '80%'},
  {key: 'customerName', label: '제출 고객사 (미입력 시 템플릿 고객사)'},
  {key: 'skillsText', label: '기술스택 (AI 분석, 현재 PPTX 매핑 제외)'},
  {key: 'careerSummary', label: '경력 요약 (AI 분석, 현재 PPTX 매핑 제외)'},
];
type ProjectTextKey = {
  [K in keyof Project]-?: NonNullable<Project[K]> extends string ? K : never
}[keyof Project];
const projectFields: { key: ProjectTextKey; label: string; example?: string }[] = [
  {key: 'projectName', label: '프로젝트명 *'}, {key: 'startDate', label: '시작일', example: 'YYYY-MM-DD'},
  {key: 'endDate', label: '종료일', example: 'YYYY-MM-DD'}, {key: 'periodText', label: '원문 참여기간'},
  {key: 'submittedRole', label: '담당역할'}, {key: 'clientName', label: '발주처'},
  {key: 'workDescription', label: '주요 수행업무'},
  {key: 'employerName', label: '근무회사'},
  // STAGE02_7_TECH_MAPPING: project-scoped technology, stored through existing PUT /structured
  {key: 'technologiesText', label: '사용 언어·프레임워크'},
  {key: 'dbmsText', label: 'DBMS'},
  {key: 'toolsText', label: '개발 도구'},
  {key: 'osText', label: 'OS'},
  {key: 'communicationText', label: '통신'},
  {key: 'otherText', label: '기타'},
];
const emptyProject = (): Project => ({ projectName: '', startDate: '', endDate: '', periodText: '',
  submittedRole: '', clientName: '', workDescription: '', include: true });
const color = '#1556ad';
const inputStyle: React.CSSProperties = { width: '100%', boxSizing: 'border-box', padding: 9,
  borderRadius: 7, border: '1px solid #cbd5e1', background: '#fff', color: '#0f172a' };
const labelStyle: React.CSSProperties = { display: 'block', fontSize: 13, fontWeight: 600 };
const gridStyle: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 12 };
const buttonStyle: React.CSSProperties = { padding: '10px 16px', borderRadius: 7, background: color,
  border: 'none', color: '#fff', cursor: 'pointer' };

async function parse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    try { detail = (JSON.parse(text) as { detail?: string }).detail || text; } catch { /* plain text */ }
    throw new Error(`HTTP ${response.status}: ${detail.slice(0, 350)}`);
  }
  return response.json() as Promise<T>;
}

// BWG_STAGE02_8_AUTO_DOWNLOAD: fetch the completed PPTX and trigger a browser download.
// The API validates the stored artifact and returns a FileResponse. Keep its URL
// as a fallback for browsers that block programmatic downloads.
async function downloadGeneratedPptx(downloadUrl: string, importId: string): Promise<void> {
  const url = new URL(downloadUrl, window.location.origin);
  const expectedPath = `${ROOT}/word-imports/${importId}/download`;
  if (url.origin !== window.location.origin || url.pathname !== expectedPath) {
    throw new Error('PPTX 다운로드 주소가 올바르지 않습니다.');
  }
  const response = await fetch(url.pathname + url.search, {
    method: 'GET', credentials: 'same-origin', cache: 'no-store',
  });
  if (!response.ok) {
    throw new Error(`PPTX 다운로드 실패 (HTTP ${response.status})`);
  }
  const contentType = (response.headers.get('content-type') || '').toLowerCase();
  if (contentType.includes('text/html') || contentType.includes('application/json')) {
    throw new Error('PPTX가 아닌 응답을 받았습니다.');
  }
  const fileBlob = await response.blob();
  if (fileBlob.size === 0) throw new Error('PPTX 파일이 비어 있습니다.');
  const objectUrl = window.URL.createObjectURL(fileBlob);
  const anchor = document.createElement('a');
  try {
    anchor.href = objectUrl;
    anchor.download = 'career-result.pptx';
    anchor.style.display = 'none';
    document.body.appendChild(anchor);
    anchor.click();
  } finally {
    anchor.remove();
    // Keep the URL alive briefly to avoid races with browser download start.
    window.setTimeout(() => window.URL.revokeObjectURL(objectUrl), 30000);
  }
}

export default function WordCareerImport() {
  const [record, setRecord] = useState<Import | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templateId, setTemplateId] = useState('');
  const [confirmReviewed, setConfirmReviewed] = useState(false);
  const [dirtyRaw, setDirtyRaw] = useState(false);
  const [dirtyProfile, setDirtyProfile] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [downloadUrl, setDownloadUrl] = useState('');
  const [aiRun, setAiRun] = useState<AIRun | null>(null);
  const [aiFocus, setAiFocus] = useState('프로젝트명, 고객사, 수행기간, PL/PM 역할, 담당업무, 기술스택');
  const [existingImportId, setExistingImportId] = useState('');

  useEffect(() => {
    if (!record || !aiRun || !(['QUEUED', 'RUNNING', 'CANCELLING'].includes(aiRun.status))) return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const response = await parse<AIRun>(await fetch(
          `${ROOT}/word-imports/${record.id}/reinterpret/${aiRun.runId}`, {cache:'no-store'}));
        if (!cancelled) {
          setAiRun(response);
          if (response.status === 'FAILED') setError(`AI 재해석 실패: ${response.error || 'Agent 로그 확인 필요'}`);
        }
      } catch (e) { if (!cancelled) setError(String(e)); }
    }, 4000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [record?.id, aiRun?.runId, aiRun?.status]);

  function citedText(ids: string[] | undefined): string {
    if (!ids || !draft) return '';
    return ids.map(id => {
      const match = /^B(\d+)$/.exec(id);
      const block = match ? draft.blocks[Number(match[1]) - 1] : undefined;
      return block ? `${id}: ${block.text.slice(0, 180)}` : `${id}: 원문 블록을 찾을 수 없음`;
    }).join(' / ');
  }

  async function loadExistingWord() {
    const id = existingImportId.trim();
    if (!/^[0-9a-fA-F-]{36}$/.test(id)) { setError('기존 Word 등록 UUID를 입력해 주세요.'); return; }
    setBusy(true); setError(''); setNotice(''); setAiRun(null); setDownloadUrl('');
    try {
      const r = await parse<Import>(await fetch(`${ROOT}/word-imports/${id}`, {cache:'no-store'}));
      setRecord(r); setDraft(r.reviewed);
      const enriched = completeBirthFields(r.structured, r.reviewed);
      setProfile(enriched);
      setDirtyRaw(false); setDirtyProfile((enriched.birthDate !== r.structured.birthDate || enriched.ageYears !== r.structured.ageYears || !!r.autoExtractedUnsaved)); setConfirmReviewed(false);
      // Restore latest AI run on reload, including unfinished/cancelled jobs.
      try {
        const latest = await parse<AIRun | {runId: null}>(await fetch(
          `${ROOT}/word-imports/${id}/reinterpret/latest`, {cache:'no-store'}));
        if (latest.runId) setAiRun(latest as AIRun);
      } catch { /* A prior AI run is optional; Word data must still load. */ }
      setNotice('기존에 등록한 Word 원문과 구조화 경력정보를 불러왔습니다. AI 재해석을 실행할 수 있습니다.');
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  async function reinterpretAI() {
    if (!record || dirtyRaw) { setError('원문 변경 내용을 먼저 저장하고 재추출해 주세요.'); return; }
    setError(''); setNotice(''); setAiRun(null);
    try {
      const job = await parse<AIRun>(await fetch(`${ROOT}/word-imports/${record.id}/reinterpret`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({focus:aiFocus}),
      }));
      setAiRun(job);
      setNotice('로컬 Ollama AI 재해석을 시작했습니다. 완료 상태는 자동 확인됩니다.');
    } catch (e) { setError(String(e)); }
  }

  async function cancelAI() {
    if (!record || !aiRun || !['QUEUED', 'RUNNING'].includes(aiRun.status)) return;
    setError('');
    try {
      const response = await parse<AIRun>(await fetch(
        `${ROOT}/word-imports/${record.id}/reinterpret/${aiRun.runId}/cancel`,
        {method:'POST'}
      ));
      setAiRun(response);
      setNotice(response.status === 'CANCELLED'
        ? 'AI 분석을 취소했습니다. 원문 및 검토 데이터는 유지됩니다.'
        : '취소 요청을 전달했습니다. 현재 Ollama 스트림 종료를 확인하는 중입니다.');
    } catch (e) { setError(String(e)); }
  }

  function applyAI(overwrite: boolean) {
    if (!aiRun?.structured || aiRun.status !== 'DONE' || aiRun.stale) return;
    const suggestion = aiRun.structured;
    setProfile(previous => {
      if (!previous) return previous;
      const next: Profile = {...previous};
      for (const {key} of profileFields) {
        const candidate = suggestion[key];
        if (candidate && (overwrite || !next[key])) next[key] = candidate;
      }
      const merged = overwrite ? [...previous.projects] : [...previous.projects];
      for (const project of suggestion.projects || []) {
        if (!project.projectName) continue;
        const index = merged.findIndex(p => p.projectName === project.projectName &&
          p.startDate === project.startDate && p.endDate === project.endDate);
        if (index < 0) {
          if (merged.length < 100) merged.push({...emptyProject(), ...project});
        } else {
          const update = {...merged[index]};
          for (const {key} of projectFields) {
            const value = project[key];
            if (typeof value === 'string' && value.trim() && (overwrite || !update[key])) {
              update[key] = value;
            }
          }
          merged[index] = update;
        }
      }
      next.projects = merged;
      next.extractionWarnings = [...new Set([...(previous.extractionWarnings || []),
        ...(suggestion.extractionWarnings || [])])].slice(0, 30);
      return next;
    });
    setDirtyProfile(true);
    setConfirmReviewed(false);
    setDownloadUrl('');
    setNotice(overwrite ? 'AI 추출값을 검토 화면에 반영했습니다. 기존에 값이 없는 AI 항목은 유지했습니다. 확인 후 PostgreSQL에 저장하세요.'
      : 'AI 결과로 비어 있던 필드를 채웠습니다. 확인 후 PostgreSQL에 저장하세요.');
  }

  // STAGE02_7_REVIEW_SYNC_FIX: a proposal is merged into the existing review form.
  // No PostgreSQL update occurs here. The operator saves only after reviewing.
  function applyExtractedProjects(candidates: WordProjectProposal[], mode: 'merge' | 'replace') {
    setProfile(previous => {
      if (!previous) return previous;
      const merged: Project[] = mode === 'replace' ? [] : [...previous.projects];
      // Ignore whitespace, punctuation and capitalization differences in titles.
      const normalizeTitle = (value: string) =>
        (value || '').normalize('NFKC').toLowerCase().replace(/[^0-9a-z가-힣]/g, '');
      for (const candidate of candidates) {
        if (!candidate.projectName.trim()) continue;
        const project: Project = { ...emptyProject(), ...candidate };
        const sourceIds = new Set(project.sourceBlockIds || []);
        let position = mode === 'replace' ? -1 : merged.findIndex(p =>
          (p.sourceBlockIds || []).some(id => sourceIds.has(id)));
        if (position < 0 && mode === 'merge') {
          const title = normalizeTitle(project.projectName);
          const sameTitle = merged.map((p, index) => ({p, index}))
            .filter(({p}) => normalizeTitle(p.projectName) === title);
          const exact = sameTitle.filter(({p}) =>
            (!p.startDate || !project.startDate || p.startDate === project.startDate) &&
            (!p.endDate || !project.endDate || p.endDate === project.endDate) &&
            (!p.clientName || !project.clientName ||
              normalizeTitle(p.clientName) === normalizeTitle(project.clientName)));
          // Do not match ambiguously named projects; protect manual edits.
          if (exact.length === 1) position = exact[0].index;
          else if (sameTitle.length === 1 &&
            sameTitle[0].p.startDate === project.startDate &&
            sameTitle[0].p.endDate === project.endDate) position = sameTitle[0].index;
        }
        if (position < 0) {
          if (merged.length < 100) merged.push(project);
          continue;
        }
        // Merge fills blank reviewed fields only. Never overwrite a human edit.
        const current: Project = { ...merged[position] };
        for (const {key} of projectFields) {
          const value = project[key];
          const existing = current[key];
          if (typeof value === 'string' && value.trim() &&
              !(typeof existing === 'string' && existing.trim())) {
            (current as unknown as Record<string, unknown>)[key] = value;
          }
        }
        // These metadata fields are not rendered as input controls.
        for (const key of ['technologiesRaw', 'industryText', 'applicationText', 'hardwareText'] as const) {
          if (!current[key] && project[key]) current[key] = project[key];
        }
        if (!current.sourceBlockIds?.length) current.sourceBlockIds = project.sourceBlockIds;
        if (!current.extractionMethod) current.extractionMethod = project.extractionMethod;
        if (current.confidence === undefined) current.confidence = project.confidence;
        merged[position] = current;
      }
      return { ...previous, projects: merged };
    });
    setDirtyProfile(true);
    setConfirmReviewed(false);
    setDownloadUrl('');
    setNotice('프로젝트 추출값을 아래 경력 검토 영역에 임시 반영했습니다. 확인 후 PostgreSQL 저장을 눌러 확정하세요.');
  }

  async function refreshTemplates() {
    const catalog = await parse<Catalog>(await fetch(`${ROOT}/pptx/templates`, {cache: 'no-store'}));
    const ready = (catalog.templates || []).filter(t => t.status === 'READY' && t.isActive !== false);
    setTemplates(ready);
    setTemplateId(prev => ready.some(t => t.templateId === prev) ? prev : (
      ready.some(t => t.templateId === catalog.defaultTemplateId) ? catalog.defaultTemplateId! : ready[0]?.templateId || ''));
  }
  useEffect(() => { void refreshTemplates().catch(e => setError(String(e))); }, []);

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.docx') || file.size > 15 * 1024 * 1024) {
      setError('.docx 파일(최대 15MB)을 선택해 주세요.'); return;
    }
    setBusy(true); setError(''); setNotice(''); setDownloadUrl(''); setConfirmReviewed(false);
    try {
      const form = new FormData(); form.append('file', file);
      const r = await parse<Import>(await fetch(`${ROOT}/word-imports`, {method: 'POST', body: form}));
      setRecord(r); setDraft(r.reviewed);
      const enriched = completeBirthFields(r.structured, r.reviewed);
      setProfile(enriched); setDirtyRaw(false); setDirtyProfile((enriched.birthDate !== r.structured.birthDate || enriched.ageYears !== r.structured.ageYears || !!r.autoExtractedUnsaved)); setAiRun(null);
      await refreshTemplates();
      setNotice('Word 문단·표 추출 완료. 인적사항과 프로젝트를 확인한 후 저장하세요.');
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  function editBlock(index: number, text: string) {
    setDraft(d => d ? {...d, blocks: d.blocks.map((b, i) => i === index ? {...b, text} : b)} : d);
    setDirtyRaw(true); setDirtyProfile(true); setDownloadUrl(''); setConfirmReviewed(false);
  }
  // STAGE02_7_BIRTHDATE_PATCH: update only DOB and age; preserve reviewed projects.
  function extractBirthDateFromReview() {
    if (!draft || !profile) return;
    const birthDate = birthFromBlocks(draft);
    if (!birthDate) {
      setError('원문에서 생년월일을 찾을 수 없습니다. 원문의 생년월일 표기 또는 셀 구성을 확인하세요.');
      return;
    }
    const ageYears = fullAge(birthDate);
    setProfile(previous => previous ? {...previous, birthDate, ageYears} : previous);
    setDirtyProfile(true);
    setConfirmReviewed(false);
    setDownloadUrl('');
    setError('');
    setNotice(`생년월일 ${birthDate.replace(/-/g, '.')} · 만 ${ageYears}세를 검토 화면에 반영했습니다. PostgreSQL 저장 버튼을 눌러 확정하세요.`);
  }

  function editProfile(key: keyof Omit<Profile, 'projects' | 'extractionWarnings'>, text: string) {
    setProfile(p => {
      if (!p) return p;
      if (key !== 'birthDate') return {...p, [key]: text};
      const iso = normalizeBirthDate(text);
      return {...p, birthDate: iso || text, ageYears: iso ? fullAge(iso) : ''};
    });
    setDirtyProfile(true); setDownloadUrl(''); setConfirmReviewed(false);
  }
  function editProject(index: number, patch: Partial<Project>) {
    setProfile(p => p ? {...p, projects: p.projects.map((project, i) => i === index ? {...project, ...patch} : project)} : p);
    setDirtyProfile(true); setDownloadUrl(''); setConfirmReviewed(false);
  }

  async function reextract() {
    if (!record || !draft) return;
    setBusy(true); setError(''); setNotice(''); setDownloadUrl('');
    try {
      const r = await parse<Import>(await fetch(`${ROOT}/word-imports/${record.id}/review`, {
        method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(draft),
      }));
      setRecord(r); setDraft(r.reviewed);
      const enriched = completeBirthFields(r.structured, r.reviewed);
      setProfile(enriched); setDirtyRaw(false); setDirtyProfile(true); setAiRun(null);
      setNotice('수정된 원문에서 다시 추출했습니다. 기존 구조화 필드 편집 내용은 재추출로 초기화되므로 다시 검토하세요.');
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  async function saveStructured() {
    if (!record || !profile) return;
    setBusy(true); setError(''); setNotice(''); setDownloadUrl('');
    try {
      const r = await parse<Import>(await fetch(`${ROOT}/word-imports/${record.id}/structured`, {
        method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(profile),
      }));
      setRecord(r); setProfile(r.structured); setDirtyProfile(false); setConfirmReviewed(false);
      setNotice('검토한 Word 경력정보를 PostgreSQL과 저장된 프로파일에 등록했습니다. 출력 동의 후 PPTX 생성이 가능합니다.');
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  async function render() {
    if (!record || !templateId || dirtyRaw || dirtyProfile || !confirmReviewed) return;
    setBusy(true); setError(''); setNotice(''); setDownloadUrl('');
    try {
      const r = await parse<Rendered>(await fetch(`${ROOT}/word-imports/${record.id}/render`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({templateId, confirmReviewed: true}),
      }));
      setDownloadUrl(r.downloadUrl);
      // 생성 성공 즉시 다운로드. 실패해도 아래의 다운로드 링크는 남깁니다.
      try {
        await downloadGeneratedPptx(r.downloadUrl, record.id);
      } catch (downloadError) {
        setError(`PPTX 생성은 완료됐지만 자동 다운로드에 실패했습니다: ${String(downloadError)}. 아래 다운로드 링크를 클릭해 주세요.`);
      }
      setNotice(`고객사 PPTX 생성 완료 (슬라이드 ${r.renderMeta?.pageCount || '?'}장, 경력 ${r.renderMeta?.projectCount || '?'}건). 실제 결과 파일을 열어 서식을 확인하세요.`);
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  return <section style={{maxWidth: 1080, margin: '0 auto', padding: 20, border: '1px solid #dbeafe', borderRadius: 12, background: '#fff', color: '#0f172a'}}>
    <h2 style={{color, marginTop: 0}}>비정형 Word 경력기술서 등록</h2>
    <p style={{color:'#475569'}}>기존 회사 Excel 등록과 별도 · Word 추출 → 경력 검토·수정 → 고객사 PPTX 지정 셀 반영</p>
    <label style={labelStyle}>비정형 Word 파일 (.docx, 최대 15MB)
      <input style={{...inputStyle, marginTop:6}} type="file" accept=".docx" disabled={busy} onChange={e => void upload(e)} />
    </label>
    <div style={{display:'flex', gap:8, flexWrap:'wrap', marginTop:12, alignItems:'center'}}>
      <input style={{...inputStyle, flex:'1 1 260px', width:'auto'}} value={existingImportId}
        onChange={e=>setExistingImportId(e.target.value)} placeholder="이미 등록한 Word의 저장 ID(UUID)" />
      <button type="button" style={buttonStyle} disabled={busy} onClick={()=>void loadExistingWord()}>기존 Word 불러오기</button>
    </div>
    {record && <p style={{color:'#475569'}}>등록 파일: <strong>{record.filename}</strong> · 저장 ID: {record.id}</p>}
    {draft && <details hidden style={{marginTop:16, border:'1px solid #cbd5e1', borderRadius:8, padding:12}}>
      <summary style={{cursor:'pointer', fontWeight:600}}>Word 원문 블록 검토·수정 ({draft.blocks.length}개)</summary>
      <p style={{fontSize:12, color:'#b45309'}}>원문 수정 후 ‘원문 재분석’을 누르면 구조화 필드를 다시 추출합니다.</p>
      <div style={{maxHeight:350, overflowY:'auto'}}>
        {draft.blocks.map((block, index) => <label key={index} style={{...labelStyle, marginBottom:8}}>
          {index + 1}. {block.kind}
          <textarea style={{...inputStyle, minHeight:54}} value={block.text} disabled={busy} onChange={e=>editBlock(index,e.target.value)} />
        </label>)}
      </div>
      <button type="button" style={buttonStyle} disabled={busy || !dirtyRaw} onClick={()=>void reextract()}>원문 재분석</button>
    </details>}
    <div hidden aria-hidden="true" data-bwg-ui="hide-project-extractor">
    {record && draft && profile && <WordProjectExtractor
      importId={record.id} reviewedBlocks={draft.blocks} disabled={busy || dirtyRaw}
      onApply={applyExtractedProjects} />}
    </div>
    {record && draft && <section hidden style={{marginTop:18, border:'1px solid #93c5fd', padding:14, borderRadius:9, background:'#eff6ff'}}>
      <h3 style={{color, marginTop:0}}>AI 원문 재해석 (Stage 02-6)</h3>
      <p style={{fontSize:13, color:'#475569'}}>Ollama 로컬 LLM · LangGraph. 원문 근거가 없는 항목은 제외하고 사용자가 승인할 때만 기존 경력정보에 반영합니다.</p>
      <label style={labelStyle}>추출 우선 항목 (최대 300자)
        <input style={{...inputStyle, marginTop:6}} value={aiFocus} maxLength={300}
          onChange={e=>setAiFocus(e.target.value)} placeholder="예: 프로젝트명, PL 역할, 주요 수행업무" />
      </label>
      <div style={{marginTop:10}}>
        <button type="button" style={buttonStyle} disabled={busy || dirtyRaw || !!(aiRun && ['QUEUED','RUNNING','CANCELLING'].includes(aiRun.status))}
          onClick={()=>void reinterpretAI()}>✨ AI 원문 재해석</button>
        {aiRun && ['QUEUED', 'RUNNING', 'CANCELLING'].includes(aiRun.status) &&
          <button type="button" style={{...buttonStyle, background:'#b91c1c', marginLeft:10}}
            disabled={busy || aiRun.status === 'CANCELLING'} onClick={()=>void cancelAI()}>
            {aiRun.status === 'CANCELLING' ? '취소 요청 중...' : '■ AI 분석 취소'}
          </button>}
      </div>
      {aiRun && <div role="status" aria-live="polite" style={{fontSize:13,marginTop:12}}>
        실행 상태: <strong>{aiRun.status}</strong>{aiRun.model ? ` · ${aiRun.model}` : ''}
        {(() => { const p = aiRun.progress || aiRun.meta?.progress || {};
          const progress = aiRun.status === 'DONE' ? 100 : Math.min(99, Math.max(0, p.percent ?? 0));
          return <div style={{marginTop:7}}>
            <progress value={progress} max={100} style={{width:'100%',height:14}} />
            <div style={{display:'flex',gap:12,flexWrap:'wrap'}}>
              <strong>{progress}%</strong>
              <span>단계: {p.stage || (aiRun.status === 'QUEUED' ? 'QUEUED' : '준비 중')}</span>
              {p.totalChunks ? <span>완료 청크: {p.completedChunks || 0}/{p.totalChunks}</span> : null}
              {typeof aiRun.elapsedSeconds === 'number' ? <span>경과: {Math.floor(aiRun.elapsedSeconds / 60)}분 {aiRun.elapsedSeconds % 60}초</span> : null}
            </div>
            <div style={{color:'#475569',marginTop:4}}>진행률은 추출 완료 청크 기준입니다. 현재 청크의 토큰 생성률은 표시하지 않습니다.</div>
          </div>;
        })()}
      </div>}
      {aiRun?.status === 'CANCELLING' && <p role="status">취소 요청이 처리되고 있습니다. 다른 작업의 Ollama 모델은 중지하지 않습니다.</p>}
      {aiRun?.status === 'CANCELLED' && <p role="status" style={{color:'#475569'}}>AI 분석이 취소되었습니다. Word 원문과 저장된 검토 내용은 유지됩니다.</p>}
      {aiRun?.status === 'STALE' && <p role="alert">Word 원문이 변경되었습니다. AI 재해석을 다시 실행해야 합니다.</p>}
      {aiRun?.status === 'DONE' && aiRun.structured && <div style={{marginTop:12}}>
        <h4 style={{margin:'8px 0'}}>AI 추출 결과 및 원문 근거</h4>
        <div style={{maxHeight:310, overflowY:'auto', background:'#fff', borderRadius:7, padding:10}}>
          {profileFields.map(({key,label}) => {
            const ev = aiRun.evidence?.fields?.[key];
            if (!ev?.value) return null;
            return <div key={key} style={{padding:'6px 0', borderBottom:'1px solid #e2e8f0'}}>
              <strong>{label}</strong>: {ev.value}
              <div style={{fontSize:12, color:'#475569'}}>근거: {citedText(ev.sourceBlockIds)}</div>
            </div>;
          })}
          {(aiRun.structured.projects || []).map((project, i) => <details key={i} style={{marginTop:8}}>
            <summary>프로젝트 {i+1}: {project.projectName}</summary>
            {projectFields.map(({key,label}) => {
              const ev = aiRun.evidence?.projects?.[i]?.[key];
              return ev?.value ? <div key={key} style={{fontSize:13, padding:'4px 0'}}>
                <strong>{label}</strong>: {ev.value}<div style={{color:'#475569'}}>근거: {citedText(ev.sourceBlockIds)}</div>
              </div> : null;
            })}
          </details>)}
        </div>
        {aiRun.warnings?.length ? <details style={{marginTop:8}}><summary>검증 경고 {aiRun.warnings.length}건</summary>
          {aiRun.warnings.map((w,i)=><p key={i} style={{fontSize:12}}>{w}</p>)}
        </details> : null}
        <div style={{display:'flex', gap:8, flexWrap:'wrap', marginTop:12}}>
          <button type="button" style={buttonStyle} disabled={busy} onClick={()=>applyAI(false)}>빈 항목만 채우기</button>
          <button type="button" style={{...buttonStyle, background:'#334155'}} disabled={busy} onClick={()=>applyAI(true)}>
            AI 추출값으로 기존 필드 갱신</button>
        </div>
        <p style={{fontSize:12, color:'#475569'}}>두 작업 모두 임시 반영입니다. 아래 ‘검토 내용 PostgreSQL 저장’ 버튼을 눌러야 저장됩니다.</p>
      </div>}
    </section>}
    {profile && <>
      <h3 style={{color, marginTop:22}}>1. 개인·경력 기본정보 검토</h3>
      {profile.extractionWarnings?.length > 0 && <div style={{padding:10, background:'#fff7ed', borderRadius:7}}>
        {profile.extractionWarnings.map((w,i)=><p key={i} style={{margin:'3px 0',fontSize:13}}>{w}</p>)}
      </div>}
      {/* STAGE02_7_BIRTHDATE_PATCH */}
      <button hidden type="button" style={{...buttonStyle, marginBottom:12, background:'#166534'}}
        disabled={busy || !draft} onClick={extractBirthDateFromReview}>
        생년월일·만 나이 원문에서 추출
      </button>
      <div style={gridStyle}>
        {profileFields.map(({key,label,example}) => <label key={key} style={labelStyle}>{label}
          <input style={{...inputStyle, marginTop:5}} disabled={busy} value={key === 'birthDate' ? (profile.birthDate || '').replace(/-/g, '.') : (profile[key] || '')}
            placeholder={example} onChange={e=>editProfile(key,e.target.value)} />
        </label>)}
      </div>
      <h3 style={{color, marginTop:22}}>2. 프로젝트 경력 검토 ({profile.projects.length}건)</h3>
      {profile.projects.map((p, index) => <div key={index} style={{padding:14, border:'1px solid #cbd5e1',borderRadius:9,marginBottom:12}}>
        <div style={{display:'flex', gap:12, justifyContent:'space-between', marginBottom:8}}>
          <label><input type="checkbox" checked={p.include !== false} disabled={busy} onChange={e=>editProject(index,{include:e.target.checked})} /> 출력 포함</label>
          <button style={{...buttonStyle, background: '#dc2626', fontWeight: 600, padding: '8px 14px', borderRadius: 7}} data-bwg-ui="delete-project" aria-label="프로젝트 삭제" type="button" disabled={busy} onClick={()=>{setProfile(x=>x ? {...x,projects:x.projects.filter((_,i)=>i!==index)} : x);setDirtyProfile(true);setDownloadUrl('');}}>프로젝트 삭제</button>
        </div>
        <div style={gridStyle}>
          {projectFields.map(({key,label,example}) => <label key={key} style={labelStyle}>{label}
            <input style={{...inputStyle, marginTop:5}} disabled={busy} value={p[key] || ''}
              placeholder={example} onChange={e=>editProject(index,{[key]:e.target.value})} />
          </label>)}
        </div>
      </div>)}
      <button type="button" style={{...buttonStyle,background:'#334155'}} disabled={busy || profile.projects.length>=100}
        onClick={()=>{setProfile(p=>p ? {...p,projects:[...p.projects,emptyProject()]} : p);setDirtyProfile(true);setDownloadUrl('');}}>+ 프로젝트 추가</button>
      <h3 style={{color, marginTop:22}}>3. 고객사 PPTX 템플릿 선택</h3>
      <label style={labelStyle}>기존 템플릿 관리에 등록된 READY 템플릿
        <select style={{...inputStyle, marginTop:5}} value={templateId} disabled={busy} onChange={e=>{setTemplateId(e.target.value);setDownloadUrl('');setConfirmReviewed(false);}}>
          <option value="">템플릿 선택</option>
          {templates.map(t=><option key={t.templateId} value={t.templateId}>
            {t.customerName ? `${t.customerName} · `:''}{t.displayName || t.templateId} ({t.layoutMode || 'READY'})
          </option>)}
        </select>
      </label>
      {templates.length===0 && <p role="alert" style={{color:'#b45309'}}>사용 가능한 READY 템플릿이 없습니다. 템플릿 관리에서 매핑 상태를 확인하세요.</p>}
      <div style={{display:'flex', flexWrap:'wrap', gap:12, alignItems:'center', marginTop:18}}>
        <button type="button" style={buttonStyle} disabled={busy || dirtyRaw || !profile.name.trim()} onClick={()=>void saveStructured()}>검토 내용 PostgreSQL 저장</button>
        <label style={{fontSize:13}}><input type="checkbox" checked={confirmReviewed} disabled={busy || dirtyProfile || dirtyRaw || record?.status !== 'REVIEWED'}
          onChange={e=>setConfirmReviewed(e.target.checked)} /> 미승인 Word 내용을 검토용 PPTX로 생성하는 데 동의합니다.</label>
        <button type="button" style={buttonStyle} disabled={busy || !templateId || dirtyRaw || dirtyProfile || !confirmReviewed || record?.status !== 'REVIEWED'} onClick={()=>void render()}>
          {busy?'처리 중...':'고객사 PPTX 생성'}
        </button>
      </div>
    </>}
    {error && <p role="alert" style={{color:'#b91c1c',whiteSpace:'pre-wrap',marginTop:16}}>{error}</p>}
    {notice && <p role="status" style={{color:'#166534',marginTop:16}}>{notice}</p>}
    {downloadUrl && <p><a href={downloadUrl} style={{color, fontWeight:700}} download>생성된 고객사 PPTX 다운로드</a></p>}
  </section>;
}
