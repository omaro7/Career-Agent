import { useEffect, useRef, useState } from 'react';
import './CareerAiChat.css';

type WordProfile = {
  import_id: string;
  profile_id: string;
  name: string;
  filename: string;
  status: string;
  rag_status: string;
  project_count: number;
  created_at?: string | null;
  history_count: number;
  is_latest: boolean;
};

type Intent = 'search' | 'analysis' | 'edit';
type Phase = 'DRAFT' | 'APPROVED_NOT_APPLIED' | 'APPLIED' | 'CANCELLED';

type Hit = {
  project_id: string;
  project_name: string;
  content?: string;
  score?: number | null;
  revision?: string;
  source_path?: string;
};
type Edit = {
  proposal_id: string;
  source_hash: string;
  project_id: string;
  project_name: string;
  field: string;
  old_value: string;
  new_value: string;
  status: string;
};
type ChatResponse = {
  profile_id: string;
  intent: Intent;
  answer: string;
  hits?: Hit[];
  edit?: Edit | null;
};
type ApplyContract = {
  path?: string;
  configured_body_style?: string;
  atomic_transaction?: boolean;
  apply_enabled?: boolean;
  warning?: string;
  issue?: string | null;
};
type ApplyPreview = {
  proposal_id: string;
  status: string;
  field: string;
  old_value: string;
  new_value: string;
  project_index: number;
  expected_source_hash: string;
  expected_after_hash: string;
  message: string;
};
type ApplyResult = {
  status: string;
  import_id: string;
  profile_id: string;
  employee_key: string;
  qdrant_status?: string;
  message?: string;
  source_hash?: string;
};
type RagStatus = {
  profile_id: string;
  status: 'INDEXED' | 'PENDING' | 'FAILED' | 'NOT_READY';
  up_to_date: boolean;
  current_source_hash: string | null;
  indexed_source_hash: string | null;
  last_indexed_at?: string | null;
};
type SyncTask = {
  profileId: string;
  sourceHash: string;
  projectId: string;
  projectName: string;
  newValue: string;
};
type SyncPhase = 'PENDING' | 'REFRESHING' | 'REFRESHED' | 'INDEXED' | 'FAILED' | 'CONFLICT' | 'TIMEOUT';
type ChatEntry = {
  id: number;
  role: 'user' | 'assistant' | 'notice';
  text: string;
  intent?: Intent;
  hits?: Hit[];
  stale?: boolean;
};
type PendingEdit = { edit: Edit; phase: Phase; preview?: ApplyPreview };

type Props = {
  /** Stable Word employee key. If supplied, the profile is fixed by the parent Word detail page. */
  profileId?: string;
  /** Leave empty when career-web already reverse-proxies /api to career-api. */
  apiBaseUrl?: string;
  /** The parent Word detail page can refresh its data after the DB commit. */
  onApplied?: (event: ApplyResult) => void;
};

const FIELD_OPTIONS = [
  { value: 'submittedRole', label: '담당 역할' },
  { value: 'workDescription', label: '수행 업무 / 성과' },
  { value: 'technologiesText', label: '사용 기술' },
  { value: 'projectName', label: '프로젝트명' },
  { value: 'clientName', label: '고객사' },
  { value: 'periodText', label: '참여 기간' },
] as const;
const WORD_ID = /^word_[0-9a-f]{32}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const HASH = /^[a-f0-9]{64}$/;
const INITIAL_PROMPTS = [
  { text: '금융권 프로젝트 수행 경험을 찾아줘', intent: 'search' as const },
  { text: '금융권 개발 경력의 강점을 근거와 함께 분석해줘', intent: 'analysis' as const },
];

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : '요청 처리 중 오류가 발생했습니다.';
}
function normalizeBase(value: string): string {
  return value.trim().replace(/\/+$/, '');
}
async function readJson<T>(url: string, method: 'GET' | 'POST', data?: object): Promise<T> {
  // The existing local FastAPI proxy returns UTF-8. Do not trust legacy PS console encoding.
  const res = await fetch(url, {
    method,
    credentials: 'same-origin',
    cache: 'no-store',
    ...(method === 'POST' ? { headers: { 'Content-Type': 'application/json; charset=utf-8' }, body: JSON.stringify(data ?? {}) } : {}),
  });
  const raw = await res.text();
  let result: unknown;
  try { result = JSON.parse(raw); }
  catch { throw new Error(`API 응답이 JSON이 아닙니다. (HTTP ${res.status})`); }
  if (!res.ok) {
    const detail = result && typeof result === 'object' && 'detail' in result ?
      (result as { detail?: unknown }).detail : undefined;
    throw new Error(typeof detail === 'string' ? detail : `HTTP ${res.status}: 요청을 처리하지 못했습니다.`);
  }
  return result as T;
}
function scoreText(score: number | null | undefined) {
  return typeof score === 'number' && Number.isFinite(score) ? score.toFixed(3) : '직접 조회';
}
function projectOptions(hits: Hit[]): Hit[] {
  const seen = new Set<string>();
  return hits.filter(hit => {
    if (!hit.project_id || seen.has(hit.project_id)) return false;
    seen.add(hit.project_id);
    return true;
  });
}

export default function CareerAiChat({ profileId, apiBaseUrl = '', onApplied }: Props) {
  const [localProfileId, setLocalProfileId] = useState('');
  const [wordProfiles, setWordProfiles] = useState<WordProfile[]>([]);
  const [profileFilter, setProfileFilter] = useState('');
  const [showHistory, setShowHistory] = useState(false);
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileError, setProfileError] = useState('');
  const [profileRefresh, setProfileRefresh] = useState(0);
  const activeProfileId = profileId || localProfileId;
  const selectedWord = wordProfiles.find(p => p.profile_id === localProfileId);
  const filteredProfiles = wordProfiles.filter(p => {
    const needle = profileFilter.trim().toLocaleLowerCase();
    return !needle || p.profile_id === localProfileId ||
      `${p.name} ${p.filename} ${p.profile_id}`.toLocaleLowerCase().includes(needle);
  });
  const [intent, setIntent] = useState<Intent>('search');
  const [question, setQuestion] = useState('');
  const [editField, setEditField] = useState<string>('submittedRole');
  const [editValue, setEditValue] = useState('');
  const [projectId, setProjectId] = useState('');
  const [hits, setHits] = useState<Hit[]>([]);
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [pending, setPending] = useState<PendingEdit | null>(null);
  const [applyContract, setApplyContract] = useState<ApplyContract | null>(null);
  const [applyContractError, setApplyContractError] = useState<string | null>(null);
  const [checkedCommit, setCheckedCommit] = useState(false);
  const [commitWord, setCommitWord] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [seconds, setSeconds] = useState(0);
  const [syncTask, setSyncTask] = useState<SyncTask | null>(null);
  const [syncPhase, setSyncPhase] = useState<SyncPhase | null>(null);
  const [syncMessage, setSyncMessage] = useState('');
  const [syncRetry, setSyncRetry] = useState(0);
  const lastEntry = useRef<HTMLDivElement>(null);
  const counter = useRef(0);
  const base = normalizeBase(apiBaseUrl);
  const url = (suffix: string) => `${base}/api/v1/career/ai${suffix}`;
  // Unsynced Word documents are visible, but cannot be sent to Qdrant-backed AI chat.
  const canEditProfile = WORD_ID.test(activeProfileId) &&
    (Boolean(profileId) || selectedWord?.rag_status === 'INDEXED');
  const profileSelectionLocked = Boolean(busy) || Boolean(pending &&
    (pending.phase === 'DRAFT' || pending.phase === 'APPROVED_NOT_APPLIED'));
  const projects = projectOptions(hits);
  const canApply = Boolean(
    applyContract?.atomic_transaction === true &&
    applyContract?.configured_body_style === 'raw' &&
    applyContract?.apply_enabled === true &&
    !applyContract?.issue
  );

  function add(role: ChatEntry['role'], text: string, extra: Partial<ChatEntry> = {}) {
    setEntries(prev => [...prev, { id: ++counter.current, role, text, ...extra }]);
  }
  function resetChat() {
    setEntries([]); setHits([]); setPending(null); setError('');
    setQuestion(''); setProjectId(''); setEditValue('');
    setCheckedCommit(false); setCommitWord('');
    setSyncTask(null); setSyncPhase(null); setSyncMessage('');
  }

  // Load only tenant-scoped Word imports; the API does not return Excel imports.
  useEffect(() => {
    if (profileId) return;
    let active = true;
    setProfileLoading(true);
    setProfileError('');
    void readJson<WordProfile[]>(`${normalizeBase(apiBaseUrl)}/api/v1/career/word-imports?include_history=${showHistory}`, 'GET')
      .then(data => {
        if (active) setWordProfiles(Array.isArray(data) ? data : []);
      })
      .catch(e => {
        if (active) { setWordProfiles([]); setProfileError(errorText(e)); }
      })
      .finally(() => { if (active) setProfileLoading(false); });
    return () => { active = false; };
  }, [apiBaseUrl, profileId, profileRefresh, showHistory]);

  function toggleWordHistory(enabled: boolean) {
    if (profileSelectionLocked || profileLoading || enabled === showHistory) return;
    // Never carry old-profile conversation, edit draft or selected project across modes.
    setLocalProfileId('');
    resetChat();
    setWordProfiles([]);
    setShowHistory(enabled);
  }

  function selectWordProfile(nextId: string) {
    if (profileSelectionLocked || nextId === localProfileId) return;
    if (nextId && !wordProfiles.some(p => p.profile_id === nextId)) return;
    setLocalProfileId(nextId);
    resetChat(); // Prevent cross-profile evidence, proposals or conversation leakage.
  }

  // Stage 04-3.3: status-only polling. The backend indexer remains the sole writer
  // to Qdrant. We NEVER report a current index until hashes match.
  useEffect(() => {
    if (!syncTask || activeProfileId !== syncTask.profileId) return;
    let alive = true;
    let timer: number | undefined;
    let attempts = 0;
    const poll = async () => {
      if (!alive) return;
      attempts += 1;
      try {
        const status = await readJson<RagStatus>(
          url(`/profiles/${encodeURIComponent(syncTask.profileId)}/rag-status`), 'GET');
        if (!alive) return;
        if (status.current_source_hash !== syncTask.sourceHash) {
          setSyncPhase('CONFLICT');
          setSyncMessage('저장 이후 Word 원본이 다시 변경되었습니다. 다른 버전이므로 자동 갱신을 중단합니다.');
          return;
        }
        if (status.status === 'INDEXED' && status.up_to_date &&
            status.indexed_source_hash === syncTask.sourceHash) {
          setSyncPhase('REFRESHING');
          setSyncMessage('Qdrant 최신 색인 확인. 검색 근거를 다시 조회하고 있습니다…');
          try {
            // Search uses query embeddings, not the expensive generative LLM.
            // Including the edited project name makes the changed record easier to retrieve.
            const refreshed = await readJson<ChatResponse>(url('/chat'), 'POST', {
              profile_id: syncTask.profileId,
              message: `${syncTask.projectName} ${syncTask.newValue}`,
              intent: 'search', limit: 5,
            });
            if (!alive) return;
            const updated = projectOptions(refreshed.hits || []);
            setHits(updated);
            setEntries(prev => [...prev, {
              id: ++counter.current, role: 'assistant' as const,
              intent: 'search' as const,
              text: `Qdrant 재색인 완료 · 최신 근거 재검색\n${refreshed.answer}`,
              hits: updated,
            }]);
            setSyncPhase('REFRESHED');
            const exact = updated.find(h => h.project_id === syncTask.projectId);
            setSyncMessage(exact
              ? '최신 색인에서 수정한 프로젝트를 다시 찾았습니다. 변경 내용을 검색 근거에서 확인하세요.'
              : '색인은 최신 상태입니다. 다만 이번 검색 상위 5건에서 수정 대상 프로젝트를 찾지 못했습니다. 프로젝트명을 직접 검색하세요.');
            setProfileRefresh(n => n + 1);
          } catch (err) {
            if (!alive) return;
            setSyncPhase('INDEXED');
            setSyncMessage(`Qdrant 재색인은 완료됐지만 자동 재검색에 실패했습니다: ${errorText(err)}. 검색 버튼으로 다시 조회하세요.`);
          }
          return;
        }
        if (status.status === 'FAILED') {
          setSyncPhase('FAILED');
          setSyncMessage('인덱싱 실패 상태가 확인됐습니다. career-agent 로그를 확인하고 다시 동기화하세요.');
          return;
        }
        setSyncPhase('PENDING');
        setSyncMessage(`PostgreSQL 저장 완료 · Qdrant 인덱스 갱신 중 (${attempts}회 확인)`);
      } catch (err) {
        if (!alive) return;
        setSyncPhase('PENDING');
        setSyncMessage(`인덱스 상태 조회 지연: ${errorText(err)} · 다시 확인합니다.`);
      }
      // 8s * 24 checks = ~3 minutes. No unbounded background polling.
      if (attempts >= 24) {
        setSyncPhase('TIMEOUT');
        setSyncMessage('자동 확인 시간이 지났습니다. 저장은 완료됐으며, 인덱스 상태만 다시 확인해 주세요.');
      } else if (alive) {
        timer = window.setTimeout(() => { void poll(); }, 8000);
      }
    };
    void poll();
    return () => { alive = false; if (timer !== undefined) window.clearTimeout(timer); };
  }, [syncTask, syncRetry, apiBaseUrl, activeProfileId]);

  // Refresh the contract when the panel mounts or the backend base URL changes.
  useEffect(() => {
    let cancelled = false;
    setApplyContract(null);
    setApplyContractError(null);
    void readJson<ApplyContract>(`${normalizeBase(apiBaseUrl)}/api/v1/career/ai/apply-contract`, 'GET')
      .then(value => { if (!cancelled) setApplyContract(value); })
      .catch(e => { if (!cancelled) setApplyContractError(errorText(e)); });
    return () => { cancelled = true; };
  }, [apiBaseUrl]);
  useEffect(() => {
    if (!busy) return;
    setSeconds(0);
    const start = Date.now();
    const handle = window.setInterval(() => setSeconds(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => window.clearInterval(handle);
  }, [busy]);
  useEffect(() => { lastEntry.current?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' }); }, [entries.length, pending?.phase]);
  useEffect(() => { resetChat(); }, [profileId]); // A new profile must never reuse a previous profile's project/proposal.

  async function submit() {
    if (busy || pending || !question.trim()) return;
    if (!canEditProfile) { setError('Word 프로파일 ID(word_ + UUID 32자리)를 확인해 주세요.'); return; }
    if (intent === 'edit' && (!projectId || !editValue.trim())) {
      setError('검색 근거에서 프로젝트를 선택하고 변경할 값을 입력해야 합니다.'); return;
    }
    const text = question.trim();
    const selected = intent;
    setQuestion(''); setError('');
    setBusy('AI 요청 처리 중');
    add('user', text, { intent: selected });
    try {
      const result = await readJson<ChatResponse>(url('/chat'), 'POST', {
        profile_id: activeProfileId,
        message: text,
        intent: selected,
        limit: selected === 'analysis' ? 3 : 5,
        ...(selected === 'edit' ? {
          project_id: projectId,
          edit_field: editField,
          new_value: editValue.trim(),
        } : {}),
      });
      add('assistant', result.answer || '응답이 비어 있습니다.', { intent: selected, hits: result.hits || [] });
      if (selected !== 'edit' && result.hits?.length) setHits(projectOptions(result.hits));
      if (result.edit) {
        if (!UUID.test(result.edit.proposal_id) || !HASH.test(result.edit.source_hash)) {
          throw new Error('수정안의 식별자 또는 원본 해시가 올바르지 않습니다. 저장을 진행하지 않습니다.');
        }
        setPending({ edit: result.edit, phase: 'DRAFT' });
        setCheckedCommit(false); setCommitWord('');
      }
    } catch (e) { setError(errorText(e)); add('notice', `요청 오류: ${errorText(e)}`); }
    finally { setBusy(''); }
  }

  async function decide(approve: boolean) {
    if (!pending || pending.phase !== 'DRAFT' || busy) return;
    const draft = pending.edit;
    setError(''); setBusy(approve ? '수정안 승인 처리 중' : '수정안 취소 중');
    try {
      if (!approve) {
        await readJson(url(`/proposals/${draft.proposal_id}/cancel`), 'POST', {});
        setPending({ ...pending, phase: 'CANCELLED' });
        add('notice', '수정안을 취소했습니다. 원본 경력 데이터는 변경되지 않았습니다.');
      } else {
        const approved = await readJson<{ status: string }>(url(`/proposals/${draft.proposal_id}/approve`), 'POST', {
          confirm: true, expected_source_hash: draft.source_hash,
        });
        if (approved.status !== 'APPROVED_NOT_APPLIED') throw new Error('승인 응답 상태가 예상과 다릅니다.');
        setPending({ ...pending, phase: 'APPROVED_NOT_APPLIED' });
        add('notice', '수정안 승인 완료. 아직 PostgreSQL 원본에는 저장되지 않았습니다.');
        try {
          const preview = await readJson<ApplyPreview>(url(`/proposals/${draft.proposal_id}/apply-preview`), 'GET');
          if (preview.status !== 'APPROVED_NOT_APPLIED' || preview.expected_source_hash !== draft.source_hash ||
              preview.old_value !== draft.old_value || preview.new_value !== draft.new_value ||
              !HASH.test(preview.expected_after_hash)) {
            throw new Error('변경 미리보기와 수정안이 일치하지 않습니다.');
          }
          setPending({ edit: draft, phase: 'APPROVED_NOT_APPLIED', preview });
        } catch (e) { setError(`승인은 완료됐지만 DB 미리보기 검증에 실패했습니다: ${errorText(e)}`); }
      }
    } catch (e) { setError(errorText(e)); }
    finally { setBusy(''); }
  }

  async function reloadPreview() {
    if (!pending || pending.phase !== 'APPROVED_NOT_APPLIED' || busy) return;
    setBusy('미리보기 재확인 중'); setError('');
    try {
      const value = await readJson<ApplyPreview>(url(`/proposals/${pending.edit.proposal_id}/apply-preview`), 'GET');
      if (value.expected_source_hash !== pending.edit.source_hash || value.old_value !== pending.edit.old_value ||
          value.new_value !== pending.edit.new_value || !HASH.test(value.expected_after_hash)) {
        throw new Error('원본 또는 변경값이 승인 내용과 다릅니다.');
      }
      setPending({ ...pending, preview: value });
    } catch (e) { setError(errorText(e)); }
    finally { setBusy(''); }
  }

  async function commit() {
    if (!pending?.preview || pending.phase !== 'APPROVED_NOT_APPLIED' || busy) return;
    if (!checkedCommit || commitWord !== '저장' || !canApply) {
      setError('읽기 전용 미리보기와 실제 저장 허용 여부를 확인하고, 저장 동의를 입력해 주세요.'); return;
    }
    setError(''); setBusy('PostgreSQL 원자적 저장 중');
    try {
      // Recheck the current backend gate immediately before writing.
      const fresh = await readJson<ApplyContract>(url('/apply-contract'), 'GET');
      setApplyContract(fresh);
      if (fresh.atomic_transaction !== true || fresh.configured_body_style !== 'raw' ||
          fresh.apply_enabled !== true || fresh.issue) {
        throw new Error('백엔드가 실제 저장을 허용하지 않습니다. 저장하지 않았습니다.');
      }
      const stored = await readJson<ApplyResult>(url(`/proposals/${pending.edit.proposal_id}/apply`), 'POST', {
        confirm: true, expected_source_hash: pending.preview.expected_source_hash,
      });
      if (stored.status !== 'APPLIED') throw new Error('저장 결과가 APPLIED가 아닙니다. 상태를 서버에서 확인해 주세요.');
      setPending({ ...pending, phase: 'APPLIED' });
      // Previous answers/evidence refer to the old Word version. Never let them
      // masquerade as current projects while Qdrant catches up.
      setEntries(prev => prev.map(e => e.role === 'assistant' ? { ...e, stale: true } : e));
      setHits([]);
      setProjectId('');
      add('notice', 'PostgreSQL 저장 완료. 기존 검색 근거는 이전 버전으로 표시하고, Qdrant 색인 상태를 확인합니다.');
      if (stored.profile_id !== activeProfileId || !stored.source_hash || !HASH.test(stored.source_hash)) {
        setSyncPhase('CONFLICT');
        setSyncMessage('DB 저장은 완료됐으나 인덱스 확인용 프로파일 ID 또는 해시가 유효하지 않습니다. 수동으로 확인해 주세요.');
      } else {
        setSyncMessage('PostgreSQL 저장 완료 · Qdrant 인덱스 갱신 중');
        setSyncPhase('PENDING');
        setSyncTask({
          profileId: stored.profile_id,
          sourceHash: stored.source_hash,
          projectId: pending.edit.project_id,
          projectName: pending.edit.project_name,
          newValue: pending.edit.new_value,
        });
      }
      try { onApplied?.(stored); }
      catch { add('notice', 'DB 저장은 성공했지만 상위 Word 화면 새로고침에 실패했습니다. 화면을 다시 불러와 주세요.'); }
    } catch (e) { setError(errorText(e)); }
    finally { setBusy(''); setCheckedCommit(false); setCommitWord(''); }
  }

  function chooseHit(hit: Hit) {
    if (!canEditProfile || pending) return;
    setProjectId(hit.project_id);
    setIntent('edit');
    setQuestion(`${hit.project_name} 프로젝트에서 확인된 경력 내용을 수정하고 싶습니다.`);
    setError('');
  }
  function next() {
    if (pending?.phase === 'DRAFT' || pending?.phase === 'APPROVED_NOT_APPLIED') return;
    setPending(null); setQuestion(''); setEditValue(''); setProjectId(''); setCheckedCommit(false); setCommitWord('');
    setIntent('search'); setError('');
    setSyncTask(null); setSyncPhase(null); setSyncMessage('');
  }

  return (
    <section className="bwca-chat" aria-label="BWG Career AI 챗봇">
      <header className="bwca-header">
        <div className="bwca-logo" aria-hidden="true">AI</div>
        <div className="bwca-header-copy">
          <h2>경력 AI 어시스턴트</h2>
          <p>프로젝트 검색 · 경력 분석 · 수정안 승인 · Word DB 반영</p>
        </div>
        <span className="bwca-chip"><span className="bwca-dot" /> Local Agent</span>
      </header>
      <div className="bwca-body">
        <aside className="bwca-sidebar">
          <div className="bwca-block">
            <h3>프로파일</h3>
            {profileId ?
              <div className="bwca-profile-id" title={profileId}>{profileId}</div> :
              <div className="bwca-profile-selector">
                <label className="bwca-field">이름 또는 Word 파일명 검색
                  <input value={profileFilter} onChange={e => setProfileFilter(e.target.value)}
                    placeholder="이름 · 파일명 검색" autoComplete="off" disabled={profileSelectionLocked} />
                </label>
                <label className="bwca-field">Word 프로파일 선택
                  <select value={localProfileId} onChange={e => selectWordProfile(e.target.value)}
                    disabled={profileLoading || profileSelectionLocked || !wordProfiles.length}>
                    <option value="">프로파일을 선택하세요</option>
                    {filteredProfiles.map(p => (
                      <option key={p.profile_id} value={p.profile_id}>
                        {p.name || '이름 미등록'} · {p.filename} [{p.rag_status === 'INDEXED' ? '검색 가능' : '색인 대기'}]{p.is_latest ? ` · 최신${p.history_count > 1 ? ` (${p.history_count}회 등록)` : ''}` : ' · 이전 이력'}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="bwca-history-option">
                  <input type="checkbox" checked={showHistory} disabled={profileLoading || profileSelectionLocked}
                    onChange={e => toggleWordHistory(e.target.checked)} />
                  전체 Word 등록 이력 보기 (기본: 최신 1건)
                </label>
                <div className="bwca-profile-actions">
                  <button type="button" className="bwca-profile-refresh" disabled={profileLoading || profileSelectionLocked}
                    onClick={() => setProfileRefresh(n => n + 1)}>
                    {profileLoading ? '불러오는 중…' : '목록 새로고침'}
                  </button>
                  <span>{wordProfiles.length}건 {showHistory ? '(전체 이력)' : '(최신 대표본)'}</span>
                </div>
                {profileError && <p role="alert" className="bwca-text-danger">목록 조회 실패: {profileError}</p>}
                {!profileLoading && !profileError && wordProfiles.length === 0 &&
                  <p className="bwca-help">등록된 Word 문서가 없습니다. 비정형 Word 등록 메뉴에서 문서를 먼저 올려주세요.</p>}
                {!profileLoading && !profileError && wordProfiles.length > 0 && filteredProfiles.length === 0 &&
                  <p className="bwca-help">검색 조건에 맞는 Word 문서가 없습니다.</p>}
                {selectedWord && <div className="bwca-selected-profile">
                  <strong>{selectedWord.name || '이름 미등록'}</strong>
                  <span title={selectedWord.filename}>{selectedWord.filename}</span>
                  <small>프로젝트 {selectedWord.project_count}건 · RAG: {selectedWord.rag_status}
                    {selectedWord.history_count > 1 ? ` · 같은 이름·파일명 등록 ${selectedWord.history_count}건` : ''}
                    {selectedWord.is_latest ? ' · 최신본' : ' · 이전 이력'}</small>
                  {selectedWord.created_at && <small>등록 일시: {new Date(selectedWord.created_at).toLocaleString('ko-KR')}</small>}
                </div>}
              </div>}
            <p className="bwca-help">{canEditProfile ? '선택된 Word 프로파일에서 검색할 수 있습니다.' :
              selectedWord && selectedWord.rag_status !== 'INDEXED' ? '아직 Qdrant 색인이 완료되지 않았습니다. 색인 후 목록을 새로고침하세요.' :
              '먼저 Word 프로파일을 선택하세요.'}</p>
          </div>
          <div className="bwca-block">
            <h3>대화 기능</h3>
            <div className="bwca-modes" role="group" aria-label="AI 기능 선택">
              {(['search', 'analysis', 'edit'] as const).map(mode => (
                <button key={mode} type="button" className={intent === mode ? 'bwca-mode active' : 'bwca-mode'}
                  disabled={Boolean(busy) || Boolean(pending && pending.phase !== 'CANCELLED' && pending.phase !== 'APPLIED')}
                  onClick={() => setIntent(mode)} aria-pressed={intent === mode}>
                  <span>{mode === 'search' ? '⌕' : mode === 'analysis' ? '▤' : '✎'}</span>
                  {mode === 'search' ? '경력 검색' : mode === 'analysis' ? 'AI 경력 분석' : '수정안 작성'}
                </button>
              ))}
            </div>
          </div>
          <div className="bwca-block">
            <h3>DB 저장 상태</h3>
            {applyContractError ? <p className="bwca-text-danger">저장 API 확인 실패: {applyContractError}</p> :
              !applyContract ? <p className="bwca-muted">저장 API 연결 확인 중…</p> :
              <><p className={canApply ? 'bwca-text-success' : 'bwca-muted'}>
                {canApply ? '원자적 저장 사용 가능' : '실제 저장 비활성화'}</p>
                <p className="bwca-help">원자적 트랜잭션: {applyContract.atomic_transaction ? '확인됨' : '미확인'} · 방식: {applyContract.configured_body_style || '미확인'}</p></>}
            <p className="bwca-help">승인과 DB 저장은 별도 동작입니다. 실제 저장은 로컬 테스트용 설정이 켜져야 가능합니다.</p>
          </div>
          <div className="bwca-sidebar-bottom">기존 Word/Excel·PPTX 기능은 유지됩니다.<br />이 화면은 사용자 경력의 사실 여부를 대신 확정하지 않습니다.</div>
        </aside>
        <div className="bwca-main">
          <div className="bwca-conversation" role="log" aria-live="polite" aria-label="챗봇 대화 내역">
            {entries.length === 0 && <div className="bwca-welcome">
              <div className="bwca-sparkle">✦</div>
              <h3>경력정보에 대해 무엇이든 물어보세요.</h3>
              <p>저장된 프로젝트 근거를 검색하거나 분석하고, 수정할 프로젝트를 직접 선택할 수 있습니다.</p>
              <div className="bwca-suggestions">
                {INITIAL_PROMPTS.map(p => <button key={p.text} type="button" disabled={Boolean(busy) || !canEditProfile}
                  onClick={() => { setIntent(p.intent); setQuestion(p.text); }}>{p.text}</button>)}
              </div>
            </div>}
            {entries.map(entry => <div key={entry.id} className={`bwca-entry ${entry.role}${entry.stale ? ' bwca-stale' : ''}`}>
              <div className="bwca-avatar" aria-hidden="true">{entry.role === 'user' ? '나' : entry.role === 'notice' ? '!' : 'AI'}</div>
              <div className="bwca-message">
                <div className="bwca-message-label">{entry.role === 'user' ? '사용자' : entry.role === 'notice' ? '상태 안내' : entry.intent === 'analysis' ? 'AI 경력 분석' : '경력 AI'}</div>
                {entry.stale && <div className="bwca-stale-banner">이전 버전의 답변 · 현재 경력정보로 사용하지 마세요</div>}
                <div className="bwca-message-text">{entry.text}</div>
                {entry.hits && entry.hits.length > 0 && <div className="bwca-evidence">
                  <div className="bwca-evidence-title">검색 근거 · {entry.hits.length}건</div>
                  {entry.hits.map((h, i) => <div key={`${h.project_id}-${i}`} className="bwca-evidence-item">
                    <div className="bwca-evidence-top"><strong>[근거 {i + 1}] {h.project_name || h.project_id}</strong>
                      <span>점수 {scoreText(h.score)}</span></div>
                    <p>{h.content || '프로젝트 상세 텍스트 없음'}</p>
                    <button type="button" disabled={entry.stale || Boolean(pending) || !canEditProfile || Boolean(busy)}
                      onClick={() => chooseHit(h)}>이 프로젝트 수정</button>
                  </div>)}
                </div>}
              </div>
            </div>)}
            {pending && <div className="bwca-proposal" aria-label="AI 수정안 검토 카드">
              <div className="bwca-proposal-head">
                <div><span className="bwca-eyebrow">AI 수정안</span><h3>{pending.edit.project_name}</h3></div>
                <span className="bwca-status">{pending.phase === 'DRAFT' ? '승인 대기' : pending.phase === 'APPROVED_NOT_APPLIED' ? '승인됨 · 미저장' : pending.phase === 'APPLIED' ? 'DB 저장 완료' : '취소됨'}</span>
              </div>
              <p className="bwca-small">대상: {pending.edit.project_id} · 필드: {FIELD_OPTIONS.find(f => f.value === pending.edit.field)?.label || pending.edit.field}</p>
              <div className="bwca-diff"><div><span>변경 전</span><p>{pending.edit.old_value || '(빈 값)'}</p></div>
                <div><span>변경 후</span><p>{pending.edit.new_value}</p></div></div>
              {pending.phase === 'DRAFT' && <><p className="bwca-help">내용이 실제 경력과 일치하는지 반드시 확인하세요. 승인만으로 DB는 수정되지 않습니다.</p>
                <div className="bwca-actions"><button className="bwca-primary" disabled={Boolean(busy)} onClick={() => void decide(true)}>수정안 승인</button>
                  <button className="bwca-secondary" disabled={Boolean(busy)} onClick={() => void decide(false)}>취소</button></div></>}
              {pending.phase === 'APPROVED_NOT_APPLIED' && <div className="bwca-commit">
                <div className="bwca-commit-heading"><strong>PostgreSQL 저장 전 미리보기</strong>
                  <button className="bwca-link" disabled={Boolean(busy)} onClick={() => void reloadPreview()}>다시 확인</button></div>
                {pending.preview ? <>
                  <p className="bwca-small">대상 프로젝트 배열 위치: {pending.preview.project_index + 1}번째 · 원본 해시 검증 완료</p>
                  <p className="bwca-help">Word 구조화 데이터와 관계형 경력 테이블을 한 트랜잭션으로 갱신합니다. 기존 Word 검토 상태 및 출력 PPTX는 무효화되며 재생성이 필요합니다.</p>
                </> : <p className="bwca-text-danger">DB 미리보기가 확인되지 않았습니다. 실제 저장을 진행할 수 없습니다.</p>}
                {!canApply ? <p className="bwca-disabled-note">실제 저장 기능이 백엔드에서 비활성화되어 있습니다. 현재는 승인·미리보기까지만 가능합니다.</p> :
                  pending.preview && <>
                    <label className="bwca-confirm"><input type="checkbox" checked={checkedCommit} onChange={e => setCheckedCommit(e.target.checked)} />수정 내용이 실제 경력과 일치하며 DB 저장을 승인합니다.</label>
                    <label className="bwca-field">실제 저장 확인: <strong>저장</strong> 입력
                      <input value={commitWord} onChange={e => setCommitWord(e.target.value)} autoComplete="off" placeholder="저장" /></label>
                    <button type="button" className="bwca-danger" disabled={Boolean(busy) || !checkedCommit || commitWord !== '저장'} onClick={() => void commit()}>PostgreSQL에 실제 저장</button>
                  </>}
              </div>}
              {pending.phase === 'APPLIED' && <div className="bwca-sync-panel" role="status" aria-live="polite">
                <strong>{syncPhase === 'REFRESHED' ? 'DB 저장 완료 · 최신 검색 근거 표시' :
                  syncPhase === 'INDEXED' ? 'DB 저장 완료 · Qdrant 최신화 확인' :
                  syncPhase === 'PENDING' || syncPhase === 'REFRESHING' ? 'DB 저장 완료 · 검색 인덱스 갱신 중' :
                  'DB 저장 완료 · 인덱스 상태 확인 필요'}</strong>
                <p>{syncMessage || '인덱스 상태를 확인하고 있습니다.'}</p>
                {(syncPhase === 'TIMEOUT' || syncPhase === 'FAILED' || syncPhase === 'CONFLICT' || syncPhase === 'INDEXED') &&
                  <button type="button" className="bwca-secondary" onClick={() => {
                    if (syncTask) { setSyncPhase('PENDING'); setSyncRetry(n => n + 1); }
                  }} disabled={!syncTask}>인덱스 상태 다시 확인</button>}
              </div>}
              {(pending.phase === 'APPLIED' || pending.phase === 'CANCELLED') &&
                <button className="bwca-secondary" disabled={pending.phase === 'APPLIED' && syncPhase !== 'REFRESHED' && syncPhase !== 'INDEXED'} onClick={next}>새 질문 시작</button>}
            </div>}
            {busy && <div className="bwca-working" role="status"><span className="bwca-spinner" />{busy} · {seconds}초 경과{intent === 'analysis' ? ' (CPU 환경의 Ollama는 오래 걸릴 수 있습니다)' : ''}</div>}
            {error && <div role="alert" className="bwca-error"><strong>확인 필요</strong> {error}</div>}
            <div ref={lastEntry} />
          </div>
          <form className="bwca-composer" onSubmit={e => { e.preventDefault(); void submit(); }}>
            {intent === 'edit' && <div className="bwca-edit-fields">
              <label className="bwca-field">수정할 프로젝트
                <select value={projectId} onChange={e => setProjectId(e.target.value)} disabled={Boolean(busy) || Boolean(pending)}>
                  <option value="">검색 결과에서 프로젝트를 선택하세요</option>
                  {projects.map(p => <option key={p.project_id} value={p.project_id}>{p.project_name} ({p.project_id})</option>)}
                </select></label>
              <label className="bwca-field">수정 필드
                <select value={editField} onChange={e => setEditField(e.target.value)} disabled={Boolean(busy) || Boolean(pending)}>
                  {FIELD_OPTIONS.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
                </select></label>
              <label className="bwca-field bwca-value-field">새로운 값
                <input value={editValue} maxLength={1200} onChange={e => setEditValue(e.target.value)}
                  disabled={Boolean(busy) || Boolean(pending)} placeholder="검토된 사실만 입력" /></label>
              {!projects.length && <p className="bwca-text-danger">먼저 경력 검색을 실행하고 근거에서 프로젝트를 선택하세요.</p>}
            </div>}
            <div className="bwca-compose-row">
              <label className="bwca-compose-label" htmlFor="bwca-chat-question">질문 또는 수정 요청</label>
              <textarea id="bwca-chat-question" value={question} maxLength={1200} rows={2}
                disabled={Boolean(busy) || Boolean(pending && pending.phase !== 'CANCELLED' && pending.phase !== 'APPLIED')}
                onChange={e => setQuestion(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void submit(); } }}
                placeholder={intent === 'edit' ? '예: 선택한 프로젝트의 담당 역할을 검토한 값으로 수정해줘' : '경력 내용에 대해 질문해 주세요. (Enter 전송 / Shift+Enter 줄바꿈)'} />
              <button className="bwca-send" type="submit" disabled={Boolean(busy) || Boolean(pending) || !canEditProfile || !question.trim() || (intent === 'edit' && (!projectId || !editValue.trim()))}>
                전송 <span aria-hidden="true">↗</span>
              </button>
            </div>
            <p className="bwca-footnote">질문마다 독립 실행됩니다. 화면의 이전 대화는 모델에 자동 전달되지 않습니다. AI 결과는 반드시 경력 원본과 대조하세요.</p>
          </form>
        </div>
      </div>
    </section>
  );
}
