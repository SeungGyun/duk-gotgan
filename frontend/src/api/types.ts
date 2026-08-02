/**
 * UI 가 다루는 도메인 타입.
 * docs/SPEC.md §3 데이터 모델의 부분집합이며, UI 가 실제로 쓰는 필드만 담습니다.
 * 백엔드를 붙일 때는 이 형태로 직렬화해 주면 됩니다.
 */

// ── 키워드 ────────────────────────────────────────────────
export type KeywordStatus =
  | "pending" // 등록 직후, 첫 수집 대기
  | "active" // 정상 동작
  | "quota_wait" // 유튜브 쿼터 소진으로 대기
  | "paused" // 사용자가 멈춤
  | "archived";

export type Schedule = "daily" | "twice_weekly" | "weekly";
export type Language = "ko" | "en" | "any";

/** search — 검색어로 찾기 (검색 1회 100유닛)
 *  channel — 관심 채널 구독. term 에 @핸들. 1유닛이라 50배 쌉니다 */
export type SourceType = "search" | "channel";

/** 차단한 채널. 자동(판정 이력) 또는 사용자가 직접. */
export interface ChannelBlock {
  channelId: string;
  channelTitle: string;
  reason: string;
  auto: boolean;
  rejectedCount: number;
  createdAt: string;
}

export interface Keyword {
  id: string;
  term: string;
  sourceType: SourceType;
  /** 채널 구독일 때 해석된 채널명 */
  channelTitle: string | null;
  status: KeywordStatus;
  language: Language;
  schedule: Schedule;
  /** 이 길이 미만 영상은 강의로 보지 않음 (초) */
  minDurationSec: number;
  /** 이 점수 미만은 공개하지 않음 (0~100) */
  minExpertScore: number;
  /** 1회 실행당 요약 상한 — 초기 백로그가 예산을 태우는 것을 막는다 */
  maxPerRun: number;
  /** 이 키워드로 공개된 강의 수 */
  lectureCount: number;
  lastRunAt: string | null;
  createdAt: string;
  /** 삭제 영역으로 옮긴 시각. 활성 상태면 null */
  archivedAt: string | null;
}

/** 키워드 등록 폼이 보내는 값. id·상태·집계는 서버가 채운다. */
export interface KeywordDraft {
  term: string;
  sourceType: SourceType;
  language: Language;
  schedule: Schedule;
  minDurationSec: number;
  minExpertScore: number;
  maxPerRun: number;
}

// ── 강의 ──────────────────────────────────────────────────
export type Verdict =
  | "expert"
  | "practical"
  | "introductory"
  | "promotional"
  | "irrelevant";

export interface KeyPoint {
  heading: string;
  detail: string;
  timestampSec: number;
}

export interface Chapter {
  title: string;
  startSec: number;
  endSec: number;
}

export interface Term {
  term: string;
  definition: string;
}

export interface Quote {
  text: string;
  timestampSec: number;
  why: string;
}

export type Criterion =
  | "structure"
  | "depth"
  | "evidence"
  | "authority"
  | "density"
  | "commercial";

export interface CriterionScore {
  criterion: Criterion;
  score: number;
  evidence: string;
}

/** 목록 행에 필요한 만큼만. 상세는 별도 조회. */
export interface LectureSummary {
  videoId: string;
  title: string;
  channelTitle: string;
  durationSec: number;
  publishedAt: string;
  expertScore: number;
  verdict: Verdict;
  oneLiner: string;
  tags: string[];
  /** 길이 트랙의 눈금 위치 (초) */
  keyPointOffsets: number[];
  isFavorite: boolean;
  /** 읽었는지. 기본 정렬이 안 읽은 것을 앞에 둡니다. */
  isRead: boolean;
  /** 곳간에 들어온 시각(UTC ISO). "새로 온 것" 개수의 기준. */
  addedAt: string;
  /** 사용자가 직접 뺐는지. 뺀 것은 제외함에서만 보입니다. */
  isExcluded: boolean;
  keywordIds: string[];
}

/** 개요의 한 마디. 라벨은 강의마다 다릅니다 — 정해진 목록이 없습니다. */
export interface Beat {
  label: string;
  text: string;
}

/** 번호 붙은 섹션 하나 — 요약의 본체.
 *  예전의 개요·핵심 포인트·챕터가 여기로 합쳐졌습니다. */
export interface Section {
  title: string;
  startSec: number;
  bullets: string[];
}

export interface LectureDetail extends LectureSummary {
  youtubeUrl: string;
  abstract: string;
  /** 흐름의 마디. 비어 있으면 abstract 문단으로 떨어집니다(옛 데이터) */
  abstractBeats: Beat[];
  /** 요약의 본체. 비어 있으면 옛 형식이라 예전 배치로 떨어집니다 */
  sections: Section[];
  /** 글 맨 끝의 한 줄 요약 */
  closing: string;
  targetAudience: string;
  prerequisites: string[];
  keyPoints: KeyPoint[];
  chapters: Chapter[];
  terms: Term[];
  takeaways: string[];
  quotes: Quote[];
  coverageNote: string | null;
  /** 판정 근거 — 임계값 튜닝의 유일한 단서 */
  review: {
    model: string;
    promptVersion: string;
    confidence: "low" | "medium" | "high";
    criteria: CriterionScore[];
    redFlags: string[];
    speakerCredentials: string | null;
    inputTokens: number;
    outputTokens: number;
    turns: number;
  };
  /** 자막 원문 보관 만료일. 지났으면 null */
  transcriptExpiresAt: string | null;
}

// ── 실행 이력 ─────────────────────────────────────────────
/** queued = "지금 실행" 요청이 접수되어 워커를 기다리는 중 */
/** interrupted = 워커가 사이클 도중 멈춤. **실패가 아닙니다** — 남은 일은
 *  다음 사이클이 이어받으므로 사람이 손댈 것이 없습니다. */
export type RunStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "partial"
  | "failed"
  | "interrupted";

/** 한 트랙(검색·자막·요약)의 지금 상태. **셋이 나란히 돕니다** — 하나만
 *  보여 주면 나머지가 멈춘 것처럼 읽힙니다. */
export interface Track {
  key: string;
  label: string;
  status: "running" | "idle";
  waiting: number;
  runLabel: string | null;
  startedAt: string | null;
  /** 지금 붙들고 있는 영상. 이게 있어야 "도는 중"이 눈에 보입니다. */
  working: { title: string; since: string } | null;
  lastAt: string | null;
  /** 검색만 — 다음 차례 시각. */
  nextAt: string | null;
}

/** 파이프라인의 지금 상태. "기다리면 되는가, 손대야 하는가"의 근거. */
export interface Pipeline {
  funnel: { key: string; label: string; count: number }[];
  tracks: Track[];
  stuck: { key: string; label: string; count: number }[];
  transcriptCoolingUntil: string | null;
}

/** 실행 하나가 실제로 옮긴 것들. */
export interface RunEvent {
  at: string;
  stage: string;
  fromState: string | null;
  toState: string;
  ok: boolean;
  videoId: string;
  title: string;
  detail: string;
}
export type RunTrigger = "initial" | "scheduled" | "manual";

export interface RunStats {
  discovered: number;
  rulePassed: number;
  transcribed: number;
  reviewed: number;
  published: number;
}

/** 어느 잡이 만든 기록인가. 셋을 따로 돌리므로 구분이 필요합니다. */
export type RunJob = "discover" | "transcript" | "review" | "cycle";

export interface Run {
  job: RunJob;
  id: string;
  label: string;
  trigger: RunTrigger;
  status: RunStatus;
  startedAt: string;
  finishedAt: string | null;
  stats: RunStats;
  tokens: number;
  youtubeUnits: number;
  /** 실패 시 사용자에게 보여줄 설명. 코드가 아니라 문장. */
  error: string | null;
}

// ── 대시보드 ──────────────────────────────────────────────
export type FailureKind = "transcript" | "review" | "quota";

export interface Failure {
  kind: FailureKind;
  label: string;
  title: string;
  detail: string;
}

export interface KeywordContribution {
  keywordId: string;
  term: string;
  published: number;
}

export interface Overview {
  newToday: number;
  totalLectures: number;
  weekAdded: number;
  avgScore: number;
  queued: { transcript: number; review: number };
  funnel: RunStats;
  /** 조기 종료로 아낀 입력 토큰 — 통합 호출 구조가 작동하는지의 지표 */
  earlyExitCount: number;
  earlyExitSavedInputTokens: number;
  contributions: KeywordContribution[];
  failures: Failure[];
  lastRunAt: string;
}

export interface Usage {
  /** **이번 창**의 토큰. 하루 합계가 아닙니다. */
  inputTokens: number;
  outputTokens: number;
  /** 창당 상한. null 이면 상한 없음 */
  limitTokens: number | null;
  /** 창의 길이(시간). 구독 사용량이 이 주기로 풀립니다. */
  windowHours: number;
  windowResetsAt: string;
  /** 오늘 하루 합계 — 창과 별개로 "오늘 얼마나 했나"를 보는 값. */
  todayTokens: number;
  youtubeUnits: number;
  youtubeUnitLimit: number;
  resetsAt: string;
}

// ── 목록 조회 ─────────────────────────────────────────────
export type LectureSort = "unread" | "recent" | "score" | "duration";

export interface LectureQuery {
  keywordIds?: string[];
  minScore?: number;
  minDurationSec?: number;
  maxDurationSec?: number;
  q?: string;
  favoritesOnly?: boolean;
  sort?: LectureSort;
  /** 제외함을 봅니다. */
  excluded?: boolean;
}


/** 앞으로 처리할 영상 한 건. */
export interface QueueItem {
  videoId: string;
  title: string;
  channelTitle: string;
  durationSec: number;
  publishedAt: string | null;
  /** 이 영상을 데려온 키워드. 괄호는 지운 키워드입니다. */
  keywords: string[];
  /** 실제 처리 차례. 발견 단계는 차례가 정해지지 않아 null 입니다. */
  order: number | null;
  reason: string;
}

export interface QueueStage {
  key: string;
  label: string;
  count: number;
  totalSec: number;
  /** 받아쓰기만 어림합니다. 검토는 영상 길이로 재면 틀립니다. */
  etaSec: number | null;
  items: QueueItem[];
}

export interface Queue {
  stages: QueueStage[];
  skipped: QueueItem[];
  asrRealtimeFactor: number;
}