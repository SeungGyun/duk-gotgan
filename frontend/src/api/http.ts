import type { Api } from "./contract";
import { ApiError } from "./contract";
import type { ChannelBlock, Keyword, KeywordDraft, LectureQuery, Run } from "./types";

/**
 * 실제 REST 구현. 백엔드를 붙일 때 .env 에서 VITE_API=http 로 바꾸면 이쪽이 씁니다.
 * 경로와 형태는 docs/API.md 계약을 따릅니다.
 */

const BASE = "/api/v1";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
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
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const httpApi: Api = {
  listKeywords: () => req("/keywords"),

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
