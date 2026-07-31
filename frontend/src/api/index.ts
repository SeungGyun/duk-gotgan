import type { Api } from "./contract";
import { httpApi } from "./http";
import { mockApi } from "./mock";

/**
 * 화면 코드는 이 `api` 하나만 import 합니다.
 * 어느 구현이 붙었는지는 .env 의 VITE_API 가 결정합니다.
 */
export const apiMode: "mock" | "http" =
  import.meta.env.VITE_API === "http" ? "http" : "mock";

export const api: Api = apiMode === "http" ? httpApi : mockApi;

export { ApiError } from "./contract";
export type { Api } from "./contract";
export * from "./types";
