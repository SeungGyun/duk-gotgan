import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import type { Usage } from "../api";
import { tokens } from "../lib/format";
import { Meter } from "./ui";
import s from "./TopBar.module.css";

const TB_H = 54;

/**
 * 아래로 스크롤하면 상단바를 접고, 위로 올리거나 커서를 화면 맨 위로
 * 가져가면 편다. sticky 패널들이 같이 올라붙도록 --tb-off 도 함께 바꾼다.
 *
 * 모션 축소 설정에서는 자동 숨김 자체를 끈다.
 */
function useTuckOnScroll() {
  const [tucked, setTucked] = useState(false);
  const lastY = useRef(0);
  const nearTop = useRef(false);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    let queued = false;

    const apply = (next: boolean) => {
      setTucked(next);
      document.documentElement.style.setProperty("--tb-off", next ? "0px" : `${TB_H}px`);
    };

    const onFrame = () => {
      const y = window.scrollY;
      if (nearTop.current || y <= 8) apply(false);
      else if (y > lastY.current + 4 && y > 90) apply(true);
      else if (y < lastY.current - 4) apply(false);
      lastY.current = y;
      queued = false;
    };

    const onScroll = () => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(onFrame);
    };

    const onPointer = (e: PointerEvent) => {
      nearTop.current = e.clientY < 56;
      if (nearTop.current) apply(false);
    };

    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("pointermove", onPointer, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("pointermove", onPointer);
      document.documentElement.style.setProperty("--tb-off", `${TB_H}px`);
    };
  }, []);

  return tucked;
}

const link = ({ isActive }: { isActive: boolean }) =>
  `${s.link} ${isActive ? s.active : ""}`;

export function TopBar({
  usage,
  lectureCount,
  keywordCount,
  runCount,
}: {
  usage: Usage | null;
  lectureCount: number | null;
  keywordCount: number | null;
  runCount: number | null;
}) {
  const tucked = useTuckOnScroll();
  const used = usage ? usage.inputTokens + usage.outputTokens : 0;
  const limit = usage?.dailyLimitTokens ?? null;
  const pct = limit ? Math.round((used / limit) * 100) : null;

  return (
    <header
      className={`${s.bar} ${tucked ? s.tucked : ""}`}
      onFocus={() => document.documentElement.style.setProperty("--tb-off", `${TB_H}px`)}
    >
      <div className={s.inner}>
      <NavLink to="/" className={s.brand}>
        <span className={s.mark}>
          Duk<span className={s.markSep}>!</span>gotgan
        </span>
        <span className={s.name}>
          덕<span className={s.sep}>!</span>곳간
        </span>
      </NavLink>

      <nav className={s.nav} aria-label="주요 화면">
        <NavLink to="/" end className={link}>
          대시보드
        </NavLink>
        <NavLink to="/lectures" className={link}>
          덕질 {lectureCount != null && <span className={s.count}>{lectureCount}</span>}
        </NavLink>
        <NavLink to="/keywords" className={link}>
          키워드 {keywordCount != null && <span className={s.count}>{keywordCount}</span>}
        </NavLink>
        <NavLink to="/excluded" className={link}>
          제외함
        </NavLink>
        <NavLink to="/runs" className={link}>
          실행 로그 {runCount != null && <span className={s.count}>{runCount}</span>}
        </NavLink>
      </nav>

      {usage && (
        <div className={s.usage} title="오늘 사용한 토큰 / 일일 상한">
          <span className={s.usageLabel}>오늘 토큰</span>
          <span className={s.usageValue}>{tokens(used)}</span>
          {limit && (
            <>
              <Meter value={used} max={limit} height={5} className={s.usageMeter} />
              <span className={s.usageCap}>
                {pct}% · 상한 {tokens(limit)}
              </span>
            </>
          )}
        </div>
      )}
      </div>
    </header>
  );
}
