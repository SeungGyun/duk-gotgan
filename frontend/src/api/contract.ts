import type {
  ChannelBlock,
  Keyword,
  KeywordDraft,
  LectureDetail,
  LectureQuery,
  LectureSummary,
  Overview,
  Run,
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
  // 키워드
  listKeywords(): Promise<Keyword[]>;
  /** 삭제 영역. 지운 것만, 최근 것부터. */
  listArchivedKeywords(): Promise<Keyword[]>;
  /** 등록. 서버는 status=pending 으로 만들고 곧 첫 수집을 돌립니다. */
  createKeyword(draft: KeywordDraft): Promise<Keyword>;
  updateKeyword(id: string, patch: Partial<KeywordDraft>): Promise<Keyword>;
  setKeywordStatus(id: string, status: "active" | "paused"): Promise<Keyword>;
  /** 지우지 않고 삭제 영역으로 옮깁니다. 모은 강의와 연결은 그대로 남습니다. */
  deleteKeyword(id: string): Promise<void>;
  /** 삭제 영역에서 되살립니다. 돌아갈 상태는 서버가 정합니다. */
  restoreKeyword(id: string): Promise<Keyword>;

  // 강의
  listLectures(query: LectureQuery): Promise<LectureSummary[]>;
  getLecture(videoId: string): Promise<LectureDetail>;
  setFavorite(videoId: string, isFavorite: boolean): Promise<void>;
  /** 읽음 표시. 목록을 다시 부르지 않으므로 보는 중에 순서가 흔들리지 않습니다. */
  markRead(videoId: string): Promise<void>;

  // 운영
  getOverview(): Promise<Overview>;
  getUsage(): Promise<Usage>;
  listRuns(): Promise<Run[]>;
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
