import { useCallback, useState } from "react";

/**
 * 켜고 끈 상태를 브라우저에 기억합니다.
 * 읽기 화면의 목록·챕터 패널처럼 "한번 정해두면 계속 그대로 쓰는" 설정용.
 */
export function usePersistentToggle(key: string, fallback: boolean) {
  const [on, setOn] = useState<boolean>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? fallback : raw === "1";
    } catch {
      return fallback;
    }
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
