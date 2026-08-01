# API 계약 — UI ↔ 백엔드

> UI가 백엔드에 기대하는 전부입니다. 이 문서와 `frontend/src/api/types.ts`가 같은 내용을
> 서술하며, 타입 정의가 정본입니다.
>
> **현재 상태**: 아래 엔드포인트는 전부 `backend/` 에 구현되어 있습니다.
> `frontend/.env` 에 `VITE_API=http` 를 두면 `http.ts` 가 이 경로들을 호출하고,
> 값이 없으면 `mock.ts`(브라우저 메모리)로 계속 동작합니다.
> 화면 코드는 어느 구현이 붙었는지 모릅니다.

---

## 0. 공통

- 베이스 경로: `/api/v1`
- 요청·응답 모두 `application/json`
- 필드명은 **camelCase** (`minDurationSec`, `expertScore`)
- 시각은 **ISO 8601 문자열** (`2026-07-31T04:02:00Z`), 날짜만 필요한 곳은 `YYYY-MM-DD`
- 시간 길이는 전부 **초 단위 정수**

### 오류 응답

```jsonc
{
  "error": {
    "code": "KEYWORD_DUPLICATE",
    "message": "\"카프카 파티셔닝 전략\" 은(는) 이미 등록되어 있습니다."
  }
}
```

`message`는 **사용자에게 그대로 보여집니다.** UI는 이 문자열을 가공하지 않습니다.
코드가 아니라 사람이 읽을 문장으로, 무엇이 잘못됐고 어떻게 고치는지까지 써 주세요.

| HTTP | 상황 |
|---|---|
| 400 | 입력값 문제 (`TERM_REQUIRED` 등) |
| 404 | 대상 없음 |
| 409 | 중복 (`KEYWORD_DUPLICATE`) |
| 429 / 503 | 쿼터·예산 초과 (`QUOTA_EXCEEDED`, `BUDGET_EXCEEDED`) |

---

## 1. 키워드

### `GET /keywords` → `Keyword[]`

`?archived=true` 를 붙이면 **삭제 영역**(보관된 것)만, 최근 삭제 순으로 돌려줍니다.
기본값은 보관되지 않은 것만입니다.

```jsonc
{
  "id": "kw_1",
  "term": "쿠버네티스 네트워킹",
  "status": "active",           // pending | active | quota_wait | paused | archived
  "language": "ko",             // ko | en | any
  "schedule": "daily",          // daily | twice_weekly | weekly
  "minDurationSec": 900,
  "minExpertScore": 75,
  "maxPerRun": 10,
  "lectureCount": 24,           // 이 키워드로 공개된 강의 수
  "lastRunAt": "2026-07-31T04:02:00Z",   // 아직 안 돌았으면 null
  "createdAt": "2026-05-28T09:10:00Z",
  "archivedAt": null                     // 삭제 영역으로 옮긴 시각
}
```

### `POST /keywords` → `Keyword` (201)

**여기가 직접 붙이실 지점입니다.** 요청 본문:

```jsonc
{
  "term": "카프카 파티셔닝 전략",
  "language": "ko",
  "schedule": "daily",
  "minDurationSec": 900,
  "minExpertScore": 75,
  "maxPerRun": 10
}
```

서버가 할 일:

1. `term` 공백 제거 후 빈 문자열이면 400 `TERM_REQUIRED`
2. 활성 키워드 중 같은 `term`이 있으면 409 `KEYWORD_DUPLICATE`
3. 테이블에 적재하고 **`status`를 `pending`으로** 설정
4. 생성된 `Keyword` 전체를 반환

> `status: "pending"`이 스케줄러의 트리거입니다. UI는 이 상태를 "첫 실행 대기"로 표시하고,
> 사용자에게 "몇 분 안에 첫 수집이 돕니다"라고 알립니다. 수집 파이프라인이 없는 동안에는
> `pending`으로 남아 있어도 UI는 정상 동작합니다.

### `PATCH /keywords/{id}` → `Keyword`

부분 수정. 임계값 변경과 상태 전환 둘 다 이 경로를 씁니다.

```jsonc
{ "minExpertScore": 80 }
{ "status": "paused" }
```

### `DELETE /keywords/{id}` → 204

지우지 않고 **삭제 영역으로 옮깁니다**(`status='archived'`, `archivedAt` 기록).
수집된 강의도, 이 키워드가 데려왔다는 연결도 그대로 둡니다 — 되살렸을 때
"몇 편 모았는지"가 이어져야 복구가 복구다워집니다.

### `POST /keywords/{id}/restore` → `Keyword`

삭제 영역에서 되살립니다. **돌아갈 상태는 서버가 정합니다** — 한 번도 안 돌아본
키워드는 `pending` 으로 보내 첫 수집을 받게 하고, 이미 돌던 것은 `active` 로
되돌려 주기를 이어갑니다. 보관 상태가 아니면 409 `NOT_ARCHIVED`.

같은 검색어를 다시 등록(`POST /keywords`)해도 같은 방식으로 되살아납니다.
새로 만들면 예전에 모은 강의와의 연결이 끊기기 때문입니다.

---

## 2. 강의

### `GET /lectures` → `LectureSummary[]`

쿼리 파라미터 (전부 선택):

| 파라미터 | 예 | 의미 |
|---|---|---|
| `keyword_ids` | `kw_1,kw_2` | 이 중 하나라도 걸린 강의 (OR) |
| `min_score` | `85` | 이 점수 이상 |
| `min_duration_sec` / `max_duration_sec` | `1800` / `5400` | 길이 범위 |
| `q` | `CNI` | 제목·요약·채널·태그 전문 검색 |
| `favorites_only` | `true` | 즐겨찾기만 |
| `sort` | `score` \| `recent` \| `duration` | 기본 `score` |

응답 항목:

```jsonc
{
  "videoId": "aX7kQ2mN9pL",      // 유튜브 video id 를 그대로 PK 로
  "title": "결제 시스템 멱등성 설계: 중복 결제를 막는 네 개의 층",
  "channelTitle": "백엔드 아키텍처 랩",
  "durationSec": 5645,
  "publishedAt": "2026-06-18",
  "expertScore": 92,
  "verdict": "expert",           // expert | practical | introductory | promotional | irrelevant
  "oneLiner": "멱등키의 생명주기부터 …",
  "tags": ["결제", "멱등성"],
  "keyPointOffsets": [682, 1661, 3364, 4470],   // 길이 트랙의 눈금 위치(초)
  "isFavorite": true,
  "keywordIds": ["kw_2"]
}
```

> **`keyPointOffsets`는 목록에서 쓰입니다.** 각 강의의 길이 트랙에 세로 눈금으로 찍혀
> "이 강의는 중반에 밀도가 높다"를 목록 단계에서 보여줍니다. `keyPoints[].timestampSec`
> 값만 뽑아 배열로 주면 됩니다.

### `GET /lectures/{videoId}` → `LectureDetail`

`LectureSummary`의 모든 필드에 더해:

```jsonc
{
  "youtubeUrl": "https://youtu.be/aX7kQ2mN9pL",
  "sections": [                          // 요약의 본체. 개요·핵심포인트·챕터가 여기로 합쳐졌습니다
    { "title": "레거시 원장의 세 가지 문제 — 제각각인 테이블, 도메인 결합, 확장 불가",
      "startSec": 78,
      "bullets": ["첫째, 테이블 구조가 제각각이었다. …", "둘째, 도메인 간 강한 의존성이다. …"] }
  ],
  "closing": "20년 된 결제 원장을 다시 짓는 일의 핵심은 …",   // 글 맨 끝의 한 줄 요약
  "abstract": "섹션 제목을 이어 붙인 문단 (검색·목록용)",
  "targetAudience": "…",
  "prerequisites": ["…"],
  // 아래 다섯은 옛 형식(시드 데이터)에만 있습니다. sections 가 비어 있을 때만 화면에 쓰입니다.
  "keyPoints": [], "chapters": [], "terms": [], "takeaways": [], "quotes": [],
  "coverageNote": null,          // 자막 품질 문제가 있으면 문장, 없으면 null
  "review": {
    "model": "claude-opus-5",
    "promptVersion": "v1",
    "confidence": "high",        // low | medium | high
    "criteria": [
      { "criterion": "structure", "score": 88, "evidence": "자막 인용" }
    ],
    "redFlags": [],
    "speakerCredentials": "결제 도메인 7년",
    "inputTokens": 19240,
    "outputTokens": 3610,
    "turns": 8
  },
  "transcriptExpiresAt": "2026-07-18T00:00:00Z"  // 보관 만료됐으면 null
}
```

`criteria`는 6개 항목 고정: `structure` · `depth` · `evidence` · `authority` · `density` · `commercial`.
`commercial`은 **역방향**(낮을수록 좋음)이라 UI가 다른 색으로 그립니다.

`chapters`의 `endSec - startSec`이 챕터 타임라인 막대의 높이 비율이 됩니다.
챕터가 연속되지 않아도(빈 구간이 있어도) 렌더링은 되지만, 비율이 왜곡되니
가능하면 앞 챕터의 `endSec`과 다음 `startSec`을 맞춰 주세요.

### `PATCH /lectures/{videoId}` → 204

```jsonc
{ "isFavorite": true }
```

---

## 3. 운영

### `GET /stats/overview` → `Overview`

```jsonc
{
  "newToday": 5,
  "totalLectures": 142,
  "weekAdded": 23,
  "avgScore": 81,
  "queued": { "transcript": 6, "review": 3 },
  "funnel": {
    "discovered": 47, "rulePassed": 18,
    "transcribed": 14, "reviewed": 14, "published": 5
  },
  "earlyExitCount": 9,
  "earlyExitSavedInputTokens": 92000,
  "contributions": [
    { "keywordId": "kw_2", "term": "결제 시스템 설계", "published": 2 }
  ],
  "failures": [
    {
      "kind": "transcript",       // transcript | review | quota
      "label": "자막 없음",
      "title": "gRPC 스트리밍 실전 패턴",
      "detail": "03:58 · 자동 자막 미제공 · STT 대기"
    }
  ],
  "lastRunAt": "2026-07-31T04:00:00Z"
}
```

> `earlyExitCount` / `earlyExitSavedInputTokens`는 **통합 검토 호출의 조기 종료가 실제로
> 작동하는지**를 화면에 드러내기 위한 값입니다. 전문성 미달 판정 시 자막을 끝까지 읽지
> 않아 아낀 입력 토큰입니다. 수집 파이프라인을 붙일 때 계측해 주세요.
> 계산할 수 없으면 `0`으로 두면 UI는 해당 칩을 숨깁니다.

### `GET /stats/usage` → `Usage`

```jsonc
{
  "inputTokens": 183000,
  "outputTokens": 24000,
  "dailyLimitTokens": 1000000,   // 상한이 없으면 null → UI가 미터를 숨김
  "youtubeUnits": 2140,
  "youtubeUnitLimit": 10000,
  "resetsAt": "2026-08-01T04:00:00Z"
}
```

### `GET /runs` → `Run[]`

```jsonc
{
  "id": "run_3",
  "label": "키워드 10개 · 정기 실행",
  "trigger": "scheduled",        // initial | scheduled | manual
  "status": "succeeded",         // running | succeeded | partial | failed
  "startedAt": "2026-07-31T04:00:00Z",
  "finishedAt": "2026-07-31T04:24:00Z",
  "stats": { "discovered": 47, "rulePassed": 18, "transcribed": 14, "reviewed": 14, "published": 5 },
  "tokens": 207000,
  "youtubeUnits": 2140,
  "error": null    // 실패 시 사용자에게 보여줄 문장
}
```

`error`는 코드가 아니라 문장으로, **다음에 무슨 일이 일어나는지까지** 씁니다.

`status` 는 `queued` · `running` · `succeeded` · `partial` · `failed` 입니다.
`queued` 는 "지금 실행" 요청이 접수되어 워커를 기다리는 상태입니다.

### `POST /runs` → `Run` (202)

**"지금 실행"** — 요청만 남깁니다. 워커가 다음 틱(1분)에 집어가 주기를 무시하고
활성 키워드를 전부 돌립니다.

여기서 직접 실행하지 않는 이유: 한 사이클이 몇 분씩 걸려 HTTP 요청이 그동안
매달려 있게 되고, 브라우저가 먼저 끊으면 진행 상황을 알 수 없습니다. 요청을
기록으로 남기면 실행 로그에 바로 보이고 워커가 집어가며 상태가 이어집니다.

이미 대기 중인 요청이 있으면 409 `RUN_ALREADY_QUEUED`.

> 좋음: `"자막 수집 5건 연속 429 — 요청이 차단되어 실행을 중단했습니다. 30분 후 자동 재개됩니다."`
> 나쁨: `"HTTP 429 Too Many Requests"`

---

## 4. 붙이는 순서

UI는 각 엔드포인트가 없어도 나머지 화면이 동작하도록 만들어져 있습니다.
가장 작은 것부터 붙일 수 있습니다.

| 순서 | 엔드포인트 | 붙이면 되는 것 |
|---|---|---|
| 1 | `GET /keywords`, `POST /keywords` | 키워드 화면 전체 |
| 2 | `PATCH` / `DELETE /keywords/{id}` | 일시정지·임계값 수정 |
| 3 | `GET /stats/usage` | 상단바 토큰 미터, 쿼터 패널 |
| 4 | `GET /lectures`, `GET /lectures/{id}` | 강의 화면 (수집 파이프라인이 데이터를 채운 뒤) |
| 5 | `GET /stats/overview`, `GET /runs` | 대시보드·실행 로그 |

1번만 붙여도 키워드 등록이 실제 테이블로 들어가고, 나머지 화면은 목 데이터로 계속 볼 수
있습니다. 다만 지금은 `VITE_API`가 전역 스위치라 **하나를 http로 바꾸면 전부 http**입니다.
부분 전환이 필요하면 `frontend/src/api/index.ts`에서 두 구현을 메서드 단위로 섞으면 됩니다.

```ts
// 예: 키워드만 실제 API, 나머지는 목
export const api: Api = { ...mockApi, listKeywords: httpApi.listKeywords, createKeyword: httpApi.createKeyword };
```
