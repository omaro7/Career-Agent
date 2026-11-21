import type { ReactNode } from 'react';

type CareerProfile = {
  employeeKey: string;
  name: string;
  positionTitle?: string | null;
  engineerGrade?: string | null;
};

type Props = {
  profileCount: number;
  profiles: CareerProfile[];
  availableTabs: string[];
  onNavigate: (tab: string) => void;
  onOpenProfile: (employeeKey: string) => void;
};

function Icon({ kind }: { kind: 'profiles' | 'excel' | 'word' | 'pptx' | 'ai' | 'arrow' }) {
  const common = { width: 19, height: 19, fill: 'none', stroke: 'currentColor', strokeWidth: 1.9, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, viewBox: '0 0 24 24', 'aria-hidden': true as const };
  const paths: Record<string, ReactNode> = {
    profiles: <><rect x="3.5" y="4" width="17" height="16" rx="3" /><circle cx="9" cy="10" r="2" /><path d="M5.5 17c.4-2 1.6-3 3.5-3s3.1 1 3.5 3M15 9h3M15 13h3" /></>,
    excel: <><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M4 8h16M9 8v13M14 8v13" /></>,
    word: <><path d="M7 3h7l5 5v13H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"/><path d="M14 3v6h5M9 14h6M9 17h6" /></>,
    pptx: <><rect x="3" y="4" width="18" height="14" rx="2"/><path d="m9 9 6 3-6 3V9ZM12 18v3M8 21h8" /></>,
    ai: <><path d="m12 2 2.5 6.5L21 11l-6.5 2.5L12 20l-2.5-6.5L3 11l6.5-2.5L12 2ZM19 18v4M17 20h4" /></>,
    arrow: <path d="M5 12h14m-6-6 6 6-6 6" />,
  };
  return <svg {...common}>{paths[kind]}</svg>;
}

const actions = [
  { id: 'import', title: 'Excel 프로파일 등록', description: '회사 기본 양식을 분석하고 경력을 등록합니다.', icon: 'excel' as const },
  { id: 'word', title: '비정형 Word 등록', description: '문서에서 경력정보를 추출하고 확인합니다.', icon: 'word' as const },
  { id: 'list', title: '프로파일 조회', description: '저장된 직원과 프로젝트 이력을 조회합니다.', icon: 'profiles' as const },
  { id: 'export', title: 'PPTX 생성', description: '기존 경력정보를 발표용 파일로 출력합니다.', icon: 'pptx' as const },
];

export default function CareerDashboard({ profileCount, profiles, availableTabs, onNavigate, onOpenProfile }: Props) {
  const chatTab = availableTabs.find(t => /^(chat|ai|ai-chat|aiChat|career-ai|careerAi|ai_chat)$/i.test(t));
  return <div className="ca-dashboard">
    <div className="ca-page-heading">
      <div><span className="ca-overline">OVERVIEW</span><h2>Dashboard</h2><p>BWG Career Agent의 경력정보 등록과 조회를 시작합니다.</p></div>
      <button className="ca-btn-primary" type="button" onClick={() => onNavigate('import')}>＋ 새 프로파일</button>
    </div>

    <div className="ca-stats">
      <article className="ca-stat"><span className="ca-stat-icon ca-blue"><Icon kind="profiles"/></span><p>등록된 프로파일</p><strong>{profileCount.toLocaleString('ko-KR')}<small>건</small></strong><span className="ca-stat-description">직원 목록 API 기준</span></article>
      <article className="ca-stat"><span className="ca-stat-icon ca-mint"><Icon kind="excel"/></span><p>지원 입력 형식</p><strong>Excel · Word</strong><span className="ca-stat-description">구조화 / 비정형 문서</span></article>
      <article className="ca-stat"><span className="ca-stat-icon ca-amber"><Icon kind="pptx"/></span><p>경력 출력</p><strong>PPTX</strong><span className="ca-stat-description">템플릿 기반 출력</span></article>
      <article className="ca-stat"><span className="ca-stat-icon ca-violet"><Icon kind="ai"/></span><p>AI 경력 검색</p><strong>{chatTab ? '메뉴 연결' : '연결 정보 없음'}</strong><span className="ca-stat-description">AI 기능은 별도 탭에서 실행</span></article>
    </div>

    <div className="ca-dashboard-columns">
      <section className="ca-widget">
        <div className="ca-widget-heading"><div><h3>빠른 시작</h3><p>수행할 작업을 선택하세요.</p></div></div>
        <div className="ca-action-grid">
          {actions.filter(a => availableTabs.includes(a.id)).map(a => <button key={a.id} className="ca-action" type="button" onClick={() => onNavigate(a.id)}>
            <span className="ca-action-icon"><Icon kind={a.icon}/></span>
            <span className="ca-action-body"><strong>{a.title}</strong><small>{a.description}</small></span>
            <span className="ca-action-arrow"><Icon kind="arrow"/></span>
          </button>)}
          {chatTab && <button className="ca-action" type="button" onClick={() => onNavigate(chatTab)}>
            <span className="ca-action-icon"><Icon kind="ai"/></span><span className="ca-action-body"><strong>AI 경력 챗봇</strong><small>자연어로 프로젝트와 담당 경력을 검색합니다.</small></span><span className="ca-action-arrow"><Icon kind="arrow"/></span>
          </button>}
        </div>
      </section>
      <section className="ca-widget">
        <div className="ca-widget-heading"><div><h3>최근 조회 가능한 프로파일</h3><p>현재 목록의 상위 5건입니다. 등록일 순이 아닙니다.</p></div><button type="button" className="ca-text-btn" onClick={() => onNavigate('list')}>전체 보기</button></div>
        {profiles.length === 0 ? <div className="ca-empty-state">등록된 프로파일이 없습니다.<br/>Excel 또는 Word 파일 등록부터 시작하세요.</div> : <div className="ca-recent-list">{profiles.slice(0, 5).map(person =>
          <button className="ca-recent-person" key={person.employeeKey} type="button" onClick={() => onOpenProfile(person.employeeKey)}>
            <span className="ca-person-avatar">{person.name.trim().slice(0, 1) || 'B'}</span>
            <span className="ca-recent-info"><strong>{person.name}</strong><small>{person.employeeKey} · {person.positionTitle || '직급 미기재'}</small></span>
            <span className="ca-recent-arrow">›</span>
          </button>)}</div>}
      </section>
    </div>
    <section className="ca-workflow" aria-label="업무 처리 흐름"><span>업무 흐름</span><strong>파일 업로드</strong><b>→</b><strong>경력정보 검증</strong><b>→</b><strong>프로파일 조회</strong><b>→</b><strong>PPTX · AI 활용</strong></section>
  </div>;
}
