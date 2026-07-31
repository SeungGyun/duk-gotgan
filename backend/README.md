# 덕!곳간 백엔드

FastAPI + SQLAlchemy + MySQL 8. UI 가 쓰는 REST API 와 저장소입니다.
계약은 [`docs/API.md`](../docs/API.md), 스키마 설계 근거는 [`docs/SPEC.md`](../docs/SPEC.md) §3.

## 띄우기

```bash
cd backend
docker compose up -d                 # MySQL (포트 3307)
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
cp .env.example .env

.venv/bin/python -m scripts.seed     # 목 데이터 적재 (선택)
.venv/bin/uvicorn app.api.main:app --reload --port 8000
```

프론트는 `frontend/.env` 에 `VITE_API=http` 를 두면 vite 프록시를 통해 이 서버를
호출합니다. 값이 없으면 브라우저 메모리 목으로 계속 동작합니다.

## DB 를 왜 따로 띄웠나

같은 머신의 GuruguruCoin 이 이미 MySQL(3306)을 쓰고 있습니다. 거기에 스키마만
추가하면 컨테이너 수명과 볼륨이 묶여서, **저쪽을 내리거나 볼륨을 지울 때 이쪽
데이터도 같이 사라집니다.** 연결 방식(pydantic-settings + SQLAlchemy 엔진/세션 +
information_schema 기반 멱등 마이그레이션)만 그대로 따르고, 컨테이너·포트·볼륨·
계정은 전부 분리했습니다.

| | GuruguruCoin | 덕!곳간 |
|---|---|---|
| 컨테이너 | `guruguru_mysql` | `dukgotgan_mysql` |
| 포트 | 3306 | **3307** |
| 볼륨 | `mysql_data` | `dukgotgan_mysql_data` |
| DB · 계정 | `gurugurucoin` | `dukgotgan` |

## 구조

```
backend/
├─ config/
│  ├─ settings.py     .env → 설정 (pydantic-settings)
│  └─ time.py         저장은 KST naive, 응답은 UTC ISO — 변환을 여기 한 곳에
├─ app/
│  ├─ db/
│  │  ├─ models.py    9개 테이블 (SPEC §3)
│  │  ├─ session.py   엔진·세션·init_db
│  │  └─ migrations.py 매 기동마다 안전한 스키마 보정
│  └─ api/
│     ├─ main.py      앱 조립 (CORS · 오류 핸들러 · 라우터)
│     ├─ errors.py    {"error":{code,message}} 한 규격
│     ├─ serializers.py 행 → 계약(camelCase). 응답 형태를 만드는 유일한 곳
│     └─ routes/      keywords · lectures · stats
├─ scripts/seed.py    목 데이터 적재 (--reset 지원)
└─ data/seed.json     프론트 mock.ts 에서 뽑아낸 시드
```

## 두 층 저장소

| 층 | 테이블 | 성격 |
|---|---|---|
| 임시 | `videos` `transcripts` `evaluations` `pipeline_events` | 파이프라인 중간 산출물. 탈락분 포함 |
| **정식** | `lectures` | AI 가 통과시킨 것만. **UI 읽기는 전부 여기서 끝납니다** |
| 설정·이력 | `keywords` `crawl_runs` `usage_ledger` | |

UI 쿼리에 `WHERE state='PUBLISHED'` 같은 조건을 걸 필요가 없습니다. 조건을
빠뜨려 탈락분이 노출되는 실수를 구조로 막습니다.

## SPEC(PostgreSQL 전제)과 달라진 점

| SPEC | 구현 | 이유 |
|---|---|---|
| `jsonb` | `JSON` | 성격은 같음. 단 정렬·필터에 쓰는 `expert_score`·`verdict`·`duration_sec` 는 JSON 밖 컬럼으로 뺐습니다 — MySQL 은 JSON 컬럼에 인덱스를 못 겁니다 |
| `tsvector` + GIN | `FULLTEXT WITH PARSER ngram` | SPEC 에서 미결이던 한국어 형태소 문제가 ngram(2글자)으로 확장 없이 풀립니다. 1글자 질의만 `LIKE` 폴백 |
| `uuid` | `CHAR(36)` | MySQL 8 에 uuid 타입이 없음 |

## 스키마 변경

Alembic 은 아직 안 씁니다. `Base.metadata.create_all()` 이 **없는 테이블**만 만들고,
이미 있는 테이블에 대한 컬럼 추가·인덱스 생성은 `app/db/migrations.py` 의
`ensure_schema()` 에 information_schema 확인과 함께 넣습니다. 매 기동마다 돌아도
안전해야 합니다.

## 아직 없는 것

수집 파이프라인(문서 3단계)입니다. 그래서 `keywords.status` 는 `pending` 에 머물고,
통계는 대부분 0 입니다. **0 을 감추려고 값을 지어내지 않습니다** — UI 는 0 을 받으면
해당 칩·미터를 숨기도록 만들어져 있습니다.
