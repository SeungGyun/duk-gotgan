import type { Api } from "./contract";
import { ApiError } from "./contract";
import type {
  ChannelBlock,
  Keyword,
  KeywordDraft,
  LectureQuery,
  Person,
  PersonDraft,
  Run,
} from "./types";

/**
 * 실제 REST 구현. 백엔드를 붙일 때 .env 에서 VITE_API=http 로 바꾸면 이쪽이 씁니다.
 * 경로와 형태는 docs/API.md 계약을 따릅니다.
 */

const BASE = "/api/v1";

/** 선택 화면의 주소. 401 을 받으면 여기로 보냅니다. */
export const WHO = "/who";

/** 누구인지 모르는 상태에서도 불러야 하는 것들.
 *
 *  이 목록에 없는 요청이 401 을 받으면 선택 화면으로 튕깁니다. 여기까지
 *  튕기면 **선택 화면이 자기 자신을 부르다가 무한히 새로 고칩니다.** */
const OPEN = new Set(["/users", "/session"]);

/** 로그인 없이 열리는 화면들.
 *
 *  **여기 있는 화면에서는 튕기지 않습니다.** 셸이 화면과 무관하게 `/me` 를
 *  한 번 부르는데, 쿠키가 없으면 그게 401 로 끝납니다. 그 401 로 무조건
 *  선택 화면으로 보내면 소개를 열자마자 곧바로 끌려 나갑니다 — 실제로
 *  `/about` 이 열리지 않았습니다. */
const PUBLIC_PAGES = new Set([WHO, "/about"]);

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      // 쿠키는 서버가 HttpOnly 로 심습니다 — 자바스크립트가 읽지도 쓰지도
      // 않습니다. 사파리가 스크립트로 심은 쿠키를 7일 만에 지우기 때문에,
      // 그렇게 해야 만료가 2년으로 유지됩니다.
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    throw new ApiError("서버에 연결할 수 없습니다. 백엔드가 실행 중인지 확인하세요.");
  }

  if (res.status === 204) return undefined as T;

  const body = await res.json().catch(() => null);

  if (!res.ok) {
    // **401 처리는 여기 한 곳뿐입니다.** 모든 호출이 이 함수를 지나가서,
    // 화면마다 "로그인 됐나" 를 챙길 필요가 없습니다.
    const base = path.split("?")[0]!;
    if (res.status === 401 && !OPEN.has(base) && !PUBLIC_PAGES.has(window.location.pathname)) {
      window.location.assign(WHO);
    }
    const err = body?.error;
    throw new ApiError(
      err?.message ?? `요청이 실패했습니다 (${res.status})`,
      res.status,
      err?.code,
    );
  }
  return body as T;
}

function qs(query: LectureQuery): string {
  const p = new URLSearchParams();
  if (query.keywordIds?.length) p.set("keyword_ids", query.keywordIds.join(","));
  if (query.minScore != null) p.set("min_score", String(query.minScore));
  if (query.minDurationSec != null) p.set("min_duration_sec", String(query.minDurationSec));
  if (query.maxDurationSec != null) p.set("max_duration_sec", String(query.maxDurationSec));
  if (query.q?.trim()) p.set("q", query.q.trim());
  if (query.favoritesOnly) p.set("favorites_only", "true");
  if (query.sort) p.set("sort", query.sort);
  if (query.excluded) p.set("excluded", "true");
  if (query.limit != null) p.set("limit", String(query.limit));
  if (query.offset) p.set("offset", String(query.offset));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const httpApi: Api = {
  listPeople: () => req("/users"),

  pickPerson: (id, pin) =>
    req<Person>("/session", {
      method: "POST",
      body: JSON.stringify({ userId: id, pin: pin ?? null }),
    }),

  createPerson: (draft: PersonDraft) =>
    req<Person>("/users", { method: "POST", body: JSON.stringify(draft) }),

  leave: () => req<void>("/session", { method: "DELETE" }),

  getMe: () => req("/me"),

  renameMe: (name) =>
    req<Person>("/me", { method: "PATCH", body: JSON.stringify({ name }) }),

  setPin: (current, next) =>
    req<void>("/me/pin", { method: "PUT", body: JSON.stringify({ current, next }) }),

  listKeywords: () => req("/keywords"),

  listAllKeywords: () => req("/keywords?mine=false"),

  subscribeKeyword: (id) => req<Keyword>(`/keywords/${id}/subscribe`, { method: "POST" }),

  listArchivedKeywords: () => req("/keywords?archived=true"),

  createKeyword: (draft: KeywordDraft) =>
    req<Keyword>("/keywords", { method: "POST", body: JSON.stringify(draft) }),

  updateKeyword: (id, patch) =>
    req<Keyword>(`/keywords/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),

  setKeywordStatus: (id, status) =>
    req<Keyword>(`/keywords/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }),

  deleteKeyword: (id) => req<void>(`/keywords/${id}`, { method: "DELETE" }),

  restoreKeyword: (id) => req<Keyword>(`/keywords/${id}/restore`, { method: "POST" }),

  listLectures: (query) => req(`/lectures${qs(query)}`),

  getLecture: (videoId) => req(`/lectures/${videoId}`),

  setFavorite: (videoId, isFavorite) =>
    req<void>(`/lectures/${videoId}`, {
      method: "PATCH",
      body: JSON.stringify({ isFavorite }),
    }),

  countNewLectures: async (query, since) => {
    // 정렬은 개수와 무관하니 뺍니다 — 조건이 같아야 화면에 안 나올 것을
    // 두고 새로 왔다고 알리는 일이 없습니다.
    const base = qs({ ...query, sort: undefined });
    const url = `/lectures/updates${base ? base + "&" : "?"}since=${encodeURIComponent(since)}`;
    const { count } = await req<{ count: number }>(url);
    return count;
  },

  setExcluded: (videoId, isExcluded) =>
    req<void>(`/lectures/${videoId}`, {
      method: "PATCH",
      body: JSON.stringify({ isExcluded }),
    }),

  deleteLecture: (videoId) => req<void>(`/lectures/${videoId}`, { method: "DELETE" }),

  markRead: (videoId) =>
    req<void>(`/lectures/${videoId}`, {
      method: "PATCH",
      body: JSON.stringify({ isRead: true }),
    }),

  getOverview: () => req("/stats/overview"),
  getUsage: () => req("/stats/usage"),
  setTokenLimit: (limitTokens, provider) =>
    req<void>("/stats/usage/limit", {
      method: "PUT",
      body: JSON.stringify({ limitTokens, provider }),
    }),
  inheritTokenLimit: (provider) =>
    req<void>("/stats/usage/limit", {
      method: "PUT",
      body: JSON.stringify({ provider, inherit: true }),
    }),
  listRuns: () => req("/runs"),
  getPipeline: () => req("/stats/pipeline"),
  listRunEvents: (runId) => req(`/runs/${runId}/events`),

  getQueue: () => req("/queue"),
  skipQueued: (videoId) => req<void>(`/queue/${videoId}/skip`, { method: "POST" }),
  restoreQueued: (videoId) => req<void>(`/queue/${videoId}/restore`, { method: "POST" }),

  requestRun: () => req<Run>("/runs", { method: "POST" }),

  listChannelBlocks: () => req("/channels/blocks"),

  blockChannel: (handle, reason) =>
    req<ChannelBlock>("/channels/blocks", {
      method: "POST",
      body: JSON.stringify({ handle, reason: reason ?? "" }),
    }),

  unblockChannel: (channelId) =>
    req<void>(`/channels/blocks/${channelId}`, { method: "DELETE" }),
};
