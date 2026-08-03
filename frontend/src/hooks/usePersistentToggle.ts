import { useCallback, useState } from "react";

/**
 * 켜고 끈 상태를 브라우저에 기억합니다.
 * 읽기 화면의 목록·챕터 패널처럼 "한번 정해두면 계속 그대로 쓰는" 설정용.
 */
export function usePersistentToggle(
  key: string,
  fallback: boolean,
  /** 좁은 화면에서만 다른 기본값을 쓰고 싶을 때. 한 번이라도 직접 켜고
   *  끈 적이 있으면 그 값이 우선합니다 — 기억한 것을 화면 폭으로
   *  덮어쓰면 껐다 켤 때마다 되살아나 성가십니다. */
  narrowFallback?: boolean,
) {
  const [on, setOn] = useState<boolean>(() => {
    try {
      const raw = localStorage.getItem(key);
      if (raw !== null) return raw === "1";
    } catch {
      /* 사생활 보호 모드 등 — 기본값으로 갑니다 */
    }
    if (narrowFallback !== undefined && typeof window !== "undefined") {
      return window.matchMedia("(max-width: 1180px)").matches ? narrowFallback : fallback;
    }
    return fallback;
  });

  const toggle = useCallback(() => {
    setOn((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(key, next ? "1" : "0");
      } catch {
        /* 사생활 보호 모드 등 — 기억만 못 할 뿐 동작은 정상 */
      }
      return next;
    });
  }, [key]);

  return [on, toggle] as const;
}
