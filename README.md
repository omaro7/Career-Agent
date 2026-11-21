# BWG-Career-Agent — Stage 4 React → Spring Boot → Docker Worker PPTX 연동

기존 업로드 소스 `02.BWG-Career-Agent.zip`을 기반으로 추가한 **Stage 4 통합 소스**입니다.
기존 Stage 2의 Excel 등록/파싱/MySQL 저장/프로파일 조회 기능을 보존합니다.

> **소스 기준 주의:** 업로드받은 ZIP은 Stage 2 중심 소스입니다. 사용자 PC에서 Stage 3 화면을 추가로 개발했다면 `web/src/main.tsx`와 스타일을 무조건 덮어쓰지 말고 차이를 확인한 후 새 `PptxExport.tsx`를 병합하세요.

## 1. 개발 범위

| 기능 | 구현 | 구현 위치 |
|---|---|---|
| 등록된 직원 및 프로젝트 목록 | MySQL `employee`, `profile_import`, `career_project` 등 정규화 테이블 조회 | `CareerPptxService.java` |
| 직원 선택 / 프로젝트 선택·정렬 | React 화면 `PPTX 생성` 탭 | `web/src/PptxExport.tsx` |
| 고객사별 제출값 입력 | 소속, 담당업무, 투입기간·투입율, 학위, 기술등급, 수행업무 | React → API 요청 |
| PPTX 전송 | Spring Boot → Worker `/render-customer-pptx` → 바이트 반환 | `CareerPptxController.java`, `worker/app.py` |
| 고객사 원본 PPTX 유지 | `templates/customer-profile.pptx`의 표와 슬라이드 마스터 활용 | `worker/career_pptx.py` |
| 9건 초과 시 페이지 분할 | 첫 장 9건, 이후 페이지당 15건, 2페이지부터 인적사항 제거 | `career_pptx.py` |
| 검토용 프로파일 정책 | `NEEDS_REVIEW` 시 사용자 명시적 확인 후 **검토용** PPTX 생성. DB 승인 상태 변경하지 않음 | React 및 API 양쪽 검증 |

**중요:** 이 패치는 파일 생성과 Python 테스트까지 완료했습니다. 사용자의 Windows Docker Desktop + 실제 MySQL에 대한 Java/React/Docker 통합 실행은 아직 확인되지 않았습니다. 아래 검증 절차로 확인하십시오.

## 2. 파일 배치

Windows 프로젝트 루트:

`D:\06.BWG-AI\02.BWG-Career-Agent`

```text
02.BWG-Career-Agent\
├─ .env                         # 기존 파일 보존 · 이 패키지에 없음
├─ compose.yaml                # worker에 읽기 전용 templates 볼륨 추가
├─ templates\
│  └─ customer-profile.pptx    # 사용자가 보관 중인 고객사 원본 양식
├─ scripts\
│  └─ Verify-Stage4.ps1        # 서비스/조회 확인만 하는 선택 스크립트
├─ api\
│  └─ src\main\java\com\bwg\career\api\
│     ├─ CareerPptxController.java
│     └─ CareerPptxService.java
├─ worker\
│  ├─ app.py                   # 기존 Excel parse API 유지 + 신규 PPTX API
│  ├─ career_pptx.py           # 신규 생성 엔진
│  ├─ requirements.txt         # python-pptx 추가
│  └─ Dockerfile               # career_pptx.py 포함
└─ web\
   └─ src\
      ├─ main.tsx              # 기존 메뉴 보존 + PPTX 생성 탭
      ├─ PptxExport.tsx        # 신규 React 생성 화면
      └─ style.css             # 추가 스타일
```

**Excel 원본은 다시 업로드할 필요가 없습니다.** 현재 Stage 4는 MySQL DB의 정규화된 경력만 조회합니다.
`input/profile.xlsx`, `Generate-Customer-Pptx.ps1`은 실행에 필요하지 않습니다.
고객사 PPTX 원본에 개인정보와 포함된 서체가 있으므로 이 코드 ZIP에는 **실제 PPTX가 포함되어 있지 않습니다.**

## 3. Windows 설치 / 실행

먼저 Docker Desktop을 실행합니다. 프로젝트 백업 후 **Stage 4 패치 ZIP의 상대 경로를 유지한 채** 루트에 덮어씁니다.

```powershell
$Root = 'D:\06.BWG-AI\02.BWG-Career-Agent'
Set-Location $Root

# 기존 환경 및 고객사 템플릿 존재 확인
Test-Path '.\.env'                                # True
Test-Path '.\templates\customer-profile.pptx'   # True

# DB 볼륨은 유지하면서 API / Python Worker / React 이미지 빌드 및 재시작
docker compose up -d --build worker api web

docker compose ps
docker compose logs --tail 80 worker api web

# 신규 API 옵션 조회 검증(직원키 변경 가능)
.\scripts\Verify-Stage4.ps1 -EmployeeKey R000936

Start-Process 'http://localhost:18080'
```

React의 **PPTX 생성** 메뉴에서 직원·고객사 제출값·출력할 프로젝트를 선택하고 **PPTX 생성 및 다운로드** 버튼을 누르면 됩니다.

`docker compose down -v` 및 `docker volume rm` **금지**: MySQL 데이터가 삭제됩니다.
`.env` 및 DB 초기 SQL을 덮어쓸 필요가 없습니다. DB 스키마 변경도 없습니다.

## 4. 주요 API

### 직원 PPTX 출력 대상 조회

`GET /api/v1/career/employees/{employeeKey}/pptx/options`

- 최신 `profile_import`를 기준으로 현재 직원의 정규화 프로젝트를 조회합니다.
- 프로젝트 ID(UUID), 중복 의심, 승인 상태, 경력일자를 반환합니다.
- `REJECTED` / `FAILED` 상태의 최신 등록은 출력 불가합니다.

### 고객사 PPTX 생성

`POST /api/v1/career/employees/{employeeKey}/pptx`

```json
{
  "customerName": "고객사 A",
  "affiliation": "뱅크웨어글로벌",
  "degree": "학사",
  "displayGrade": "특급",
  "businessRole": "Android",
  "assignmentPeriod": "2026.12 ~ 2027.12",
  "allocationRate": "70%",
  "asOfDate": "2026-11-17",
  "confirmDraft": true,
  "selectedProjects": [
    {"projectId": "GET /pptx/options 응답의 실제 프로젝트 UUID", "submittedRole": "", "workDescription": ""}
  ]
}
```

- 선택 순서대로 PPTX에 표시되며, UUID는 반드시 해당 직원의 최신 유효 경력에 있어야 합니다.
- `EXCLUDED` 프로젝트는 출력 불가하며, 중복 프로젝트는 사용자가 직접 선택해야 합니다.
- `submittedRole` 비어 있으면 DB의 역할을 사용합니다.
- `workDescription`이 없으면 빈 칸으로 둡니다. **추측으로 생성하지 않습니다.**
- 개인 기본정보는 현재 `employee` 및 관련 학력/자격증 테이블에서 읽습니다.
- `customerName`은 제출 요청 메타데이터이며 고객사 원본 슬라이드에는 별도 칸이 없습니다.
- 지원되는 현재 고객사 템플릿은 **1종**입니다. 다른 고객사 양식은 매핑 정의/검증 후 추가합니다.
- 서버는 파일을 영구 보관하지 않고 PPTX 바이트를 반환합니다. 다운로드 파일은 브라우저의 다운로드 위치에 저장됩니다.

### Python 내부 엔드포인트

`POST http://worker:8000/render-customer-pptx` (Docker 내부망 전용)

**외부 브라우저는 반드시 Spring Boot API를 통해 요청**합니다. Windows Python 가상환경이나 별도 `.ps1` 생성 작업은 필요 없습니다.

## 5. 검토용 출력과 실제 승인 구분

기존 제공 소스에는 Stage 3 편집/승인 API가 구현되어 있지 않습니다. 따라서 Stage 4에서 이력 진위나 프로젝트 단위 승인 상태를 변경하지 않았습니다.

- `NEEDS_REVIEW`, `PARSED`, 프로젝트 `PENDING`: 사용자가 화면에서 내용을 확인하고 `confirmDraft=true`로 **검토용 PPTX** 생성 가능합니다.
- 이는 **DB `APPROVED`로 승격하는 동작이 아닙니다.** 고객사 최종 제출 전 수동 검수 필수입니다.
- `REJECTED`, `FAILED`, `EXCLUDED`: 출력 차단.
- 기술등급이 Excel '특급'이고 고객사 샘플 '고급'이라도 자동 변경하지 않습니다. 화면에서 별도 선택·확인하세요.
- 나이는 사용자가 지정한 제출 기준일로 재계산합니다.

## 6. 테스트 결과와 환경 제약

- Python: 기존 파서 테스트 + 신규 PPTX 분할·치환·Worker API 테스트 **총 15건 통과** (업로드한 고객사 PPTX를 테스트 템플릿으로 사용).
- 1/9/10/19/24/25건에서 페이지 수와 다음 페이지 인적사항 제거 검증.
- React `.tsx`의 TypeScript 문법 변환 검사 통과. Node 패키지 설치가 불가능하여 `npm run build` 전체 타입 검사는 미완료입니다.
- 이 제작 환경에 Docker Engine/Maven이 없어 실제 Java 빌드 및 MySQL API End-to-End 테스트는 미실행입니다.

프로젝트 PC에서 `docker compose up -d --build worker api web` 과정 중 문제가 발생하면 다음 로그로 진단합니다.

```powershell
docker compose logs --tail 200 api
docker compose logs --tail 200 worker
docker compose logs --tail 200 web
```

## 7. 운영 정책

- 기본 Web 접속은 `127.0.0.1:18080`이며, 외부에 API 포트를 열지 않습니다.
- **인증/인가 시스템이 아직 없습니다.** 내부 로컬 PC 사용으로 제한하십시오.
- `.env`, 개인정보 원본 PPTX, DB 덤프는 Git/공유 패키지에 포함하지 마세요.
- 지금 제공된 코드의 `scripts/Verify-Stage4.ps1`은 **조회 검증용**입니다. PPTX 생성은 React 메뉴에서만 실행하도록 구현했습니다.
