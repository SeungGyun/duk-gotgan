# duk-gotgan (덕!곳간)

키워드를 등록해두면 **AI가 알아서 유튜브에서 좋은 강의를 찾아 요약해 쌓아주는** 개인용 지식 아카이브.

> 유튜브를 뒤지는 시간을 없애고, "읽을 수 있는 강의 노트"만 남긴다.

## 무엇을 하는가

```
[UI] 키워드 등록
  │
  ├─ 1. [스케줄러]      신규 키워드 + 주기 도래 키워드 확인
  │
  ├─ 2. [일반 프로그램]  유튜브 검색 → 후보 리스트 → 룰 필터      ─┐
  │                                                              │ 임시
  ├─ 3. [일반 프로그램]  자막 추출 → 영상별 매칭 저장             ─┤ 저장소
  │                                                              │
  ├─ 4. [스케줄러]      자막 완료분을 AI 워커에 발행              ─┘
  │
  ├─ 5. [AI · 헤드리스 1회]
  │        자막 정리 → 전문성 판정 → (통과 시) 가독성 구조로 변환
  │        └ 마지막에 save_review 도구 호출
  │
  ├─ 6. [save_review 구현부 = 워커 프로세스]
  │        검증 → 판정 기록 → 임계값 판단 → 통과분을 정식 저장소로 이동
  │
  └─ [UI] 정리된 강의 노트 열람 · 검색 · 태그
```

**AI 호출은 영상 1건당 정확히 1회**입니다. 판정과 정리를 한 호출로 묶고, 전문성 미달이면 자막을 끝까지 읽지 않고 종료합니다.

5→6이 한 실행 안에서 이어지지만, **`save_review`의 구현체는 워커 프로세스에 있습니다.** AI는 스키마에 맞는 인자를 넘길 뿐이고 셸도 DB 커넥션도 만지지 않습니다. 사용자에게 보이는 것은 정식 저장소(`lectures`)뿐이라 미완성·탈락 데이터가 노출될 수 없습니다.

## 시스템 구성

| 컴포넌트 | 역할 | 스택 |
|---|---|---|
| **web** | 키워드 관리 UI, 강의 요약 열람 | React + Vite + TypeScript |
| **api** | REST API, 인증, 조회/검색 | FastAPI (Python 3.12) |
| **worker** | 유튜브 수집 · 자막 확보 | Celery + Celery Beat |
| **worker-ai** | 전문성 판정 · 요약 생성 | **Claude Code 헤드리스** (`claude-agent-sdk`) + Node.js |
| **db** | 영속 저장 + 전문 검색 | MySQL 8 (ngram FULLTEXT) |
| **broker** | 작업 큐 · 분산 락 · 캐시 | Redis 7 |

> **AI는 Messages API 직접 호출이 아니라 Claude Code 헤드리스 실행입니다.** Python 프로젝트이므로 `claude -p` subprocess 대신 Agent SDK(`claude-agent-sdk`)를 씁니다. 스키마 강제 출력과 호출 단위 비용 상한(`max_budget_usd`)을 그대로 쓸 수 있고, 인프로세스 도구(`create_sdk_mcp_server`)로 저장까지 한 흐름에 담을 수 있습니다. 대신 Batch API 50% 할인은 사용할 수 없고 **프롬프트 인젝션이 새로운 최상위 리스크**가 됩니다 — 자세한 내용은 [docs/AI-PIPELINE.md](docs/AI-PIPELINE.md).

전부 하나의 `docker-compose.yml`로 뜨는 단일 서버 구성. 파이프라인 단계가 전부 큐 기반이라 나중에 worker만 수평 확장하면 클라우드로 옮길 수 있습니다.

## 개발 단계

프로젝트를 두 갈래로 나눠 진행합니다.

| 단계 | 범위 | 상태 |
|---|---|---|
| **1. 웹 UI** | 4개 화면, 목 데이터로 단독 동작 | 완료 — `frontend/` |
| **2. 저장소 + API** | MySQL 스키마, docs/API.md 전 엔드포인트 | 완료 — `backend/` |
| 3. 수집 에이전트 | 스케줄러 · 유튜브 · 자막 · AI 검토 | **진행 중** — M2 부터 |

UI는 백엔드 없이도 전부 돌아갑니다(목 데이터). 실제 API 로 붙일 때는 `frontend/.env` 의
`VITE_API=http` 한 줄만 바꾸면 됩니다.

```bash
cd backend && docker compose up -d          # MySQL (3307)
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
cp .env.example .env
.venv/bin/python -m scripts.seed            # 목 데이터 적재 (선택)
.venv/bin/uvicorn app.api.main:app --reload --port 8000

cd ../frontend && echo "VITE_API=http" > .env && npm run dev
```

자세한 내용은 [backend/README.md](backend/README.md).

```bash
cd frontend && npm install && npm run dev   # http://localhost:5173
```

## 문서

| 문서 | 내용 | 단계 |
|---|---|---|
| [docs/API.md](docs/API.md) | **UI ↔ 백엔드 계약** — 경로·형태·붙이는 순서 | 1–2 |
| [frontend/README.md](frontend/README.md) | 프론트엔드 구조와 설계 근거 | 1 |
| [docs/mockups/ui.html](docs/mockups/ui.html) | 디자인 시안 (화면별 설계 의도 포함) | 1 |
| [docs/SPEC.md](docs/SPEC.md) | 시스템 구조, 데이터 모델, 파이프라인 상태 머신 | 3 |
| [docs/AI-PIPELINE.md](docs/AI-PIPELINE.md) | LLM 설계 — 루브릭, 출력 스키마, 비용, 보안 | 3 |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 마일스톤, 리스크, 미결정 사항 | 3 |

> SPEC·AI-PIPELINE·ROADMAP은 **3단계(수집 에이전트) 문서**입니다. 지금 작업 범위 밖이지만
> 나중에 필요하므로 남겨 둡니다. 그 안의 인증 결정("구독 먼저 실측")도 3단계 사항입니다.

## 빠른 시작 (예정 구조)

```bash
cp .env.example .env      # ANTHROPIC_API_KEY, YOUTUBE_API_KEY 입력
docker compose up -d      # db, redis, api, worker, beat, web
docker compose exec api alembic upgrade head
open http://localhost:5173
```

## 필수 환경 변수

| 변수 | 설명 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude Code 헤드리스 인증 (비대화형 실행의 표준 경로) |
| `YOUTUBE_API_KEY` | YouTube Data API v3 키 (검색 · 메타데이터) |
| `DATABASE_URL` | `postgresql+psycopg://gotgan:...@db:5432/gotgan` |
| `REDIS_URL` | `redis://redis:6379/0` |
| `DAILY_LLM_BUDGET_USD` | 일일 LLM 사용량 상한 (초과 시 파이프라인 자동 정지) |
| `DAILY_YOUTUBE_QUOTA_UNITS` | 일일 YouTube 쿼터 상한 (기본 10000) |
| `REVIEW_MODEL` | 판정+정리 통합 호출 모델. 기본 `claude-opus-5` |
| `REVIEW_EFFORT` | 기본 `medium` |
| `JOBS_DIR` | AI 워커 격리 워크스페이스 (기본 `/var/lib/gotgan/jobs`) |

> **인증은 M0에서 확정해야 합니다.** 헤드리스라고 해서 API 비용이 사라지지는 않습니다 — `ANTHROPIC_API_KEY`로 돌리면 과금은 API 직접 호출과 동일한 토큰 단가입니다. 구독 인증이 컨테이너 환경에서 가능한지, 그것이 비용 모델을 바꾸는지 먼저 검증하세요 ([AI-PIPELINE.md §8.3](docs/AI-PIPELINE.md)).

## 프로젝트 구조

```
duk-gotgan/
├─ backend/
│  ├─ src/gotgan/
│  │  ├─ api/          # FastAPI 라우터 · 의존성
│  │  ├─ worker/       # Celery 태스크 정의 · 스케줄
│  │  ├─ pipeline/     # 단계별 도메인 로직 (discover / transcript / review)
│  │  ├─ llm/          # Agent SDK 실행기 · 도구 · 권한 가드 · 프롬프트
│  │  │  ├─ runner.py  #   실행기 (예산·타임아웃·계측·완료 계약)
│  │  │  ├─ tools.py   #   save_review 인프로세스 MCP 도구 (DB 반영 지점)
│  │  │  ├─ guard.py   #   can_use_tool 경로 화이트리스트
│  │  │  ├─ schemas.py #   LectureReview (도구 입력 스키마)
│  │  │  └─ prompts/   #   버전 관리되는 시스템 프롬프트 (.md)
│  │  ├─ youtube/      # Data API · 자막 수집 · STT
│  │  ├─ db/           # SQLAlchemy 모델 · 리포지토리
│  │  └─ core/         # 설정 · 로깅 · 예산 가드
│  ├─ alembic/
│  ├─ Dockerfile           # api · worker · beat
│  ├─ Dockerfile.ai        # worker-ai (+ Node.js + claude CLI)
│  └─ pyproject.toml
├─ frontend/           # React + Vite
├─ docs/
└─ docker-compose.yml
```

## 원칙

1. **자막은 신뢰하지 않는다.** 제3자가 쓴 텍스트를 도구 가진 에이전트에 넣는 구조다. 도구는 `Read`(워크스페이스 내부)와 `save_review`(인프로세스) 둘뿐이고, 셸·파일 쓰기·네트워크는 어떤 경우에도 열지 않는다.
2. **AI에게 능력이 아니라 인터페이스를 준다.** 저장이 필요하면 범용 셸이 아니라 타입 있는 도구 하나를 주고, 구현체는 샌드박스 밖에 둔다. 정책 판단(공개 임계값)도 구현체 쪽에 남긴다.
3. **비용은 상한선 안에서.** 호출 단위(`max_budget_usd`)와 일일 총량, 두 겹으로 막는다.
4. **싼 필터를 먼저.** 룰 필터로 거른 뒤, AI는 탈락 판정 시 자막을 끝까지 읽지 않는다.
5. **원저작자 우선.** 요약본에는 항상 원본 링크·채널·타임스탬프를 붙이고, 자막 원문은 처리용으로만 보관한다.
6. **모든 단계는 재시도 가능.** 영상 단위 상태 머신으로 어디서 실패해도 그 지점부터 재개한다.
