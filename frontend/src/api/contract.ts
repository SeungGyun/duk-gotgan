import type {
  ChannelBlock,
  Keyword,
  KeywordDraft,
  LectureDetail,
  LecturePage,
  LectureQuery,
  Me,
  Overview,
  Person,
  PersonDraft,
  Pipeline,
  Queue,
  Run,
  RunEvent,
  Usage,
} from "./types";

/**
 * UI 가 백엔드에 기대하는 전부.
 *
 * 구현이 두 개 있습니다.
 *   mock.ts — 브라우저 메모리. 백엔드 없이 UI 전체가 동작합니다.
 *   http.ts — REST 호출. 실제 테이블에 붙일 때 이쪽으로 전환합니다.
 *
 * 전환은 .env 의 VITE_API 값 하나로 끝납니다. 화면 코드는 이 인터페이스만
 * 알고 있으므로 어느 구현이 붙었는지 모릅니다.
 */
export interface Api {
  // 사람 — **`listPeople` 만 로그인 없이 부를 수 있습니다.**
  // 그게 로그인 전 화면이라 그렇습니다.
  listPeople(): Promise<Person[]>;
  /** 이 사람으로 들어갑니다. 비밀번호를 안 건 사람은 pin 없이. */
  pickPerson(id: string, pin?: string): Promise<Person>;
  /** 새로 만들고 바로 그 사람으로 들어갑니다. */
  createPerson(draft: PersonDraft): Promise<Person>;
  /** 사용자 바꾸기 — 이 기기만 나갑니다. 계정은 그대로. */
  leave(): Promise<void>;
  /** 지금 누구인지. 쿠키가 없으면 401 이 나고 선택 화면으로 갑니다. */
  getMe(): Promise<Me>;
  renameMe(name: string): Promise<Person>;
  /** 비밀번호를 걸거나 바꿉니다. next 가 null 이면 풀기(관리자는 불가). */
  setPin(current: string | null, next: string | null): Promise<void>;

  // 키워드
  listKeywords(): Promise<Keyword[]>;
  /** 아직 구독하지 않은 것까지. 새로 온 사람이 고를 목록입니다. */
  listAllKeywords(): Promise<Keyword[]>;
  /** 이미 있는 키워드를 내 것으로. **수집 비용이 늘지 않습니다.** */
  subscribeKeyword(id: string): Promise<Keyword>;
  /** 삭제 영역. 지운 것만, 최근 것부터. */
  listArchivedKeywords(): Promise<Keyword[]>;
  /** 등록. 서버는 status=pending 으로 만들고 곧 첫 수집을 돌립니다. */
  createKeyword(draft: KeywordDraft): Promise<Keyword>;
  updateKeyword(id: string, patch: Partial<KeywordDraft>): Promise<Keyword>;
  setKeywordStatus(id: string, status: "active" | "paused"): Promise<Keyword>;
  /** **내가 만든 것만.** 지우지 않고 삭제 영역으로 옮깁니다. 모은 강의와
   *  연결은 그대로 남습니다. 남이 만든 것이면 403 `NOT_KEYWORD_AUTHOR`. */
  deleteKeyword(id: string): Promise<void>;
  /** 남이 만든 키워드를 **내 목록에서만** 뺍니다. 만든 사람이 아직 보고
   *  있으므로 **수집은 그대로 돌고**, 삭제 영역에도 가지 않습니다 — 다시
   *  담는 자리는 "다른 사람도 보는 키워드" 입니다.
   *
   *  나 혼자 보던 것이었다면 서버가 수집을 멈추고 삭제 영역에 남깁니다.
   *  어느 쪽이었는지는 돌려받은 `status` 로 압니다. */
  excludeKeyword(id: string): Promise<Keyword>;
  /** 삭제 영역에서 되살립니다. 돌아갈 상태는 서버가 정합니다. */
  restoreKeyword(id: string): Promise<Keyword>;

  // 강의
  /** 목록 한 쪽. **전체를 다 주지 않습니다** — 809편이 374KB 였습니다.
   *  `limit`·`offset` 으로 끊어 받고, 개수와 최신 시각은 전체 기준으로 옵니다. */
  listLectures(query: LectureQuery): Promise<LecturePage>;
  getLecture(videoId: string): Promise<LectureDetail>;
  setFavorite(videoId: string, isFavorite: boolean): Promise<void>;
  /** 읽음 표시. 목록을 다시 부르지 않으므로 보는 중에 순서가 흔들리지 않습니다. */
  markRead(videoId: string): Promise<void>;
  /** 화면을 켜 둔 사이 새로 들어온 편수. 목록 전체를 다시 받지 않습니다. */
  countNewLectures(query: LectureQuery, since: string): Promise<number>;
  /** 제외하거나 되돌립니다. 뺀 것은 제외함에서 볼 수 있습니다. */
  setExcluded(videoId: string, isExcluded: boolean): Promise<void>;
  /** 완전삭제 — 요약을 지우고 다시 수집하지 않습니다. 되돌릴 수 없습니다. */
  deleteLecture(videoId: string): Promise<void>;

  // 운영
  getOverview(): Promise<Overview>;
  getUsage(): Promise<Usage>;
  /** 토큰 상한을 바꿉니다. **관리자만 됩니다** (식구는 403).
   *
   *  0 이면 무제한, null 이면 설정 기본값으로. `provider` 를 주면 그 회사만
   *  바뀌고, 안 주면 공용 값이 바뀝니다 — 자기 값이 없는 회사가 물려받습니다. */
  setTokenLimit(limitTokens: number | null, provider?: string): Promise<void>;
  /** 이 회사만 걸어 둔 상한을 지우고 공용 값으로 되돌립니다. 관리자만 됩니다. */
  inheritTokenLimit(provider: string): Promise<void>;
  listRuns(): Promise<Run[]>;
  /** 지금 파이프라인 상태 — 각 칸의 대기 수와 지금 하는 일. */
  getPipeline(): Promise<Pipeline>;
  /** 실행 하나의 상세 흐름. */
  listRunEvents(runId: string): Promise<RunEvent[]>;

  // 대기 목록
  getQueue(): Promise<Queue>;
  /** 처리 전에 뺍니다 — 받아쓰기도 검토도 하지 않습니다. */
  skipQueued(videoId: string): Promise<void>;
  restoreQueued(videoId: string): Promise<void>;
  /** "지금 실행" — 요청만 남깁니다. 워커가 다음 틱에 집어갑니다. */
  requestRun(): Promise<Run>;

  // 채널
  listChannelBlocks(): Promise<ChannelBlock[]>;
  blockChannel(handle: string, reason?: string): Promise<ChannelBlock>;
  unblockChannel(channelId: string): Promise<void>;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
