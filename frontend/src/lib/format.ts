/**
 * 초 → 1:34:05 / 48:20 / 00:00
 * 분을 항상 두 자리로 맞춥니다 — 모노스페이스로 세로 정렬되어야 하는
 * 자리(챕터 타임라인, 타임스탬프 칩)가 많습니다.
 */
export function duration(sec: number): string {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** 초 → [MM:SS] 형태의 타임스탬프 라벨 */
export function timestamp(sec: number): string {
  return duration(sec);
}

/** 207000 → "207K", 1_400_000 → "1.4M" */
export function tokens(n: number): string {
  if (n >= 1_000_000) {
    const v = n / 1_000_000;
    return `${v >= 10 ? Math.round(v) : v.toFixed(1)}M`;
  }
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return String(n);
}

export function num(n: number): string {
  return n.toLocaleString("ko-KR");
}

/** ISO → "2026-06-18" */
export function date(iso: string): string {
  return iso.slice(0, 10);
}

/** ISO → "오늘 04:02" / "어제 04:11" / "7월 12일" */
export function when(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";

  const startOfDay = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOfDay(new Date()) - startOfDay(d)) / 864e5);
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;

  if (days === 0) return `오늘 ${hm}`;
  if (days === 1) return `어제 ${hm}`;
  return `${d.getMonth() + 1}월 ${d.getDate()}일`;
}

/** 유튜브 원본의 해당 시점으로 가는 링크 */
export function youtubeAt(url: string, sec: number): string {
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}t=${sec}`;
}

export const scoreColor = (score: number): string =>
  score >= 85 ? "var(--seq-5)" : score >= 78 ? "var(--seq-4)" : "var(--seq-3)";

export const verdictLabel: Record<string, string> = {
  expert: "전문가",
  practical: "실무",
  introductory: "개론",
  promotional: "홍보",
  irrelevant: "무관",
};

export const criterionLabel: Record<string, string> = {
  structure: "구조성",
  depth: "깊이",
  evidence: "근거",
  authority: "화자",
  density: "밀도",
  commercial: "상업성",
};

export const scheduleLabel: Record<string, string> = {
  daily: "매일 04:00",
  twice_weekly: "주 2회",
  weekly: "주 1회",
};

/**
 * 검색 기간을 사람 말로 — 90 → "3개월", 7 → "1주", 1 → "1일".
 *
 * 서버의 `rules.window_label` 과 같은 규칙입니다. 탈락 사유("오래됨 ·
 * 기준 1일 이내")와 화면이 다른 말을 쓰면, 같은 값인지 한 번 더 세어
 * 봐야 합니다.
 */
export function windowLabel(days: number): string {
  if (days >= 30 && days % 30 === 0) return `${days / 30}개월`;
  if (days >= 7 && days % 7 === 0) return `${days / 7}주`;
  return `${days}일`;
}

export const languageLabel: Record<string, string> = {
  ko: "한국어",
  en: "영어",
  any: "언어 무관",
};
