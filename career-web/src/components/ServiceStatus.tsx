import './ServiceStatus.css';

type Check = 'UP' | 'DOWN' | 'UNKNOWN';
type ServiceState = {
  api?: string; postgresql?: string; mysql?: string;
  careerAgent?: string; pythonWorker?: string;
  checkedAt?: string;
};
type Props = { status: ServiceState | null; onRefresh: () => void };

const resolve = (value?: string): Check =>
  value === 'UP' ? 'UP' : value === 'DOWN' ? 'DOWN' : 'UNKNOWN';

export default function ServiceStatus({status, onRefresh}: Props) {
  const services: {label:string; state:Check}[] = [
    {label: 'API', state: resolve(status?.api)},
    {label: 'PostgreSQL', state: resolve(status?.postgresql ?? status?.mysql)},
    {label: 'Agent', state: resolve(status?.careerAgent ?? status?.pythonWorker)},
  ];
  const checkedAt = status?.checkedAt ? new Date(status.checkedAt).toLocaleString('ko-KR') : '미확인';
  return <div className="service-health" aria-label="서비스 상태" title={`마지막 확인: ${checkedAt}`}>
    <span className="service-health__heading">서비스 상태</span>
    {services.map(item => <span key={item.label}
      className={`service-health__item service-health__item--${item.state.toLowerCase()}`}
      title={`${item.label}: ${item.state === 'UP' ? '정상' : item.state === 'DOWN' ? '오류' : '미확인'}`}>
      <span className="service-health__dot" aria-hidden="true" />
      <span>{item.label}</span>
      <span className="sr-only">{item.state === 'UP' ? '정상' : item.state === 'DOWN' ? '오류' : '미확인'}</span>
    </span>)}
    <button type="button" className="service-health__refresh"
      onClick={onRefresh} aria-label="서비스 상태 새로고침" title="상태 새로고침">↻</button>
  </div>;
}
