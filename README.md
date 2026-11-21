# Career-Agent v1.0

**Excel·Word 경력 프로파일 관리 및 고객사 PPTX 자동 생성 — 로컬 AI Agent · RAG 통합**

> 문서 기준: 2026-11-21 · `Career-Agent-v1.0-Source-Clean.zip` 정리본  
> 대상 환경: Windows 10/11, Docker Desktop(WSL 2 / Linux containers), Docker Compose v2  
> **이 소스 패키지에는 실제 `.env`와 Docker 볼륨 데이터가 포함되지 않습니다.**

## 1. 개요

Career-Agent는 경력 프로파일의 등록·검토·관리와 고객사별 PPTX 문서 생성을 위한 로컬 실행형 애플리케이션입니다. 기존 Excel 프로파일 처리와 비정형 Word 프로파일 처리 경로를 구분하며, Ollama 기반 로컬 LLM, LangChain/LangGraph, Qdrant를 사용한 AI Agent 및 RAG 기능을 함께 제공합니다.

주요 기능은 다음과 같습니다.

- **Excel 경력 프로파일:** 회사 양식 기반 Excel 입력, 프로파일 파싱·검증, PostgreSQL 저장 및 조회.
- **비정형 Word 프로파일:** Word 파일 업로드, 구조화 데이터 추출 및 검토, AI 재해석, 저장·PPTX 변환.
- **PPTX 생성:** 등록된 프로파일에서 고객사 PPTX 양식을 선택해 생성. 템플릿 등록·매핑 및 그룹 출력 코드 포함.
- **AI Agent:** LangChain·LangGraph를 활용한 경력 정보 분석, 대화 및 수정 제안/승인 경로.
- **RAG:** Ollama 임베딩으로 Qdrant에 경력 정보를 색인하고 유사도 검색 수행. 자동 동기화는 별도 조건을 충족해야 활성화 가능.
- **로컬 우선 운영:** 6개 Docker Compose 서비스로 실행하며, 예시 포트는 `127.0.0.1`에만 바인딩됩니다.

이 README는 **제공된 소스에 실제로 존재하는 파일과 설정**을 기준으로 작성했습니다. 동작 검증과 미검증 사항은 [13. 검증 범위 및 알려진 제약](#13-검증-범위-및-알려진-제약)에 구분했습니다.

## 2. 시스템 아키텍처

```text
[Windows 브라우저]
       │ HTTP localhost:13000 (기본값)
       ▼
[career-web]  React + TypeScript + Vite / Nginx
       │ /api/* 프록시
       ▼
[career-api]  Python FastAPI :8000
       ├──────────────► [career-db] PostgreSQL 17 + pgvector :5432
       │
       └──────────────► [career-agent] Python FastAPI :8001
                                 ├── Excel Parser / Word AI / PPTX
                                 ├── LangChain / LangGraph AI Agent
                                 ├── [career-ollama] LLM · Embeddings :11434
                                 └── [career-qdrant] RAG 벡터 DB :6333
```

`career-web` → `career-api` → `career-agent`의 애플리케이션 경로를 사용하며, 내부 서비스끼리는 Compose 네트워크 `career-net`에서 통신합니다. `career-agent`는 `expose: 8001`로 설정되어 있고 호스트에 직접 포트를 공개하지 않습니다.

### 2.1 Docker Compose 서비스 (6개)

| 서비스 | 기술/역할 | 코드 또는 설정 | 호스트 접근(기본값) |
|---|---|---|---|
| `career-agent` | Python 3.12 FastAPI Worker; Excel/Word, PPTX, LangChain/LangGraph, RAG 연동 | `career-agent/` | 내부 `8001`만 |
| `career-api` | Python 3.12 FastAPI; 프로파일·Word·PPTX·AI API | `career-api/` | `127.0.0.1:18000` |
| `career-db` | PostgreSQL 17 + pgvector | `career-db/`, `pgvector/pgvector:pg17` | `127.0.0.1:15432` |
| `career-web` | React / TypeScript / Vite → Nginx | `career-web/` | `127.0.0.1:13000` |
| `career-ollama` | 로컬 LLM 및 임베딩 모델 실행 | `ollama/ollama:${OLLAMA_VERSION:-0.40.14}` | `127.0.0.1:11435` |
| `career-qdrant` | RAG 벡터 저장소 | `qdrant/qdrant:v1.15.5` | `127.0.0.1:16333` |

호스트 포트는 `.env`의 `WEB_PORT`, `API_PORT`, `POSTGRES_PORT`, `OLLAMA_PORT`, `QDRANT_PORT`로 변경할 수 있습니다. `career-db`, `career-ollama`, `career-qdrant`는 기본 서비스이며 `career-agent`, `career-api`, `career-web`은 Compose의 **`app` 프로파일**을 사용합니다.

### 2.2 Ollama · RAG 기본 모델

| 용도 | 모델 | 설정 |
|---|---|---|
| 로컬 대화/분석 LLM | `qwen3:4b-instruct` | `OLLAMA_MODEL` |
| 임베딩 모델 | `embeddinggemma` | `CAREER_EMBED_MODEL` (코드 기본값) |
| 임베딩 차원 | 768 | `career_rag_indexer.py` 기준 |
| RAG 컬렉션 | `career_profiles_stage03_test` | Compose의 `CAREER_RAG_COLLECTION` 기본값 |

`CAREER_EMBED_MODEL`은 현행 `compose.yml`의 `career-agent.environment`에 명시적으로 전달되어 있지 않습니다. **제공된 Compose 기준 임베딩 모델은 코드 기본값인 `embeddinggemma`입니다.** 이름을 변경하려면 실제 컨테이너 환경 전달 여부와 기존 컬렉션 벡터 차원을 함께 검토하세요.

## 3. 소스 디렉터리

```text
Career-Agent/
├── compose.yml                       # 사용 중인 통합 Docker Compose (6개 서비스)
├── .env.example                      # 환경변수 예시 (실제 .env 아님)
├── .gitignore
├── README.md
├── CLEANUP-REPORT.md                 # 소스 정리 및 검증 보고서
├── career-agent/                     # Worker, Excel/Word, PPTX, AI Agent, RAG
│   ├── Dockerfile
│   ├── app.py
│   ├── career_parser.py
│   ├── career_pptx.py
│   ├── career_ai_graph.py
│   ├── career_rag_indexer.py
│   ├── career_rag_auto_sync.py
│   ├── word_ai_*.py
│   ├── requirements.txt
│   ├── schema/
│   └── tests/
├── career-api/                       # FastAPI / 프로파일·Word·PPTX·AI 라우트
│   ├── Dockerfile
│   ├── app/
│   ├── requirements.txt
│   └── tests/
├── career-db/
│   ├── init/001_extensions.sql        # 새 볼륨 최초 생성 시 확장 기능
│   └── migrations/                   # 수동 검토·적용 SQL 및 이관 검사 도구
├── career-web/                       # React·TypeScript·Vite·Nginx
├── career-ollama/                    # 모델 서비스 운영 안내
├── career-qdrant/                    # 벡터 DB 운영 안내
├── templates/                        # 고객사 PPTX 및 매핑 JSON
├── sql/                              # Word 추가 기능용 SQL
├── scripts/                          # 설치·빌드·시작·점검·중지·DB 백업
├── deploy-windows/                   # Windows 간 오프라인 이전 도구
└── tests/                            # Word/데이터 검증 테스트
```

**정리 정책:** 이전 Java/MySQL 복사본, 개발 중간 산출물, 캐시, 백업 복사본, 생성 PDF·동영상·PPTX는 소스 ZIP에서 제외했습니다. 기존 `templates/`의 서비스 관련 PPTX 양식은 보존했습니다. 변경 내역은 `CLEANUP-REPORT.md` 및 별도 제거 목록 CSV를 참조하세요.

## 4. 설치 전 준비

1. Windows 10/11에 Docker Desktop 설치 및 실행. WSL 2 백엔드와 **Linux containers** 모드를 사용합니다.
2. PowerShell 5.1 이상 및 Docker Compose v2 명령이 동작하는지 확인합니다.
3. 기존 시스템 데이터를 사용할 경우 **원본 프로젝트와 PostgreSQL/Ollama/Qdrant/업로드 볼륨을 먼저 백업**합니다.
4. ZIP을 별도 디렉터리에 압축 해제하여 파일을 검토합니다. 운영 프로젝트에 바로 덮어쓰지 않습니다.
5. 원본 PC의 실제 `.env`를 안전한 경로로 복사하여 프로젝트 루트에 둡니다. `.env.example`은 실제 비밀번호가 아닙니다.

프로젝트 경로 예시는 다음과 같습니다.

```powershell
$Root = 'D:\06.BWG-AI\02.BWG-Career-Agent'
Set-Location $Root

docker --version
docker compose version
docker info --format '{{.OSType}}'  # linux 여부 확인
```

## 5. `.env` 설정

프로젝트 루트의 `.env.example`을 참고해 실제 `.env`를 준비합니다. 기존 운영 시스템 이전 시 **원본 DB와 동일한 접속 정보**를 유지해야 합니다.

```dotenv
# 예시 값일 뿐입니다. 실제 기존 시스템 설정을 사용하세요.
POSTGRES_DB=bwg_career
POSTGRES_USER=bwg_career
BWG_CAREER_DB_PASSWORD=REPLACE_WITH_SOURCE_VALUE
POSTGRES_PASSWORD=REPLACE_WITH_SOURCE_VALUE
POSTGRES_PORT=15432
OLLAMA_PORT=11435
QDRANT_PORT=16333
API_PORT=18000
WEB_PORT=13000
OLLAMA_MODEL=qwen3:4b-instruct

# 선택 항목. 기본값은 compose.yml에서 지정합니다.
CAREER_RAG_AUTO_SYNC=false
CAREER_RAG_COLLECTION=career_profiles_stage03_test
CAREER_RAG_SYNC_INTERVAL_SECONDS=30
CAREER_RAG_SYNC_MAX_SCAN=5000
```

- `career-db`와 API/Agent가 실제 사용하는 비밀번호 변수는 **`BWG_CAREER_DB_PASSWORD`**입니다. `.env.example`의 `POSTGRES_PASSWORD`가 동일하게 들어 있어도 Compose는 이 변수를 `career-db`의 비밀번호로 직접 사용하지 않습니다.
- `.env`는 Git이나 공개 ZIP/GHCR에 포함하지 마세요.
- 기존 DB에 연결할 경우 비밀번호를 임의로 변경하지 마세요. DB 인증 및 데이터 접근 문제가 발생할 수 있습니다.
- `CAREER_RAG_AUTO_SYNC`는 기본적으로 `false`입니다. 필요한 스키마와 동기화 상태 테이블을 검증하기 전에는 활성화하지 마세요.

## 6. 기존 Windows PC에서 실행하기 (기존 데이터 유지)

**기존 PostgreSQL·Ollama·Qdrant·업로드 데이터가 이미 Docker named volume에 있는 경우**의 권장 절차입니다.

```powershell
$Root = 'D:\06.BWG-AI\02.BWG-Career-Agent'
Set-Location $Root

# 1) 사전 점검 (.env / 서비스 / 볼륨 / Docker)
.\scripts\01-Preflight.ps1

# 2) 구성 확인
# 통합 compose.yml만 사용하며, 별도의 compose.rag.yml은 사용하지 않습니다.
docker compose --env-file .env -f compose.yml --profile app config --services

# 3) 6개 서비스 빌드·실행
docker compose --env-file .env -f compose.yml --profile app up -d --build

# 4) 상태 / 로그 확인
docker compose --env-file .env -f compose.yml --profile app ps
docker compose --env-file .env -f compose.yml --profile app logs --tail 100 career-api career-agent career-web
```

**`01-Preflight.ps1`의 경고 해석:** 기존 외부 볼륨이 없는 경우 경고를 표시합니다. 기존 데이터 이전이 목적이라면 **빈 볼륨을 새로 만들어 진행하지 말고** 먼저 백업/이전 상태를 확인하세요.

### 6.1 접속 주소

| 기능 | 기본 주소 |
|---|---|
| 웹 화면 | <http://127.0.0.1:13000> |
| API 상태(JSON) | <http://127.0.0.1:18000/api/v1/system/status> |
| 웹 프록시 API 상태 | <http://127.0.0.1:13000/api/v1/system/status> |
| API OpenAPI 문서 | <http://127.0.0.1:18000/docs> |
| Ollama API | <http://127.0.0.1:11435/api/tags> |
| Qdrant 컬렉션 API | <http://127.0.0.1:16333/collections> |

`career-agent`의 내부 FastAPI 문서 경로는 비활성화되어 있으며, 외부 포트도 공개하지 않습니다.

### 6.2 정상 상태 확인

```powershell
Invoke-RestMethod 'http://127.0.0.1:13000/api/v1/system/status'

docker compose --env-file .env -f compose.yml --profile app exec -T career-ollama ollama list

docker compose --env-file .env -f compose.yml --profile app exec -T career-db `
  sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

# 간단한 서비스 상태 점검용 PowerShell 스크립트
.\scripts\status.ps1
.\scripts\Verify-Stage4.ps1
```

`/api/v1/system/status`의 JSON은 `api`, `postgresql`, `careerAgent` 상태를 포함합니다. 호환을 위한 `mysql`, `pythonWorker` 필드가 코드에 남아 있으나 **현행 DB는 PostgreSQL**입니다.

## 7. 완전히 신규 환경에 설치하는 경우

신규 PC에서 기존 데이터를 **이전하지 않고 빈 환경**을 만드는 경우에만 다음 절차를 사용합니다.

```powershell
$Root = 'D:\06.BWG-AI\02.BWG-Career-Agent'
Set-Location $Root

# 주의: 기존 데이터 복구용 명령이 아닙니다.
.\scripts\install.ps1 -InitializeEmptyVolumes
```

이 설치 스크립트는 없던 외부 볼륨 4개를 만들고 Compose를 실행할 수 있지만, **업무 테이블을 자동으로 모두 생성하는 마이그레이션 도구는 아닙니다.** `career-db/init/001_extensions.sql`은 새 PostgreSQL 데이터 디렉터리 최초 생성 시 `vector` 확장만 설정합니다.

신규 데이터베이스에서는 `career-db/migrations/001_postgresql_schema.sql`, `sql/001_word_import.sql` 및 후속 Word/AI SQL을 **의존성·적용 이력 순서에 맞춰 별도 검토 및 적용**해야 합니다. `001_postgresql_schema.sql`은 무조건 재실행 가능한 스크립트가 아니므로 운영 DB에 임의로 반복 적용하지 마세요. 이미 데이터가 존재하는 PC는 해당 파일을 일괄 적용하지 마세요.

## 8. 로컬 LLM · RAG 운영

### 8.1 설치된 Ollama 모델 확인

```powershell
docker compose --env-file .env -f compose.yml --profile app exec -T career-ollama ollama list
```

필요한 모델이 없는 **신규 환경에서만** 모델을 다운로드합니다. 네트워크 연결 및 디스크 공간이 필요합니다.

```powershell
docker compose --env-file .env -f compose.yml --profile app exec -T career-ollama ollama pull qwen3:4b-instruct
docker compose --env-file .env -f compose.yml --profile app exec -T career-ollama ollama pull embeddinggemma
```

모델은 외부 볼륨 `career_ollama_data`에 저장됩니다. `embeddinggemma`는 RAG 검색·색인에서 사용할 768차원 벡터를 생성하도록 코드에 설정돼 있습니다.

### 8.2 RAG의 위치와 동작

1. Excel 또는 Word에서 경력/프로젝트 정보를 구조화합니다.
2. `career_rag_indexer.py`가 경력 JSON에서 색인할 청크를 구성합니다.
3. Ollama `embeddinggemma`로 768차원 임베딩을 생성합니다.
4. `career-qdrant`의 컬렉션에 저장하고 의미 기반 검색에 사용합니다.
5. 경력 AI 대화 경로는 LangChain/LangGraph 및 Ollama를 사용합니다.

RAG 수동 명령은 `career_rag_indexer.py`에서 `preview`, `index`, `search`를 제공하며, 입력 JSON은 **컨테이너 안에서 접근 가능한 경로**여야 합니다. 운영 데이터에 `index` 명령을 실행하기 전에 `preview`와 백업을 권장합니다.

### 8.3 자동 동기화의 현재 제한 (중요)

`CAREER_RAG_AUTO_SYNC=true`로 설정하면 Agent 시작 스크립트에서 백그라운드 동기화 데몬을 실행하도록 구현돼 있습니다. 그러나 `career_rag_auto_sync.py`가 참조하는 **`sql/003_word_rag_sync.sql`은 원본 및 정리본 ZIP에 존재하지 않습니다.**

따라서 데이터베이스에 `public.career_word_rag_sync` 테이블이 이미 생성되어 있는지와 필요한 스키마가 실제로 준비됐는지 검증하기 전에는 **`CAREER_RAG_AUTO_SYNC=false` 유지**가 안전합니다. 이 상태를 'RAG 전체 자동 동기화 검증 완료'로 간주해서는 안 됩니다.

## 9. Excel · Word · PPTX 처리

| 입력/기능 | 현재 처리 위치 | 비고 |
|---|---|---|
| 회사 Excel 경력 파일 | `career-agent/career_parser.py`, `career-api/app/profiles.py` | Word 경로와 분리 |
| 비정형 Word 업로드·검토 | `career-api/app/word_imports.py`, `word_profile.py` | 별도 Word import 데이터 경로 |
| Word AI 재해석 | `career-api/app/word_ai_runs.py`, `career-agent/word_ai_*.py` | Ollama 기반 구조화 보조 |
| 프로파일 기반 PPTX | `career-agent/career_pptx.py`, `career-api/app/pptx.py` | 템플릿 기반 변환 |
| 템플릿 관리/매핑 | `career-api/app/template_registry.py`, `templates/` | 고객사 양식 지원 |
| 그룹 PPTX 출력 | `career-api/app/career_group_pptx.py` | 현행 API·UI 코드 존재 |
| AI 경력 분석/수정 | `career-agent/career_ai_graph.py`, `career-api/app/career_ai_apply.py` | 수정 제안 및 적용 경로 |

웹 프론트엔드에는 대시보드, Word 업로드, AI 경력 챗봇, PPTX 다운로드, 그룹 출력, 템플릿 관리 컴포넌트가 존재합니다. **Word 입력과 회사 Excel 입력은 서로 다른 파싱/등록 경로**를 사용합니다.

### 9.1 템플릿 관리

- `templates/customer-profile.pptx`: 기본 PPTX 템플릿.
- `templates/customer-profile-sinhyup.pptx`, `customer-profile-woori.pptx`: 보존된 고객사 템플릿.
- `templates/customer-profile-woori.mapping.json`: 템플릿 매핑 설정.
- `career-agent`는 `./templates`를 읽기 전용으로, `career-api`는 읽기·쓰기 모드로 마운트합니다.

```powershell
# 컨테이너에서 템플릿 분석 정보 출력
.\scripts\Inspect-Customer-Templates.ps1
```

PPTX 템플릿에는 개인 경력/고객사 정보가 남아 있을 수 있습니다. 공개 저장소나 배포 아카이브로 공유하기 전에 파일별로 검수하세요. `templates/README.md` 일부 과거 설명은 레거시 MySQL/Spring Boot 기준이므로, 현재 실행 구성은 **루트 `README.md`와 `compose.yml`**을 우선합니다.

## 10. 데이터 저장소 및 백업

`compose.yml`은 아래 **외부 Docker named volume 4개**를 사용합니다. 이름을 변경하면 기존 데이터 대신 다른 볼륨을 바라보게 될 수 있습니다.

| Compose 볼륨 키 | 기존 외부 볼륨 이름 | 내용 |
|---|---|---|
| `career_postgres_data` | `bwg-career-agent-stage02_career_postgres_data` | 프로파일·Word·업무 DB |
| `career_ollama_data` | `bwg-career-agent-stage02_career_ollama_data` | 다운로드된 LLM 및 임베딩 모델 |
| `career_qdrant_data` | `bwg-career-agent-stage02_career_qdrant_data` | RAG 컬렉션/벡터 |
| `career_uploads` | `bwg-career-agent-stage02_career_uploads` | 업로드·생성 파일 |

### 10.1 PostgreSQL 논리 백업

```powershell
Set-Location 'D:\06.BWG-AI\02.BWG-Career-Agent'
.\scripts\backup.ps1
```

백업 결과는 기본적으로 프로젝트의 `backups/career-postgres-YYYYMMDD-HHmmss.dump`에 생성됩니다. **DB 논리 백업만으로 Ollama 모델, Qdrant 데이터, 업로드 파일, bind mount PPTX 템플릿은 복원되지 않습니다.** 전체 PC 이전에는 4개 볼륨과 프로젝트 파일을 모두 포함해야 합니다.

### 10.2 데이터 보존 규칙

- **금지:** `docker compose down -v`, `docker volume rm`, 볼륨 이름 변경을 수반한 무검증 Compose 교체.
- **안전한 중지:** `docker compose --env-file .env -f compose.yml --profile app down` (볼륨 삭제 옵션 없음).
- 원본 운영 볼륨은 정리 ZIP에 포함되지 않았고, 소스 정리 과정에서 변경되지 않았습니다.

## 11. 운영 스크립트 및 명령어

| 목적 | 실행 방법 | 비고 |
|---|---|---|
| 사전 검사 | `.\scripts\01-Preflight.ps1` | 외부 볼륨 상태 확인 |
| 이미지 빌드 | `.\scripts\build-images.ps1` | 소스 빌드 서비스 3개 |
| 서비스 실행 | `.\scripts\start.ps1` | 기본 `--build` |
| 이미지 재빌드 없이 시작 | `.\scripts\start.ps1 -NoBuild` | 기존 이미지 필요 |
| 상태 확인 | `.\scripts\status.ps1` | 6개 서비스 |
| 안전한 종료 | `.\scripts\stop.ps1` | `down`만 수행; 볼륨 보존 |
| PostgreSQL 백업 | `.\scripts\backup.ps1` | `pg_dump` 결과 저장 |
| PPTX 템플릿 검사 | `.\scripts\Inspect-Customer-Templates.ps1` | 실행 중 Agent 필요 |
| Web/API 확인 | `.\scripts\Verify-Stage4.ps1` | 시스템 상태 요청 |

직접 Compose 명령을 실행할 때는 아래 패턴을 사용하세요.

```powershell
# 실행 / 상태 / 로그 / 중지
docker compose --env-file .env -f compose.yml --profile app up -d --build
docker compose --env-file .env -f compose.yml --profile app ps
docker compose --env-file .env -f compose.yml --profile app logs --tail 100
docker compose --env-file .env -f compose.yml --profile app down
```

## 12. Windows PC 간 Docker 배포 (별도 가이드)

**Career-Agent — Windows PC 간 Docker 설치가이드**  
패키지: `Career-Agent-Windows-Deploy-v1.0.zip`  
내장 가이드: [`deploy-windows/README-KO.md`](deploy-windows/README-KO.md)

이 패키지는 GitHub/GHCR 없이 **원본 PC의 실행 Docker 이미지 및 4개 named volume을 오프라인으로 이전**할 수 있도록 작성된 PowerShell 도구입니다.

| 순서 | 원본/대상 | 스크립트 | 역할 |
|---|---|---|---|
| 0 | 원본 | `00-Preflight.ps1` | 환경/구성 검증 |
| 1 | 원본 | `01-Export-From-Source.ps1` | 이미지/볼륨/바인드 파일 내보내기 |
| 2 | 대상 | `02-Restore-To-Target.ps1` | 이미지 로드 및 볼륨 복원 |
| 3 | 대상 | `03-Start.ps1` | `compose.transfer.yml` 오버라이드로 `--no-build` 실행 |
| 4 | 대상 | `04-Verify.ps1` | 상태·API·DB·모델 확인 |
| 5 | 대상 | `05-Stop.ps1` | 볼륨 유지하며 서비스 중지 |

**운영 주의:** 대상 PC는 빈 설치 환경이어야 하며, 복구 스크립트는 기존 볼륨을 덮어쓰지 않습니다. 내보내기 시 서비스 일시 중단이 발생합니다. `.env`는 별도로 안전하게 이전해야 합니다. Windows 실기기에서의 End-to-End 복원 테스트는 수행되지 않았으므로 복제 PC에서 먼저 검증하세요.

**원본 `compose.yml`에는 `compose.transfer.yml`이 없습니다.** 이는 Export 결과에 생성되는 **대상 PC 이전 전용 오버라이드 파일**로, 일반 소스 빌드 실행에서는 사용하지 않습니다.

## 13. 검증 범위 및 알려진 제약

### 13.1 정리본 생성 시 수행한 검증

- ZIP 무결성, Compose 6개 서비스, 외부 Docker 볼륨 4개 정의 확인.
- Dockerfile의 COPY 대상 경로 및 Python 소스 52개 문법 검사 통과.
- Worker 단위 테스트: **110 passed / 1 skipped** (환경 의존 테스트 일부 미수행).
- Word/RAG/이관 관련 테스트: **21 passed / 2 skipped**.

### 13.2 별도 검증이 필요한 항목

1. Windows Docker Desktop에서의 실제 전체 빌드/실행, API·UI 회귀 테스트는 **미검증**입니다.
2. `career-api` 전체 테스트는 검사 환경에 `psycopg` 패키지가 없어 실행하지 못했습니다.
3. 기존 PostgreSQL 업무 스키마와 제공된 SQL의 실제 적용 이력은 확인되지 않았습니다.
4. Word → Qdrant 자동 동기화 코드의 참조 SQL `sql/003_word_rag_sync.sql`이 **소스에 없습니다**. 자동 동기화를 켜기 전에 해결해야 합니다.
5. 템플릿 PPTX에 개인/고객사 정보가 포함돼 있는지 최종 검수가 필요합니다.
6. 정리본은 신규 설치 소스를 제공할 뿐 **모델·데이터베이스·Qdrant 컬렉션을 자동 복원하지 않습니다.**
7. 호스트 포트는 `127.0.0.1`에만 열리도록 구성됐습니다. 외부/다중 사용자 공개에는 별도의 인증, TLS, 네트워크 보안 검토가 필요합니다.

## 14. 기본 장애 점검

| 현상 | 우선 확인 사항 |
|---|---|
| `external volume ... not found` | 기존 4개 볼륨 존재 여부 확인. 이전 중이라면 빈 볼륨을 만들지 말 것 |
| 웹 페이지 연결 불가 | `career-web` 상태, `WEB_PORT`, Nginx 로그 확인 |
| API 오류 또는 DB 연결 실패 | `career-db` health, `.env`의 `BWG_CAREER_DB_PASSWORD`, 실제 DB 스키마 확인 |
| AI 응답 없음 | `career-ollama` 실행, `ollama list`, `OLLAMA_MODEL` 다운로드 여부 확인 |
| RAG 검색 실패 | `career-qdrant` 상태, `embeddinggemma`, 컬렉션명/벡터 차원 확인 |
| RAG 자동 동기화 실패 | `CAREER_RAG_AUTO_SYNC=false` 유지 후 `career_word_rag_sync` 테이블·SQL 이력 점검 |
| Word 가져오기 오류 | `career-api` 로그 및 `career_word_import` 관련 SQL 이력 확인 |
| PPTX 생성 오류 | `templates/` 양식·매핑 JSON, `career-agent` 로그 확인 |
| 다른 PC 배포 실패 | `deploy-windows/README-KO.md`, 대상 빈 볼륨·이미지·`.env` 확인 |

## 15. 보안 및 배포 범위

- 실제 `.env`, DB 백업, 원본 고객사 경력 파일과 생성 문서는 개인정보/민감 정보로 취급합니다.
- 공개 Git 저장소에 `templates/*.pptx`와 업로드된 Word/Excel 파일을 게시하지 마세요.
- 이 소스 ZIP은 **6개 서비스의 소스 빌드 패키지**이며, Docker 이미지 자체를 GHCR에 게시하는 자동화 워크플로를 포함하지 않습니다.
- Docker 이미지·DB·모델·벡터 데이터까지 다른 Windows PC에 옮기려면 12장의 오프라인 배포 절차를 이용합니다.
- 원본 소스는 별도 보관하고, 운영 파일 교체 전에 차이점 검토·백업·복제 환경 테스트를 진행하세요.

---

**핵심 운영 원칙:** `compose.yml` 단일 파일 + `.env` + `--profile app`을 사용하고, 기존 4개 외부 볼륨을 보존합니다. **`docker compose down -v`는 실행하지 않습니다.**
