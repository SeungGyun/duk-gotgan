import type { Api } from "./contract";
import { ApiError } from "./contract";
import type {
  ChannelBlock,
  Keyword,
  KeywordDraft,
  LectureDetail,
  LectureSummary,
  Overview,
  Person,
  Run,
  Usage,
} from "./types";

/**
 * 브라우저 메모리 구현. 백엔드 없이 UI 전체를 쓰고 확인하기 위한 것입니다.
 * 상태를 실제로 들고 있으므로 키워드를 등록하면 목록·필터에 바로 반영됩니다.
 * (새로고침하면 초기 상태로 돌아갑니다 — 영속은 백엔드의 몫입니다.)
 */

const delay = (ms = 180) => new Promise((r) => setTimeout(r, ms));
const iso = (d: Date) => d.toISOString();
const daysAgo = (n: number) => iso(new Date(Date.now() - n * 864e5));

/** n일 전 h시 m분 (로컬). ISO 문자열에 Z 를 직접 쓰면 표시가 시간대만큼 밀린다. */
const atHour = (n: number, h: number, min = 0) => {
  const d = new Date(Date.now() - n * 864e5);
  d.setHours(h, min, 0, 0);
  return iso(d);
};
/** n일 전 04:mm — 정기 실행은 새벽에 돈다 */
const runAt = (n: number, min = 0) => atHour(n, 4, min);

/** 목에서 "지금 실행"으로 쌓인 요청 — 워커가 없으니 대기 상태로 남습니다 */
let queuedRuns: Run[] = [];

/** 목의 차단 채널 — 자동 차단 예시 하나를 넣어 둡니다 */
let channelBlocks: ChannelBlock[] = [
  {
    channelId: "ch_demo_1",
    channelTitle: "슬기로운 스테이블코인 생활",
    reason: "3번 검토, 한 번도 통과 못 함",
    auto: true,
    rejectedCount: 3,
    createdAt: daysAgo(1),
  },
];

// ── 사람 ──────────────────────────────────────────────────
// 목에서도 선택 화면이 그대로 돌아야 합니다 — 백엔드 없이 UI 를 만지는
// 흐름이 이 프로젝트의 기본이라, 로그인만 예외로 두면 그 흐름이 깨집니다.
let people: Person[] = [
  { id: "u_1", name: "관리자", isOwner: true, hasPin: true, lectureCount: 41 },
  { id: "u_2", name: "아내", isOwner: false, hasPin: false, lectureCount: 12 },
];
let current: Person | null = people[0]!;

let seq = 100;
const nextId = (p: string) => `${p}_${++seq}`;

// ── 시드: 키워드 ───────────────────────────────────────────
let keywords: Keyword[] = [
  {
    id: "kw_1",
    term: "쿠버네티스 네트워킹",
    sourceType: "search" as const,
    channelTitle: null,
    status: "active",
    language: "ko",
    schedule: "daily",
    minDurationSec: 900,
    minExpertScore: 75,
    maxPerRun: 10,
    lectureCount: 24,
    lastRunAt: runAt(0, 2),
    createdAt: daysAgo(64),
    isMine: true,
    subscriberCount: 1,
    canEdit: true,
    createdByName: "관리자",
    archivedAt: null,
  },
  {
    id: "kw_2",
    term: "결제 시스템 설계",
    sourceType: "search" as const,
    channelTitle: null,
    status: "active",
    language: "ko",
    schedule: "daily",
    minDurationSec: 1200,
    minExpertScore: 80,
    maxPerRun: 10,
    lectureCount: 17,
    lastRunAt: runAt(0, 5),
    createdAt: daysAgo(51),
    isMine: true,
    subscriberCount: 1,
    canEdit: true,
    createdByName: "관리자",
    archivedAt: null,
  },
  {
    id: "kw_3",
    term: "PostgreSQL 튜닝",
    sourceType: "search" as const,
    channelTitle: null,
    status: "active",
    language: "ko",
    schedule: "twice_weekly",
    minDurationSec: 600,
    minExpertScore: 70,
    maxPerRun: 10,
    lectureCount: 31,
    lastRunAt: runAt(1, 11),
    createdAt: daysAgo(88),
    isMine: true,
    subscriberCount: 1,
    canEdit: true,
    createdByName: "관리자",
    archivedAt: null,
  },
  {
    id: "kw_4",
    term: "브라우저 성능",
    sourceType: "search" as const,
    channelTitle: null,
    status: "active",
    language: "ko",
    schedule: "daily",
    minDurationSec: 900,
    minExpertScore: 70,
    maxPerRun: 10,
    lectureCount: 28,
    lastRunAt: runAt(0, 8),
    createdAt: daysAgo(40),
    isMine: true,
    subscriberCount: 1,
    canEdit: true,
    createdByName: "관리자",
    archivedAt: null,
  },
  {
    id: "kw_5",
    term: "옵저버빌리티",
    sourceType: "search" as const,
    channelTitle: null,
    status: "quota_wait",
    language: "ko",
    schedule: "daily",
    minDurationSec: 900,
    minExpertScore: 75,
    maxPerRun: 10,
    lectureCount: 9,
    lastRunAt: runAt(0, 20),
    createdAt: daysAgo(22),
    isMine: true,
    // 아내가 만든 것을 내가 구독한 경우 — 읽기는 되고 수정은 안 되는 상태를
    // 목에서도 볼 수 있게 하나 둡니다.
    subscriberCount: 2,
    canEdit: false,
    createdByName: "아내",
    archivedAt: null,
  },
  {
    id: "kw_6",
    term: "LLM 서빙 최적화",
    sourceType: "search" as const,
    channelTitle: null,
    status: "pending",
    language: "ko",
    schedule: "daily",
    minDurationSec: 900,
    minExpertScore: 75,
    maxPerRun: 10,
    lectureCount: 0,
    lastRunAt: null,
    createdAt: daysAgo(0),
    isMine: true,
    subscriberCount: 1,
    canEdit: true,
    createdByName: "관리자",
    archivedAt: null,
  },
  {
    id: "kw_7",
    term: "프론트엔드 렌더링",
    sourceType: "search" as const,
    channelTitle: null,
    status: "paused",
    language: "ko",
    schedule: "daily",
    minDurationSec: 900,
    minExpertScore: 70,
    maxPerRun: 10,
    lectureCount: 28,
    lastRunAt: runAt(19, 4),
    createdAt: daysAgo(120),
    isMine: true,
    subscriberCount: 1,
    canEdit: true,
    createdByName: "관리자",
    archivedAt: null,
  },
];

// ── 시드: 강의 ─────────────────────────────────────────────
interface Seed extends LectureSummary {
  detail: Omit<LectureDetail, keyof LectureSummary>;
}

const lectures: Seed[] = [
  {
    videoId: "aX7kQ2mN9pL",
    title: "결제 시스템 멱등성 설계: 중복 결제를 막는 네 개의 층",
    channelTitle: "백엔드 아키텍처 랩",
    durationSec: 5645,
    publishedAt: "2026-06-18",
    expertScore: 92,
    verdict: "expert",
    oneLiner:
      "멱등키의 생명주기부터 정산 대사까지, 실제 장애 사례를 따라가며 방어 계층을 하나씩 쌓는다.",
    tags: ["결제", "멱등성", "분산시스템", "장애대응"],
    keyPointOffsets: [682, 1661, 3364, 4470],
    isFavorite: true,
    isRead: false,
    isExcluded: false,
    addedAt: "2026-07-31T14:48:00Z",
    keywordIds: ["kw_2"],
    detail: {
      youtubeUrl: "https://youtu.be/aX7kQ2mN9pL",
      abstractBeats: [],
      sections: [],
      closing: "",
      abstract:
        "강연자는 실제 운영 중 발생한 이중 청구 사고를 재구성하며 시작한다. 네트워크 타임아웃 후 클라이언트가 재시도했고, 게이트웨이는 두 요청을 서로 다른 것으로 보았으며, DB 유니크 제약은 두 트랜잭션이 커밋되기 전 구간에서 무력했다. 이 사고를 축으로 방어 계층을 하나씩 쌓아 올리고, 각 층이 막는 실패와 막지 못하는 실패를 분리해 설명한다.",
      targetAudience: "결제·주문 도메인을 다루는 백엔드 개발자, 실무 경험 1년 이상",
      prerequisites: [
        "HTTP 재시도 의미론",
        "트랜잭션 격리 수준",
        "기본적인 분산 시스템 용어",
      ],
      keyPoints: [
        {
          heading: '멱등키는 요청이 아니라 "의도"에 붙인다',
          detail:
            "요청마다 새 키를 발급하면 재시도가 새 결제가 된다. 키는 사용자가 결제 버튼을 누른 그 행위 하나에 귀속되어야 하며, 화면 진입 시점에 발급해 재시도 전체가 같은 키를 공유하게 만든다.",
          timestampSec: 682,
        },
        {
          heading: "지수 백오프보다 멱등성이 먼저다",
          detail:
            "재시도 정책을 정교하게 다듬는 것은 멱등성이 보장된 다음의 일이다. 순서를 바꾸면 백오프는 사고의 빈도만 낮출 뿐 원인을 남겨둔다.",
          timestampSec: 1661,
        },
        {
          heading: "유니크 제약만으로는 부족하다 — 커밋 전 경쟁 구간이 남는다",
          detail:
            '두 트랜잭션이 동시에 조회하고 동시에 삽입을 시도하면, 제약은 늦게 도착한 쪽을 실패시키지만 그 실패를 "이미 처리됨"으로 해석할지 "오류"로 해석할지가 애플리케이션 몫으로 남는다.',
          timestampSec: 3364,
        },
        {
          heading: "정산 대사는 최후의 방어선이지 예방책이 아니다",
          detail:
            "PG사 응답과 내부 기록을 하루 단위로 맞춰보는 배치는 놓친 건을 찾아낼 뿐 발생을 막지 못한다. 앞의 세 층 없이 대사만 붙이면 매일 수동 보정을 하게 된다.",
          timestampSec: 4470,
        },
      ],
      chapters: [
        { title: "문제 정의 — 중복 결제는 어디서", startSec: 0, endSec: 580 },
        { title: "멱등키의 정의와 생명주기", startSec: 580, endSec: 1455 },
        { title: "1층 · 클라이언트 재시도 제어", startSec: 1455, endSec: 2330 },
        { title: "2층 · 게이트웨이 중복 제거", startSec: 2330, endSec: 3150 },
        { title: "3층 · 유니크 제약과 경쟁 조건", startSec: 3150, endSec: 4090 },
        { title: "4층 · PG 응답 대조와 정산 대사", startSec: 4090, endSec: 5160 },
        { title: "정리 · 도입 순서", startSec: 5160, endSec: 5645 },
      ],
      terms: [
        {
          term: "멱등키",
          definition:
            "같은 의도의 요청을 하나로 식별하는 클라이언트 발급 식별자. 서버는 이 키로 이미 처리한 결과를 되돌려준다.",
        },
        {
          term: "경쟁 구간",
          definition: "조회와 삽입 사이, 두 트랜잭션이 서로를 아직 볼 수 없는 시간 창.",
        },
        {
          term: "정산 대사",
          definition: "결제사 기록과 내부 기록을 주기적으로 대조해 불일치를 찾는 절차.",
        },
      ],
      takeaways: [
        "결제 API에 Idempotency-Key 헤더를 필수화하고 24시간 보관한다.",
        "키 발급 시점을 결제 요청이 아니라 결제 화면 진입으로 옮긴다.",
        '유니크 제약 위반을 오류가 아니라 "기존 결과 반환"으로 처리하는 분기를 넣는다.',
        "일 1회 PG 대사 배치를 붙이고, 불일치 건수를 알림 임계값으로 잡는다.",
      ],
      quotes: [
        {
          text: "멱등성을 나중에 붙이겠다는 말은, 사고가 난 다음에 붙이겠다는 말과 같습니다. 결제는 그 사고의 비용을 고객이 먼저 지불합니다.",
          timestampSec: 1868,
          why: "도입 순서를 논하며",
        },
      ],
      coverageNote: null,
      review: {
        model: "claude-opus-5",
        promptVersion: "v1",
        confidence: "high",
        criteria: [
          {
            criterion: "structure",
            score: 88,
            evidence: "도입에서 목차를 제시하고 계층별로 순서대로 전개",
          },
          {
            criterion: "depth",
            score: 95,
            evidence: '"제약은 늦게 도착한 쪽을 실패시키지만 그 해석은 애플리케이션 몫"',
          },
          {
            criterion: "evidence",
            score: 93,
            evidence: "실제 장애 타임라인과 로그를 화면에 띄워 설명",
          },
          {
            criterion: "authority",
            score: 90,
            evidence: '"결제 도메인 7년" 자기소개, 용어 구사 자연스러움',
          },
          {
            criterion: "density",
            score: 86,
            evidence: "도입 3분가량 잡담, 이후 군더더기 거의 없음",
          },
          { criterion: "commercial", score: 8, evidence: "홍보 없음 (낮을수록 좋음)" },
        ],
        redFlags: [],
        speakerCredentials: "결제 도메인 7년, 국내 PG 연동 경험",
        inputTokens: 19240,
        outputTokens: 3610,
        turns: 8,
      },
      transcriptExpiresAt: null,
    },
  },
  {
    videoId: "bK4rT8wY1zQ",
    title: "쿠버네티스 CNI 플러그인은 실제로 어떻게 패킷을 옮기는가",
    channelTitle: "인프라 노트",
    durationSec: 4360,
    publishedAt: "2026-03-14",
    expertScore: 87,
    verdict: "expert",
    oneLiner:
      "네트워크 네임스페이스에서 시작해 Calico의 BGP·IPIP 모드까지 패킷 경로를 직접 추적한다.",
    tags: ["쿠버네티스", "네트워킹", "CNI", "Calico"],
    keyPointOffsets: [723, 1481, 2210, 3105, 3940],
    isFavorite: false,
    isRead: false,
    isExcluded: false,
    addedAt: "2026-07-31T14:48:00Z",
    keywordIds: ["kw_1"],
    detail: {
      youtubeUrl: "https://youtu.be/bK4rT8wY1zQ",
      abstractBeats: [],
      sections: [],
      closing: "",
      abstract:
        "CNI는 스펙일 뿐 구현이 아니라는 점에서 출발해, 파드가 만들어질 때 네트워크 네임스페이스와 veth 쌍이 어떻게 배치되는지를 실습으로 보여준다. 이어서 Calico의 두 모드를 비교하며 각각 어떤 네트워크 환경에서 유효한지 설명한다.",
      targetAudience: "쿠버네티스 운영 경험 6개월 이상",
      prerequisites: ["리눅스 네트워크 네임스페이스", "iptables 기초", "라우팅 테이블 읽기"],
      keyPoints: [
        {
          heading: "CNI는 스펙일 뿐 구현이 아니다",
          detail:
            "kubelet은 CNI 바이너리를 호출할 뿐이고, 실제 패킷 경로는 플러그인이 결정한다. 그래서 같은 매니페스트라도 클러스터마다 동작이 다르다.",
          timestampSec: 723,
        },
        {
          heading: "veth 쌍과 네임스페이스가 전부의 출발점",
          detail:
            "파드 하나당 veth 쌍 하나가 만들어지고 한쪽 끝이 호스트에 남는다. 이 구조를 직접 만들어 보면 이후 모든 플러그인의 동작이 같은 뼈대 위에 있음을 알 수 있다.",
          timestampSec: 1481,
        },
        {
          heading: "Calico BGP 모드는 언더레이 네트워크를 신뢰한다",
          detail:
            "노드 간 라우팅을 BGP로 광고하므로 오버레이 캡슐화 비용이 없지만, 네트워크 장비가 이 경로를 이해해야 한다.",
          timestampSec: 2210,
        },
        {
          heading: "IPIP 모드는 그 신뢰를 포기하는 대신 어디서나 돈다",
          detail:
            "캡슐화 오버헤드를 지불하고 언더레이 제약에서 벗어난다. 클라우드 VPC처럼 라우팅을 통제할 수 없는 환경의 기본 선택지.",
          timestampSec: 3105,
        },
        {
          heading: "장애 진단은 항상 같은 순서로",
          detail:
            "파드 내부 → veth → 호스트 라우팅 → 노드 간 경로 순으로 좁혀 가면 대부분의 통신 장애가 두세 단계 안에서 잡힌다.",
          timestampSec: 3940,
        },
      ],
      chapters: [
        { title: "왜 파드 간 통신이 어려운가", startSec: 0, endSec: 495 },
        { title: "네트워크 네임스페이스 복습", startSec: 495, endSec: 1180 },
        { title: "veth 쌍 직접 만들어 보기", startSec: 1180, endSec: 1990 },
        { title: "CNI 스펙과 플러그인 호출", startSec: 1990, endSec: 2760 },
        { title: "Calico BGP 모드", startSec: 2760, endSec: 3480 },
        { title: "Calico IPIP 모드", startSec: 3480, endSec: 4010 },
        { title: "장애 진단 순서", startSec: 4010, endSec: 4360 },
      ],
      terms: [
        {
          term: "CNI",
          definition: "컨테이너 런타임이 네트워크 플러그인을 호출하는 방식을 정한 스펙.",
        },
        { term: "veth 쌍", definition: "네임스페이스를 잇는 가상 이더넷 인터페이스 한 쌍." },
        {
          term: "IPIP",
          definition: "IP 패킷을 다른 IP 패킷 안에 넣어 보내는 캡슐화 방식.",
        },
      ],
      takeaways: [
        "클러스터의 CNI 플러그인과 모드를 먼저 확인하고 문서를 읽는다.",
        "통신 장애는 파드 내부 → veth → 호스트 라우팅 순으로 좁힌다.",
        "VPC 라우팅을 통제할 수 없으면 IPIP 모드를 기본으로 둔다.",
      ],
      quotes: [
        {
          text: "CNI 문서를 아무리 읽어도 패킷이 어디로 가는지는 안 나옵니다. 그건 플러그인 문서에 있어요.",
          timestampSec: 812,
          why: "스펙과 구현의 구분을 강조하며",
        },
      ],
      coverageNote: "43분경 자동 자막 품질이 떨어져 명령어 일부를 옮기지 못했습니다.",
      review: {
        model: "claude-opus-5",
        promptVersion: "v1",
        confidence: "high",
        criteria: [
          { criterion: "structure", score: 85, evidence: "네임스페이스 → CNI → 구현 순서" },
          { criterion: "depth", score: 90, evidence: "veth 쌍을 직접 만들어 보이며 설명" },
          { criterion: "evidence", score: 88, evidence: "tcpdump 출력으로 경로 확인" },
          { criterion: "authority", score: 84, evidence: "온프렘 클러스터 운영 경험 언급" },
          { criterion: "density", score: 82, evidence: "실습 대기 시간이 다소 김" },
          { criterion: "commercial", score: 4, evidence: "홍보 없음" },
        ],
        redFlags: [],
        speakerCredentials: "온프렘 쿠버네티스 운영 4년",
        inputTokens: 16880,
        outputTokens: 3320,
        turns: 7,
      },
      transcriptExpiresAt: daysAgo(-12),
    },
  },
  {
    videoId: "cM9nP3vB7hR",
    title: "PostgreSQL 인덱스, EXPLAIN을 읽는 법부터",
    channelTitle: "데이터베이스 실전",
    durationSec: 2900,
    publishedAt: "2026-05-02",
    expertScore: 81,
    verdict: "expert",
    oneLiner:
      "실행 계획의 비용 추정치를 해석하고, 인덱스가 선택되지 않는 다섯 가지 흔한 원인을 짚는다.",
    tags: ["PostgreSQL", "인덱스", "쿼리튜닝"],
    keyPointOffsets: [395, 1240, 2115],
    isFavorite: false,
    isRead: false,
    isExcluded: false,
    addedAt: "2026-07-31T14:48:00Z",
    keywordIds: ["kw_3"],
    detail: {
      youtubeUrl: "https://youtu.be/cM9nP3vB7hR",
      abstractBeats: [],
      sections: [],
      closing: "",
      abstract:
        "EXPLAIN 출력의 각 숫자가 무엇을 뜻하는지부터 시작해, 추정치와 실측치가 벌어질 때 무엇을 의심해야 하는지를 다룬다. 인덱스를 만들었는데도 순차 스캔이 나오는 상황을 다섯 가지 원인으로 분류한다.",
      targetAudience: "SQL을 쓰지만 실행 계획은 잘 안 보는 개발자",
      prerequisites: ["기본 SQL", "인덱스의 개념"],
      keyPoints: [
        {
          heading: "cost 는 시간이 아니라 상대적 단위다",
          detail:
            "cost=0.00..8.27 의 숫자를 밀리초로 읽으면 안 된다. 플래너가 계획들을 비교하기 위한 상대 척도이고, 실제 시간은 ANALYZE 를 붙여야 나온다.",
          timestampSec: 395,
        },
        {
          heading: "추정 행 수가 실제와 10배 이상 벌어지면 통계를 의심한다",
          detail:
            "rows= 추정치와 actual rows 의 괴리가 크면 ANALYZE 가 오래됐거나 상관관계가 있는 컬럼을 독립으로 가정한 경우다.",
          timestampSec: 1240,
        },
        {
          heading: "인덱스가 있어도 안 쓰이는 다섯 가지",
          detail:
            "타입 불일치, 함수 적용, 선택도 부족, 통계 미갱신, 그리고 그냥 순차 스캔이 더 싼 경우. 마지막은 문제가 아니다.",
          timestampSec: 2115,
        },
      ],
      chapters: [
        { title: "EXPLAIN 출력 구조", startSec: 0, endSec: 610 },
        { title: "cost 와 rows 읽기", startSec: 610, endSec: 1420 },
        { title: "추정과 실측이 벌어질 때", startSec: 1420, endSec: 2200 },
        { title: "인덱스가 안 쓰이는 경우들", startSec: 2200, endSec: 2900 },
      ],
      terms: [
        { term: "선택도", definition: "조건이 전체 행 중 얼마나 걸러내는지의 비율." },
        {
          term: "순차 스캔",
          definition: "인덱스 없이 테이블을 처음부터 훑는 방식. 항상 나쁜 것은 아니다.",
        },
      ],
      takeaways: [
        "EXPLAIN 대신 EXPLAIN (ANALYZE, BUFFERS) 를 습관으로 만든다.",
        "추정 행 수와 실제 행 수를 항상 나란히 본다.",
        "인덱스 컬럼에 함수를 씌우면 표현식 인덱스를 따로 만든다.",
      ],
      quotes: [
        {
          text: "순차 스캔이 나왔다고 다 문제는 아닙니다. 작은 테이블에서는 그게 제일 빠릅니다.",
          timestampSec: 2240,
          why: "과도한 인덱스 추가를 경계하며",
        },
      ],
      coverageNote: null,
      review: {
        model: "claude-opus-5",
        promptVersion: "v1",
        confidence: "medium",
        criteria: [
          { criterion: "structure", score: 84, evidence: "출력 구조 → 해석 → 사례 순" },
          { criterion: "depth", score: 80, evidence: "통계 정보와 상관관계까지 언급" },
          { criterion: "evidence", score: 82, evidence: "실제 쿼리 실행 계획을 화면에 띄움" },
          { criterion: "authority", score: 76, evidence: "DBA 경력 언급은 있으나 구체성 부족" },
          { criterion: "density", score: 84, evidence: "군더더기 적음" },
          { criterion: "commercial", score: 6, evidence: "홍보 없음" },
        ],
        redFlags: [],
        speakerCredentials: "DBA 경력 언급",
        inputTokens: 12400,
        outputTokens: 2980,
        turns: 6,
      },
      transcriptExpiresAt: daysAgo(-4),
    },
  },
  {
    videoId: "dW2jL6xF4tG",
    title: "브라우저 렌더링 파이프라인과 레이아웃 스래싱",
    channelTitle: "프론트엔드 딥다이브",
    durationSec: 3933,
    publishedAt: "2026-07-09",
    expertScore: 79,
    verdict: "expert",
    oneLiner:
      "스타일 계산부터 합성까지 각 단계의 비용을 프로파일러로 측정하며 재계산을 유발하는 코드를 찾아낸다.",
    tags: ["브라우저", "렌더링", "성능"],
    keyPointOffsets: [512, 1420, 2380, 3350],
    isFavorite: false,
    isRead: false,
    isExcluded: false,
    addedAt: "2026-07-31T14:48:00Z",
    keywordIds: ["kw_4"],
    detail: {
      youtubeUrl: "https://youtu.be/dW2jL6xF4tG",
      abstractBeats: [],
      sections: [],
      closing: "",
      abstract:
        "렌더링 파이프라인의 다섯 단계를 나누고, 각 단계를 건드리는 CSS 속성이 무엇인지 정리한다. 이어서 읽기와 쓰기를 번갈아 하는 코드가 왜 강제 동기 레이아웃을 유발하는지 프로파일러로 보여준다.",
      targetAudience: "성능 문제를 감이 아니라 측정으로 접근하려는 프론트엔드 개발자",
      prerequisites: ["DOM API", "CSS 박스 모델", "개발자 도구 기본 사용"],
      keyPoints: [
        {
          heading: "파이프라인 단계마다 되돌아가는 비용이 다르다",
          detail:
            "transform 과 opacity 는 합성 단계만 건드리지만 width 는 레이아웃부터 다시 돈다. 같은 시각 효과라도 어떤 속성으로 만드느냐가 프레임 예산을 좌우한다.",
          timestampSec: 512,
        },
        {
          heading: "레이아웃 스래싱은 읽기-쓰기 교대에서 생긴다",
          detail:
            "offsetHeight 를 읽고 스타일을 쓰고 다시 읽으면, 브라우저는 매번 레이아웃을 강제로 확정해야 한다. 반복문 안에서 이 패턴이 나오면 프레임이 무너진다.",
          timestampSec: 1420,
        },
        {
          heading: "읽기와 쓰기를 모아서 분리한다",
          detail:
            "모든 측정을 먼저 끝내고 그다음에 모든 변경을 적용하면 레이아웃이 한 번만 돈다. 라이브러리 없이도 이 규칙만으로 대부분 해결된다.",
          timestampSec: 2380,
        },
        {
          heading: "측정 없이 최적화하지 않는다",
          detail:
            "Performance 패널에서 어느 단계가 긴지 먼저 확인한다. 스크립트가 문제인지 레이아웃이 문제인지 모르고 손대면 시간만 쓴다.",
          timestampSec: 3350,
        },
      ],
      chapters: [
        { title: "렌더링 파이프라인 다섯 단계", startSec: 0, endSec: 780 },
        { title: "속성별로 어디까지 되돌아가는가", startSec: 780, endSec: 1690 },
        { title: "강제 동기 레이아웃 재현", startSec: 1690, endSec: 2600 },
        { title: "읽기-쓰기 분리 리팩터링", startSec: 2600, endSec: 3400 },
        { title: "측정 습관", startSec: 3400, endSec: 3933 },
      ],
      terms: [
        {
          term: "레이아웃 스래싱",
          definition: "읽기와 쓰기를 번갈아 해 레이아웃을 반복 강제하는 패턴.",
        },
        { term: "합성", definition: "이미 그려진 레이어를 GPU에서 합치는 마지막 단계." },
      ],
      takeaways: [
        "애니메이션은 transform·opacity 로 만든다.",
        "반복문 안에서 offset* / getBoundingClientRect 읽기와 스타일 쓰기를 섞지 않는다.",
        "Performance 패널로 먼저 측정하고 손댄다.",
      ],
      quotes: [
        {
          text: "느리다고 느끼는 것과 느린 것은 다릅니다. 패널을 켜기 전까지는 둘을 구분할 수 없어요.",
          timestampSec: 3390,
          why: "측정 우선 원칙",
        },
      ],
      coverageNote: null,
      review: {
        model: "claude-opus-5",
        promptVersion: "v1",
        confidence: "medium",
        criteria: [
          { criterion: "structure", score: 82, evidence: "단계 정의 → 사례 → 리팩터링" },
          { criterion: "depth", score: 78, evidence: "합성 레이어 승격 조건까지 다룸" },
          { criterion: "evidence", score: 80, evidence: "프로파일러 타임라인 실측" },
          { criterion: "authority", score: 74, evidence: "구체적 소속 언급 없음" },
          { criterion: "density", score: 80, evidence: "중반 반복 설명 있음" },
          { criterion: "commercial", score: 10, evidence: "채널 구독 유도 짧게" },
        ],
        redFlags: [],
        speakerCredentials: null,
        inputTokens: 14300,
        outputTokens: 3100,
        turns: 7,
      },
      transcriptExpiresAt: daysAgo(-21),
    },
  },
  {
    videoId: "eR5tY8uI0oP",
    title: "LLM 파인튜닝 전에 확인할 것들",
    channelTitle: "ML 엔지니어링 랩",
    durationSec: 3312,
    publishedAt: "2026-07-25",
    expertScore: 74,
    verdict: "practical",
    oneLiner:
      "파인튜닝이 실제로 필요한 조건을 먼저 따지고, 프롬프트·RAG로 해결되는 경우를 구분한다.",
    tags: ["LLM", "파인튜닝", "RAG"],
    keyPointOffsets: [440, 1590, 2705],
    isFavorite: false,
    isRead: false,
    isExcluded: false,
    addedAt: "2026-07-31T14:48:00Z",
    keywordIds: ["kw_6"],
    detail: {
      youtubeUrl: "https://youtu.be/eR5tY8uI0oP",
      abstractBeats: [],
      sections: [],
      closing: "",
      abstract:
        "파인튜닝을 검토하는 팀이 가장 자주 놓치는 것은 '이 문제가 정말 가중치 문제인가'라는 질문이다. 프롬프트 개선과 검색 증강으로 해결되는 사례를 먼저 걸러내고, 남는 경우에 필요한 데이터 규모와 평가 체계를 다룬다.",
      targetAudience: "LLM 제품을 만들며 파인튜닝을 검토 중인 팀",
      prerequisites: ["프롬프트 엔지니어링 기본", "RAG 개념"],
      keyPoints: [
        {
          heading: "형식 문제는 파인튜닝 문제가 아니다",
          detail:
            "출력 형식이 흔들리는 것은 대부분 구조화 출력이나 스키마 강제로 해결된다. 여기에 파인튜닝을 쓰면 비용만 든다.",
          timestampSec: 440,
        },
        {
          heading: "지식 문제는 검색으로, 행동 문제는 학습으로",
          detail:
            "모델이 모르는 사실이 문제면 RAG가 맞고, 아는데 원하는 방식으로 안 하는 것이 문제면 그때 파인튜닝을 검토한다.",
          timestampSec: 1590,
        },
        {
          heading: "평가 세트가 먼저다",
          detail:
            "학습 데이터를 모으기 전에 평가 세트를 만든다. 없으면 나아졌는지 알 수 없고, 나아졌다고 착각하게 된다.",
          timestampSec: 2705,
        },
      ],
      chapters: [
        { title: "언제 파인튜닝이 답이 아닌가", startSec: 0, endSec: 900 },
        { title: "프롬프트·RAG로 해결되는 경우", startSec: 900, endSec: 1900 },
        { title: "데이터 규모와 품질", startSec: 1900, endSec: 2600 },
        { title: "평가 체계 먼저 만들기", startSec: 2600, endSec: 3312 },
      ],
      terms: [
        { term: "RAG", definition: "외부 문서를 검색해 프롬프트에 넣어 답하게 하는 방식." },
        { term: "평가 세트", definition: "변경 전후를 비교하기 위해 라벨링해 둔 고정 입력 묶음." },
      ],
      takeaways: [
        "파인튜닝 검토 전에 형식 문제와 지식 문제를 먼저 분리한다.",
        "평가 세트를 30건 규모로 먼저 만든다.",
        "프롬프트 버전을 기록해 무엇이 성능을 바꿨는지 추적한다.",
      ],
      quotes: [
        {
          text: "평가 세트 없이 파인튜닝하면, 좋아졌다는 느낌만 남고 근거는 안 남습니다.",
          timestampSec: 2760,
          why: "평가 우선 원칙",
        },
      ],
      coverageNote: null,
      review: {
        model: "claude-opus-5",
        promptVersion: "v1",
        confidence: "medium",
        criteria: [
          { criterion: "structure", score: 78, evidence: "질문 던지고 분기하는 구성" },
          { criterion: "depth", score: 70, evidence: "구체적 하이퍼파라미터는 다루지 않음" },
          { criterion: "evidence", score: 72, evidence: "사례는 있으나 수치 제시 적음" },
          { criterion: "authority", score: 76, evidence: "실무 프로젝트 경험 언급" },
          { criterion: "density", score: 76, evidence: "도입부 잡담 다소" },
          { criterion: "commercial", score: 14, evidence: "자사 강의 언급 1회" },
        ],
        redFlags: ["종반에 유료 강의 언급 1회"],
        speakerCredentials: "ML 엔지니어, 사내 LLM 제품 경험",
        inputTokens: 13100,
        outputTokens: 2740,
        turns: 6,
      },
      transcriptExpiresAt: daysAgo(-27),
    },
  },
];

// ── 구현 ───────────────────────────────────────────────────
function toSummary(s: Seed): LectureSummary {
  const { detail: _detail, ...rest } = s;
  return rest;
}

export const mockApi: Api = {
  async listPeople() {
    await delay();
    return people.map((p) => ({ ...p }));
  },

  async pickPerson(id, pin) {
    await delay();
    const p = people.find((x) => x.id === id);
    if (!p) throw new ApiError("그 사람을 찾을 수 없습니다.", 404, "USER_NOT_FOUND");
    // 목에서는 0000 만 맞다고 봅니다 — 진짜 검증은 서버 몫입니다.
    if (p.hasPin && pin !== "0000") {
      throw new ApiError("비밀번호가 다릅니다.", 401, "PIN_WRONG");
    }
    current = p;
    return { ...p };
  },

  async createPerson(draft) {
    await delay();
    const p: Person = {
      id: nextId("u"),
      name: draft.name,
      isOwner: false,
      hasPin: Boolean(draft.pin),
      lectureCount: draft.keywordIds.length * 6,
    };
    people = [...people, p];
    current = p;
    return { ...p };
  },

  async leave() {
    await delay();
    current = null;
  },

  async getMe() {
    await delay();
    if (!current) throw new ApiError("누구인지 먼저 골라 주세요.", 401, "NO_SESSION");
    return {
      ...current,
      keywordCount: keywords.filter((k) => k.isMine && k.status !== "archived").length,
      keywordLimit: current.isOwner ? 0 : 10,
      pinIsDefault: current.isOwner,
    };
  },

  async renameMe(name) {
    await delay();
    if (!current) throw new ApiError("누구인지 먼저 골라 주세요.", 401, "NO_SESSION");
    const next = { ...current, name };
    current = next;
    people = people.map((p) => (p.id === next.id ? next : p));
    return { ...next };
  },

  async setPin(_current, next) {
    await delay();
    if (!current) throw new ApiError("누구인지 먼저 골라 주세요.", 401, "NO_SESSION");
    if (current.isOwner && !next) {
      throw new ApiError("관리자는 비밀번호를 비울 수 없습니다.", 400, "OWNER_NEEDS_PIN");
    }
    const updated = { ...current, hasPin: Boolean(next) };
    current = updated;
    people = people.map((p) => (p.id === updated.id ? updated : p));
  },

  async listAllKeywords() {
    await delay();
    return keywords.filter((k) => k.status !== "archived").map((k) => ({ ...k }));
  },

  async subscribeKeyword(id) {
    await delay();
    const k = keywords.find((x) => x.id === id);
    if (!k) throw new ApiError("해당 키워드를 찾을 수 없습니다.", 404, "KEYWORD_NOT_FOUND");
    k.isMine = true;
    k.subscriberCount += 1;
    return { ...k };
  },

  async listKeywords() {
    await delay();
    return keywords.filter((k) => k.status !== "archived").map((k) => ({ ...k }));
  },

  async listArchivedKeywords() {
    await delay();
    return keywords
      .filter((k) => k.status === "archived")
      .sort((a, b) => (b.archivedAt ?? "").localeCompare(a.archivedAt ?? ""))
      .map((k) => ({ ...k }));
  },

  async createKeyword(draft: KeywordDraft) {
    await delay(320);
    const term = draft.term.trim();
    if (!term) throw new ApiError("검색어를 입력하세요.", 400, "TERM_REQUIRED");
    if (keywords.some((k) => k.term === term && k.status !== "archived")) {
      throw new ApiError(`"${term}" 은(는) 이미 등록되어 있습니다.`, 409, "KEYWORD_DUPLICATE");
    }
    const created: Keyword = {
      id: nextId("kw"),
      term,
      sourceType: draft.sourceType,
      channelTitle: draft.sourceType === "channel" ? term.replace(/^@/, "") : null,
      status: "pending",
      // 내가 만든 것이니 당연히 내 구독이고, 아직 나 혼자입니다.
      isMine: true,
      subscriberCount: 1,
      // 만든 사람이 나이므로 고칠 수 있습니다.
      canEdit: true,
      createdByName: "관리자",
      language: draft.language,
      schedule: draft.schedule,
      minDurationSec: draft.minDurationSec,
      minExpertScore: draft.minExpertScore,
      maxPerRun: draft.maxPerRun,
      lectureCount: 0,
      lastRunAt: null,
      createdAt: iso(new Date()),
      archivedAt: null,
    };
    keywords = [created, ...keywords];
    return { ...created };
  },

  async updateKeyword(id, patch) {
    await delay();
    const i = keywords.findIndex((k) => k.id === id);
    const found = keywords[i];
    if (!found) throw new ApiError("키워드를 찾을 수 없습니다.", 404, "NOT_FOUND");
    // 서버와 같은 자리에서 막습니다 — 목이 더 허용하면 화면 버그를 못 잡습니다.
    if (!found.canEdit)
      throw new ApiError(
        `${found.createdByName ?? "다른 사람"} 님이 만든 키워드라 고칠 수 없습니다. 빼는 것은 됩니다.`,
        403,
        "NOT_KEYWORD_AUTHOR",
      );
    const updated: Keyword = { ...found, ...patch };
    keywords[i] = updated;
    return { ...updated };
  },

  async setKeywordStatus(id, status) {
    await delay();
    const i = keywords.findIndex((k) => k.id === id);
    const found = keywords[i];
    if (!found) throw new ApiError("키워드를 찾을 수 없습니다.", 404, "NOT_FOUND");
    // 일시정지도 같은 통로입니다 — 남의 키워드를 멈추면 그 사람 수집이 멎습니다.
    if (!found.canEdit)
      throw new ApiError(
        `${found.createdByName ?? "다른 사람"} 님이 만든 키워드라 멈출 수 없습니다.`,
        403,
        "NOT_KEYWORD_AUTHOR",
      );
    const updated: Keyword = { ...found, status };
    keywords[i] = updated;
    return { ...updated };
  },

  async deleteKeyword(id) {
    await delay();
    // 목록에서 지우는 게 아니라 삭제 영역으로 옮깁니다 — 되살릴 수 있어야 합니다
    const i = keywords.findIndex((k) => k.id === id);
    const found = keywords[i];
    if (!found) throw new ApiError("키워드를 찾을 수 없습니다.", 404, "NOT_FOUND");
    keywords[i] = { ...found, status: "archived", archivedAt: iso(new Date()) };
  },

  async restoreKeyword(id) {
    await delay();
    const i = keywords.findIndex((k) => k.id === id);
    const found = keywords[i];
    if (!found) throw new ApiError("키워드를 찾을 수 없습니다.", 404, "NOT_FOUND");
    if (found.status !== "archived") {
      throw new ApiError("삭제된 키워드가 아닙니다.", 409, "NOT_ARCHIVED");
    }
    const restored: Keyword = {
      ...found,
      status: found.lastRunAt === null ? "pending" : "active",
      archivedAt: null,
    };
    keywords[i] = restored;
    return { ...restored };
  },

  async listLectures(query) {
    await delay();
    let rows = lectures.map(toSummary);

    if (query.keywordIds?.length) {
      const set = new Set(query.keywordIds);
      rows = rows.filter((l) => l.keywordIds.some((id) => set.has(id)));
    }
    // 목록과 제외함이 같은 함수를 씁니다 — 조건이 갈리면 화면에 안 나오는
    // 것을 두고 새로 왔다고 알리게 됩니다.
    rows = rows.filter((l) => Boolean(l.isExcluded) === Boolean(query.excluded));
    if (query.minScore != null) rows = rows.filter((l) => l.expertScore >= query.minScore!);
    if (query.minDurationSec != null)
      rows = rows.filter((l) => l.durationSec >= query.minDurationSec!);
    if (query.maxDurationSec != null)
      rows = rows.filter((l) => l.durationSec <= query.maxDurationSec!);
    if (query.favoritesOnly) rows = rows.filter((l) => l.isFavorite);
    if (query.q?.trim()) {
      const q = query.q.trim().toLowerCase();
      rows = rows.filter((l) =>
        [l.title, l.oneLiner, l.channelTitle, ...l.tags].join(" ").toLowerCase().includes(q),
      );
    }

    const sort = query.sort ?? "unread";
    rows.sort((a, b) => {
      const fresh = (b.publishedAt ?? "").localeCompare(a.publishedAt ?? "");
      if (sort === "unread") return Number(a.isRead) - Number(b.isRead) || fresh;
      if (sort === "recent") return fresh;
      if (sort === "duration") return b.durationSec - a.durationSec;
      return b.expertScore - a.expertScore;
    });

    // 서버와 같이 한 쪽씩 줍니다. 목이 전부 주면 지연 로딩이 목에서는
    // 한 번도 안 돌아, 끊어 받는 코드의 버그가 실제로 붙일 때 드러납니다.
    const offset = query.offset ?? 0;
    const limit = query.limit ?? 60;
    return {
      items: rows.slice(offset, offset + limit),
      // 개수와 최신 시각은 **쪽이 아니라 걸린 것 전체** 기준입니다.
      total: rows.length,
      latestAddedAt: rows.reduce<string | null>(
        (max, l) => (max === null || l.addedAt > max ? l.addedAt : max),
        null,
      ),
    };
  },

  async getLecture(videoId) {
    await delay();
    const found = lectures.find((l) => l.videoId === videoId);
    if (!found) throw new ApiError("강의를 찾을 수 없습니다.", 404, "NOT_FOUND");
    return { ...toSummary(found), ...found.detail };
  },

  async setFavorite(videoId, isFavorite) {
    await delay(80);
    const found = lectures.find((l) => l.videoId === videoId);
    if (found) found.isFavorite = isFavorite;
  },

  async countNewLectures(_query, since) {
    await delay(50);
    return lectures.filter((l) => l.addedAt > since).length;
  },

  async setExcluded(videoId, isExcluded) {
    await delay(80);
    const found = lectures.find((l) => l.videoId === videoId);
    if (found) found.isExcluded = isExcluded;
  },

  async deleteLecture(videoId) {
    await delay(80);
    const i = lectures.findIndex((l) => l.videoId === videoId);
    if (i >= 0) lectures.splice(i, 1);
  },

  async markRead(videoId) {
    await delay(60);
    const found = lectures.find((l) => l.videoId === videoId);
    if (found) found.isRead = true;
  },

  async setTokenLimit(_limitTokens: number | null, _provider?: string) {
    await delay(60);
  },

  async inheritTokenLimit(_provider: string) {
    await delay(60);
  },

  async getOverview(): Promise<Overview> {
    await delay();
    return {
      newToday: 5,
      totalLectures: 142,
      weekAdded: 23,
      avgScore: 81,
      queued: { transcript: 6, review: 3 },
      funnel: {
        discovered: 47,
        rulePassed: 18,
        transcribed: 14,
        reviewed: 14,
        published: 5,
      },
      earlyExitCount: 9,
      earlyExitSavedInputTokens: 92_000,
      contributions: [
        { keywordId: "kw_2", term: "결제 시스템 설계", published: 2 },
        { keywordId: "kw_1", term: "쿠버네티스 네트워킹", published: 1 },
        { keywordId: "kw_3", term: "PostgreSQL 튜닝", published: 1 },
        { keywordId: "kw_4", term: "브라우저 성능", published: 1 },
      ],
      failures: [
        {
          kind: "transcript",
          label: "자막 없음",
          title: "gRPC 스트리밍 실전 패턴",
          detail: "03:58 · 자동 자막 미제공 · STT 대기",
        },
        {
          kind: "review",
          label: "검토 실패",
          title: "분산 트랜잭션 사가 패턴 정리",
          detail: "04:12 · 타임아웃 900s · 재시도 1/2",
        },
        {
          kind: "quota",
          label: "쿼터",
          title: '키워드 "옵저버빌리티"',
          detail: "04:20 · 검색 유닛 부족 · 내일 재개",
        },
      ],
      lastRunAt: runAt(0, 0),
    };
  },

  async getUsage(): Promise<Usage> {
    await delay();
    return {
      inputTokens: 183_000,
      outputTokens: 24_000,
      providers: [
        {
          provider: "claude",
          inputTokens: 150_000,
          outputTokens: 19_000,
          calls: 7,
          limitTokens: 12_000_000,
          hasOwnLimit: true,
          restingUntil: null,
          capped: false,
        },
        {
          provider: "antigravity",
          inputTokens: 33_000,
          outputTokens: 5_000,
          calls: 2,
          limitTokens: 8_000_000,
          hasOwnLimit: false,
          // 막혀서 쉬는 중인 모습도 화면에서 볼 수 있게 해 둡니다.
          restingUntil: null,
          // 상한을 넘어 멈춘 모습도 화면에서 볼 수 있게 해 둡니다.
          capped: true,
        },
      ],
      limitTokens: 20_000_000,
      windowHours: 5,
      windowResetsAt: new Date(Date.now() + 3 * 3600_000).toISOString(),
      todayTokens: 1_200_000,
      youtubeUnits: 2_140,
      youtubeUnitLimit: 10_000,
      resetsAt: runAt(-1, 0),
    };
  },

  async listChannelBlocks() {
    await delay();
    return channelBlocks.map((b) => ({ ...b }));
  },

  async blockChannel(handle: string, reason?: string) {
    await delay(320);
    const name = handle.replace(/^@/, "");
    if (channelBlocks.some((b) => b.channelTitle === name)) {
      throw new ApiError(`${name} 은(는) 이미 차단되어 있습니다.`, 409, "ALREADY_BLOCKED");
    }
    const block: ChannelBlock = {
      channelId: nextId("ch"),
      channelTitle: name,
      reason: reason?.trim() || "직접 차단했습니다.",
      auto: false,
      rejectedCount: 0,
      createdAt: iso(new Date()),
    };
    channelBlocks = [block, ...channelBlocks];
    return { ...block };
  },

  async unblockChannel(channelId: string) {
    await delay();
    channelBlocks = channelBlocks.filter((b) => b.channelId !== channelId);
  },

  async requestRun() {
    await delay(320);
    const run: Run = {
      id: nextId("run"),
      label: "실행 대기 중",
      job: "cycle",
      trigger: "manual",
      status: "queued",
      startedAt: iso(new Date()),
      finishedAt: null,
      stats: { discovered: 0, rulePassed: 0, transcribed: 0, reviewed: 0, published: 0 },
      tokens: 0,
      youtubeUnits: 0,
      error: null,
    };
    queuedRuns = [run, ...queuedRuns];
    return { ...run };
  },

  async getPipeline() {
    await delay();
    return {
      funnel: [
        { key: "discovered", label: "발견", count: 4 },
        { key: "transcript", label: "자막 대기", count: 3 },
        { key: "review", label: "요약 대기", count: 2 },
        { key: "published", label: "공개", count: lectures.length },
      ],
      tracks: [
        { key: "discover", label: "검색", status: "idle" as const, waiting: 4,
          runLabel: null, startedAt: null, working: null, lastAt: null, nextAt: null },
        { key: "transcript", label: "자막", status: "idle" as const, waiting: 3,
          runLabel: null, startedAt: null, working: null, lastAt: null, nextAt: null },
        { key: "review", label: "요약", status: "idle" as const, waiting: 2,
          runLabel: null, startedAt: null, working: null, lastAt: null, nextAt: null },
      ],
      stuck: [
        { key: "failedTranscript", label: "자막 실패", count: 0 },
        { key: "failedReview", label: "요약 실패", count: 0 },
      ],
      transcriptCoolingUntil: null,
    };
  },

  async getQueue() {
    await delay();
    return { stages: [], skipped: [], asrRealtimeFactor: 5 };
  },

  async skipQueued(_videoId: string) {
    await delay(60);
  },

  async restoreQueued(_videoId: string) {
    await delay(60);
  },

  async listRunEvents(_runId: string) {
    await delay();
    return [];
  },

  async listRuns(): Promise<Run[]> {
    await delay();
    return [
      ...queuedRuns,
      {
        id: "run_3",
        label: "키워드 10개 · 정기 실행",
        job: "cycle",
        trigger: "scheduled",
        status: "succeeded",
        startedAt: runAt(0, 0),
        finishedAt: runAt(0, 24),
        stats: { discovered: 47, rulePassed: 18, transcribed: 14, reviewed: 14, published: 5 },
        tokens: 207_000,
        youtubeUnits: 2_140,
        error: null,
      },
      {
        id: "run_2",
        label: "키워드 10개 · 정기 실행",
        job: "cycle",
        trigger: "scheduled",
        status: "partial",
        startedAt: runAt(1, 0),
        finishedAt: runAt(1, 31),
        stats: { discovered: 52, rulePassed: 21, transcribed: 15, reviewed: 13, published: 7 },
        tokens: 222_000,
        youtubeUnits: 2_140,
        error: null,
      },
      {
        id: "run_1",
        label: '"LLM 서빙 최적화" · 수동 실행',
        job: "cycle",
        trigger: "manual",
        status: "failed",
        startedAt: atHour(2, 14, 12),
        finishedAt: atHour(2, 14, 19),
        stats: { discovered: 31, rulePassed: 6, transcribed: 1, reviewed: 0, published: 0 },
        tokens: 2_400,
        youtubeUnits: 200,
        error:
          "자막 수집 5건 연속 429 — 요청이 차단되어 실행을 중단했습니다. 30분 후 자동 재개됩니다.",
      },
    ];
  },
};
