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

`?archived=true` 를 붙이면 **삭제 영역**(내가 뺀 것)만, 최근 삭제 순으로 돌려줍니다.
기본값은 보관되지 않은 것만입니다. `?mine=false` 면 아직 구독하지 않은 것까지 —
새로 온 사람이 고를 목록이자, 제외한 것을 다시 담는 자리입니다.

> 삭제 영역에는 **수집이 멎은 것과 내가 만든 것만** 올라옵니다. 남이 만들었고
> 아직 도는 키워드는 내가 뺐어도 여기 두지 않습니다 — 그건 제외이고, 한쪽은
> "지웠다" 한쪽은 "돌고 있다" 고 말하게 됩니다.

```jsonc
{
  "id": "kw_1",
  "term": "쿠버네티스 네트워킹",     // 검색어, 또는 채널이면 "@gaingetv"
  "sourceType": "search",         // search | channel
  "channelTitle": null,           // 채널 구독이면 해석된 채널명
  "status": "active",           // pending | active | quota_wait | paused | archived
  "language": "ko",             // ko | en | any
  "schedule": "daily",          // daily | twice_weekly | weekly
  "minDurationSec": 900,
  "minExpertScore": 75,
  "maxPerRun": 10,
  "searchWindowDays": 90,       // 며칠 안에 올라온 것까지 볼 것인가 (1~90)
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
  "maxPerRun": 10,
  "searchWindowDays": 90
}
```

> **`searchWindowDays` 는 키워드마다 다릅니다.** 검색의 `publishedAfter` 이자 룰
> 필터의 "오래됨" 기준입니다. `경제`·`주식` 은 하루만 지나도 헌 이야기라 1일,
> `면역력`·`과학` 은 석 달 전 강의가 그대로 쓸모 있어 90일. **상한은 90(석 달)**
> 이고 넘기면 422 입니다 — 그 위는 새 것을 모으는 일이 아니라 과거를 긁는
> 일이라, 요약 비용이 통째로 그쪽으로 갑니다.
>
> 못 돈 날은 그만큼 더 거슬러 봅니다. 창이 1일인 키워드가 쿼터 대기로 하루를
> 거르면 어제 것은 이미 창 밖인데, 마지막 실행 이후는 무슨 일이 있어도 훑도록
> 바닥을 낮춥니다(그래도 90일은 안 넘습니다). 덕분에 주 1회로 도는 키워드에
> 1일을 넣어도 한 주 치를 다 봅니다.

서버가 할 일:

1. `term` 공백 제거 후 빈 문자열이면 400 `TERM_REQUIRED`
2. 활성 키워드 중 같은 `term`이 있으면 409 `KEYWORD_DUPLICATE`
3. 테이블에 적재하고 **`status`를 `pending`으로** 설정
4. 생성된 `Keyword` 전체를 반환

> `status: "pending"`이 스케줄러의 트리거입니다. UI는 이 상태를 "첫 실행 대기"로 표시하고,
> 사용자에게 "몇 분 안에 첫 수집이 돕니다"라고 알립니다. 수집 파이프라인이 없는 동안에는
> `pending`으로 남아 있어도 UI는 정상 동작합니다.

> **채널 구독** — `POST /keywords` 에 `sourceType: "channel"` 과 `term: "@gaingetv"` 를
> 보내면 핸들을 해석해 등록합니다. 못 찾으면 404 `CHANNEL_NOT_FOUND`.
>
> 검색은 호출당 100유닛인데 **업로드 목록은 1유닛**이라 50배 쌉니다. 관련도 문제도
> 없습니다 — 채널을 직접 골랐으니까요. 대신 조회수 룰은 적용하지 않습니다.

### `GET /channels/blocks` → `ChannelBlock[]`

무관·홍보로 반복해서 걸린 채널은 자동으로 차단됩니다.

```jsonc
{
  "channelId": "UCBJyRmWE_KJZu19EdeupQFA",
  "channelTitle": "슬기로운 스테이블코인 생활",
  "reason": "3번 검토, 한 번도 통과 못 함",
  "auto": true,              // false = 사용자가 직접 막음
  "rejectedCount": 3,
  "createdAt": "2026-08-01T02:17:59Z"
}
```

### `POST /channels/blocks` → `ChannelBlock` (201)

`{ "handle": "@somechannel", "reason": "" }` — 직접 차단. 이미 막혀 있으면 409.

### `DELETE /channels/blocks/{channelId}` → 204

차단을 풉니다. **판정 이력은 지우지 않습니다** — 지우면 다음 탈락 때 처음부터
세기 시작해 같은 채널이 또 자동 차단됩니다. 해제한 채널은 다시 자동 차단되지 않습니다.

### `PATCH /keywords/{id}` → `Keyword`

부분 수정. 임계값 변경과 상태 전환 둘 다 이 경로를 씁니다.

```jsonc
{ "minExpertScore": 80 }
{ "status": "paused" }
```

> **만든 사람만 고칠 수 있습니다.** 설정은 키워드에 붙어 있어 고친 결과가 구독자
> 모두에게 퍼집니다. 남이 만든 것이면 403 `NOT_KEYWORD_AUTHOR` — 일시정지도 이
> 경로라 같이 막힙니다(남의 키워드를 멈추면 그 사람 수집이 통째로 멎습니다).
> **관리자도 예외가 아닙니다.**
>
> 구독(`POST /keywords/{id}/subscribe`)과 제외(`POST /keywords/{id}/exclude`)는
> 내 것만 건드리므로 누구나 됩니다. 응답의 `canEdit` 이 화면과 서버가 함께 보는
> 하나의 근거이고, `createdByName` 은 왜 못 고치는지를 화면이 말할 수 있게 합니다.

### `DELETE /keywords/{id}` → 204

**내가 만든 것만.** 남이 만든 것이면 403 `NOT_KEYWORD_AUTHOR` — 제외로 보냅니다.

지우지 않고 **삭제 영역으로 옮깁니다**(`archivedAt` 기록). 마지막 구독자일 때만
키워드까지 보관됩니다(`status='archived'`) — 아니면 남은 사람이 보고 있는데
수집이 멈춥니다. 수집된 강의도, 이 키워드가 데려왔다는 연결도 그대로 둡니다 —
되살렸을 때 "몇 편 모았는지"가 이어져야 복구가 복구다워집니다.

### `POST /keywords/{id}/exclude` → `Keyword`

남이 만든 키워드를 **내 목록에서만** 뺍니다. 삭제와 갈리는 곳은 하나입니다 —
**수집이 그대로 돕니다.** 만든 사람이 아직 보고 있으니 멈출 이유가 없고, 멈추면
지운 적 없는 사람의 곳간이 말라붙습니다.

그래서 **삭제 영역에도 넣지 않습니다.** 거기 넣으면 "지웠다"고 적어 두고는 계속
수집하는 셈입니다. 다시 담는 자리는 `GET /keywords?mine=false`("다른 사람도 보는
키워드") 한 곳이고, 거기서 `subscribe` 하면 그대로 돌아옵니다.

> **나 혼자 보던 것이었다면** 얘기가 다릅니다. 내가 빠지면 아무도 안 읽는 것을
> 매일 수집하게 되므로 그때는 수집을 멈추고 삭제 영역에 남깁니다 — 되살릴 곳이
> 있어야 하니까요. 어느 쪽이었는지는 응답의 `status` 로 압니다.

`GET /keywords?archived=true`(삭제 영역)에는 **수집이 멎은 것과 내가 만든 것만**
올라옵니다. 남이 만들었고 아직 도는 키워드는 두 곳에서 다른 말을 하게 되므로
빠집니다.

### `POST /keywords/{id}/restore` → `Keyword`

삭제 영역에서 되살립니다. **돌아갈 상태는 서버가 정합니다** — 한 번도 안 돌아본
키워드는 `pending` 으로 보내 첫 수집을 받게 하고, 이미 돌던 것은 `active` 로
되돌려 주기를 이어갑니다. 보관 상태가 아니면 409 `NOT_ARCHIVED`.

같은 검색어를 다시 등록(`POST /keywords`)해도 같은 방식으로 되살아납니다.
새로 만들면 예전에 모은 강의와의 연결이 끊기기 때문입니다.

---

## 2. 강의

### `GET /lectures` → `{ items, total, latestAddedAt }`

쿼리 파라미터 (전부 선택):

| 파라미터 | 예 | 의미 |
|---|---|---|
| `keyword_ids` | `kw_1,kw_2` | 이 중 하나라도 걸린 강의 (OR) |
| `min_score` | `85` | 이 점수 이상 |
| `min_duration_sec` / `max_duration_sec` | `1800` / `5400` | 길이 범위 |
| `q` | `CNI` | 제목·요약·채널·태그 전문 검색 |
| `favorites_only` | `true` | 즐겨찾기만 |
| `sort` | `score` \| `recent` \| `duration` | 기본 `score` |
| `limit` / `offset` | `60` / `120` | 한 쪽의 크기와 시작점. 기본 60, 상한 200 |

> **한 쪽씩 옵니다.** 예전에는 걸린 것을 통째로 줬는데, 809편이 되자 한 번에
> 374KB 였습니다(실측 596ms). 폰에서는 첫 글자가 뜨기까지 그 전부를 기다리는데
> 정작 처음 보이는 것은 열몇 편입니다. 60편이면 28KB · 201ms 입니다.
>
> `total` 과 `latestAddedAt` 은 **쪽이 아니라 걸린 것 전체**를 두고 셉니다.
> 쪽을 나눴다고 "809편"이 "60편"으로 보이면 안 되고, `latestAddedAt` 을 받아 둔
> 쪽에서만 고르면 더 최신인 것이 다음 쪽에 있을 때 기준이 과거로 잡혀
> `GET /lectures/updates` 가 이미 목록에 있는 것을 새 것으로 셉니다.

```jsonc
{
  "items": [ /* LectureSummary … */ ],
  "total": 809,
  "latestAddedAt": "2026-08-05T14:10:57Z"   // 비어 있으면 null
}
```

`items` 의 항목:

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
  "unreadLectures": 37,   // 상단바 메뉴에 붙는 숫자 — 안 본 것만
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

### `GET /stats/pipeline` → `Pipeline`

**지금 무엇을 하고 있고, 왜 그러고 있고, 다음은 언제인가.** 실행 기록(`/runs`)은
지나간 일이라 이 셋 중 어느 것에도 답하지 못합니다.

```jsonc
{
  "funnel": [{ "key": "discovered", "label": "발견", "count": 12 }],
  "tracks": [
    {
      "key": "transcript", "label": "자막",
      "status": "idle",              // running | idle
      "waiting": 55,
      "runLabel": null, "startedAt": null,
      "working": null,               // { title, since } — 지금 붙들고 있는 영상
      "lastAt": "2026-08-19T16:04:38Z",
      "nextAt": "2026-08-19T19:08:44Z",  // 다음에 실제로 무슨 일이 일어나는 시각
      "everySec": 30,                // 확인 주기 (collector/cadence.py)
      "hold": {
        "code": "audio_blocked",
        "tone": "warn",              // info | warn | stop
        "title": "음성 파일을 내려받지 못하고 있습니다",
        "detail": "소리를 받아 직접 받아쓰는 길이 막혔습니다. 그동안은 …",
        "until": "2026-08-19T19:08:44Z",
        "since": null,
        "fix": null                  // 사람이 해야 할 일. 없으면 기다리면 됩니다
      }
    }
  ],
  "reviewers": [
    { "provider": "claude", "label": "클로드",
      "restingUntil": null, "capped": true, "working": null },
    { "provider": "antigravity", "label": "안티그래비티",
      "restingUntil": null, "capped": false,
      "working": { "title": "DNA 계통수 분석과 이명법", "since": "2026-08-19T16:51:17Z" } }
  ],
  "blog": { /* BlogStatus */ },
  "stuck": [{ "key": "failedTranscript", "label": "자막 실패", "count": 0 }]
}
```

`hold` 가 이 응답의 요점입니다. 없으면 막힌 데가 없다는 뜻이고, 있으면 **무엇
때문에 · 언제까지 · 그동안 무슨 일이 벌어지는지**가 문장으로 들어 있습니다.
`tone` 셋을 가르는 기준은 "지금 일이 되고 있는가" 입니다.

| tone | 뜻 | 예 |
|---|---|---|
| `info` | 우회로로 일이 되는 중 | 유튜브 자막이 막혀 소리를 받아 직접 받아쓰는 중 |
| `warn` | 일부가 멎음 — 저절로 풀리지만 처리량이 줌 | 오디오가 막혀 자막 있는 영상만 처리 |
| `stop` | 이 트랙은 아무것도 못 함 | 자막도 소리도 막힘 |

`fix` 는 **사람이 해야 할 일**이 있을 때만 채웁니다. 비어 있으면 기다리면 되는
일입니다 — 이 구분이 없으면 모든 줄이 똑같이 불안하게 읽힙니다.

`nextAt` 은 "다음 확인 시각"이 아니라 **다음에 실제로 무슨 일이 일어나는 시각**
입니다. 검색은 키워드의 차례이고, 자막·요약은 막힌 것이 풀리는 때입니다. 막힌
데가 없으면 `null` 이고, 그때 답이 되는 값은 `everySec` 입니다 — 30초마다 도는
트랙에 "다음 차례 01:45" 를 적어 두면 맞는 말인데도 쓸모가 없습니다.

`reviewers` 는 요약 트랙을 회사별로 펼친 것입니다. **한쪽만 쉬는 것과 둘 다
멎은 것은 완전히 다른 상황인데**, 합쳐서 "요약 쉬는 중" 으로 적으면 그 차이가
사라집니다. `capped` 는 우리가 건 상한이라 올리면 곧바로 재개되고,
`restingUntil` 은 회사 쪽 사정이라 기다리는 수밖에 없습니다.

`working` 은 **지금 그 회사가 쥐고 있는 영상**입니다(`videos.claimed_by` 의 회사
접두사로 가릅니다). 막힘 여부와 따로 보내는 이유: 처음에는 `capped`·`restingUntil`
만 보냈는데, 둘 다 비어 있는 상태를 화면이 "도는 중"으로 읽어 **요약 대기가 0 이라
아무도 아무것도 안 하는 순간에 둘 다 도는 중으로** 떴습니다. 안 막힌 것과 일하는
중인 것은 다릅니다.

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

**트랙 하나를 지금 시작합니다** — 요청만 남깁니다. 워커가 다음 틱에 집어갑니다.

```jsonc
{ "job": "discover" }   // discover | transcript | review | publish. 없으면 discover
```

여기서 직접 실행하지 않는 이유: 한 사이클이 몇 분씩 걸려 HTTP 요청이 그동안
매달려 있게 되고, 브라우저가 먼저 끊으면 진행 상황을 알 수 없습니다. 요청을
기록으로 남기면 실행 로그에 바로 보이고 워커가 집어가며 상태가 이어집니다.

**기다리는 요청은 트랙마다 하나씩입니다.** 같은 트랙을 두 번 누르면 409
`RUN_ALREADY_QUEUED`, 모르는 트랙이면 400 `UNKNOWN_JOB`. 전체에 하나만 두면
검색을 눌러 놓고 요약을 못 누릅니다 — 서로 다른 일인데 한 줄을 두고 다투게 됩니다.

트랙마다 **무엇을 건너뛰는지가 다릅니다.** 공통점은 막힌 것은 건너뛰지 않는다는
것입니다 — 차단·상한·세션은 눌러서 넘길 수 있는 값이 아니고, 두드리면 오히려
길어집니다.

| 트랙 | 눌렀을 때 | 다음 차례는 |
|---|---|---|
| `discover` | 주기를 무시하고 **활성 키워드를 전부** 돕니다 | `last_run_at` 이 갱신돼 **누른 시각 기준**으로 다시 잡힙니다 |
| `transcript` | 30초 주기를 기다리지 않고 한 바퀴. 냉각은 지킵니다 | 주기대로 (30초) |
| `review` | "5건 모일 때까지 기다리기"만 건너뜁니다. 토큰 상한·회사 사정은 그대로 | 주기대로 (1분) |
| `publish` | 30~60분 간격을 건너뛰고 한 편. 하루 상한·세션은 그대로 | 발행 직후 **그 시각부터** 다시 30~60분 |

마지막 칸이 요점입니다. 눌러 놓고 1분 뒤에 정기 실행이 또 도는 것은 눌러 준
사람의 뜻이 아닙니다.

> 좋음: `"자막 수집 5건 연속 429 — 요청이 차단되어 실행을 중단했습니다. 30분 후 자동 재개됩니다."`
> 나쁨: `"HTTP 429 Too Many Requests"`

### `POST /queue/retry` → `{ restored }`

실패한 것을 줄에 다시 세웁니다. **자막·요약은 저절로 다시 시도하지 않습니다** —
다섯 번 해 보고 접은 뒤에는 사람이 손대야 풀립니다.

```jsonc
{ "videoIds": ["abc", "def"] }              // 화면이 고른 것
{ "kind": "review", "onlyRetryable": true } // 무리 전체 중 다시 해 볼 만한 것만
```

`videoIds` 를 주면 그것만, `kind` 만 주면 그 무리 전체입니다. **서버에 "3회 이상"
같은 필터 언어를 두지 않습니다** — 화면이 보여 준 목록과 서버가 고른 목록이 조용히
갈릴 수 있고, 그 차이가 하필 일괄 삭제에서 나타납니다.

돌아갈 줄은 **자막 원문이 남아 있는지**가 정합니다. 요약 실패는 요약 줄로 가야
하지만, 처리가 끝난 상태의 원문은 30일 뒤 지워집니다 — 없으면 자막부터 다시 받습니다.

되살릴 때 `stage: "revive"` 이벤트를 남깁니다. 재시도 횟수를 그 기록 이후로만
세기 때문에(`_retries`), 이 한 줄이 있어야 다섯 번의 기회가 다시 생깁니다.

### `POST /queue/exclude` → `{ excluded }`

되풀이해 실패하는 것을 **완전히 뺍니다.** 본문은 `retry` 와 같습니다.

`SKIPPED`(미리 빼기)와 다릅니다 — 저건 이번엔 넘어간다는 뜻이라 다음 검색에 다시
들어옵니다. 여기 넣은 것(`EXCLUDED`)은 발견 단계가 보고 **다시 데려오지 않습니다.**
한 편이 매 사이클 줄 앞을 차지하며 재시도만 태우는 것을 끊는 자리입니다.

`GET /queue` 의 `failed` 가 그 목록입니다. 항목마다 `attempts`(지금까지 실패한
횟수)와 `retryable` 이 붙습니다. `retryable` 은 **어림짐작**입니다 — 다시 해도 같은
사유(자막이 8자, 영상이 너무 김, 비공개)만 `false` 이고 모르는 실패는 `true`
입니다. 자동으로 아무것도 하지 않고 화면에서 걸러 보는 데만 씁니다.

---

## 4. 사람

`GET /users` · `POST /session` · `POST /users`(만들기) · `DELETE /users/{id}` 는
**로그인 없이** 열립니다 — 선택 화면 자체가 로그인 전 화면입니다. `/me` 계열은
쿠키를 지납니다.

### `DELETE /users/{id}` → `{ removedKeywords, removedLectures }`

사람을 지웁니다. **되돌릴 수 없습니다.** 만드는 자리가 선택 화면이라 지우는
자리도 거기입니다.

```jsonc
// 요청 본문 — 잠긴 사람을 밖에서 지울 때만 씁니다
{ "pin": "1234" }

// 응답: 그 사람이 빠져서 아무도 안 보게 된 키워드와, 그 키워드만 데려왔던 강의
{ "removedKeywords": 2, "removedLectures": 17 }
```

같이 지워지는 것은 세 겹입니다.

| 겹 | 무엇 |
|---|---|
| 그 사람만의 것 | 세션·구독·읽음/즐겨찾기/제외·채널 숨김 |
| 아무도 안 보게 된 키워드 | 그 사람이 빠진 뒤 **구독자 0명**인 것. 한 명이라도 남으면 그대로 돕니다 |
| 그 키워드‘만’ 데려온 영상 | 자막·판정·요약·블로그 이력까지. 다른 키워드도 데려온 영상은 남습니다 |

실행 이력(`crawl_runs`)은 남습니다 — 어제 유닛을 무엇에 썼는지는 키워드가
사라져도 답이 필요한 질문입니다. 티스토리에 이미 올라간 글도 그대로입니다
(지우는 것은 곳간 안의 발행 이력뿐).

문은 들어가는 문과 같습니다. 잠근 사람은 `pin` 이 있어야 하고(없으면 401
`PIN_REQUIRED`, 틀리면 로그인과 똑같이 잠깁니다), 이미 그 사람으로 들어와
있거나 관리자면 묻지 않습니다. **관리자는 못 지웁니다** — 403
`OWNER_UNDELETABLE`. 내 계정을 지우면 그 기기의 쿠키도 함께 지웁니다.

---

## 5. 붙이는 순서

UI는 각 엔드포인트가 없어도 나머지 화면이 동작하도록 만들어져 있습니다.
가장 작은 것부터 붙일 수 있습니다.

| 순서 | 엔드포인트 | 붙이면 되는 것 |
|---|---|---|
| 1 | `GET /keywords`, `POST /keywords` | 키워드 화면 전체 |
| 2 | `PATCH` / `DELETE` / `POST /keywords/{id}/exclude` | 일시정지·임계값 수정, 빼기(삭제·제외) |
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
