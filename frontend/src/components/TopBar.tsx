import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import { api } from "../api";
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
  name,
  isOwner,
}: {
  usage: Usage | null;
  lectureCount: number | null;
  keywordCount: number | null;
  runCount: number | null;
  name: string;
  isOwner: boolean;
}) {
  const tucked = useTuckOnScroll();
  const used = usage ? usage.inputTokens + usage.outputTokens : 0;
  const limit = usage?.limitTokens ?? null;
  // 언제 풀리는지가 "지금 아껴야 하나"의 답입니다. 남은 시간이 짧으면
  // 상한에 가까워도 걱정할 일이 아닙니다.
  const resetIn = usage
    ? Math.max(0, Math.round((new Date(usage.windowResetsAt).getTime() - Date.now()) / 60000))
    : 0;
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
        <NavLink to="/queue" className={link}>
          대기 목록
        </NavLink>
        <NavLink to="/excluded" className={link}>
          제외함
        </NavLink>
        <NavLink to="/runs" className={link}>
          실행 로그 {runCount != null && <span className={s.count}>{runCount}</span>}
        </NavLink>
      </nav>

      {usage && (
        <div
          className={s.usage}
          title={`이번 ${usage.windowHours}시간 창에서 쓴 토큰 / 창당 상한`}
        >
          <span className={s.usageLabel}>
            {usage.windowHours}시간 토큰
          </span>
          <span className={s.usageValue}>{tokens(used)}</span>
          {limit && (
            <>
              <Meter value={used} max={limit} height={5} className={s.usageMeter} />
              <span className={s.usageCap}>
                {pct}% · {resetIn >= 60 ? `${Math.floor(resetIn / 60)}시간 ${resetIn % 60}분` : `${resetIn}분`} 뒤 초기화
              </span>
            </>
          )}
        </div>
      )}

      <Whoami name={name} isOwner={isOwner} />
      </div>
    </header>
  );
}

/** 지금 누가 보고 있는지, 그리고 바꾸기.
 *
 *  가족이 한 태블릿을 같이 쓰면 **누구로 들어와 있는지가 안 보이는 것**이
 *  가장 헷갈립니다 — 읽음 표시가 남의 것에 붙고 나서야 알게 됩니다. */
function Whoami({ name, isOwner }: { name: string; isOwner: boolean }) {
  const [busy, setBusy] = useState(false);

  const leave = async () => {
    setBusy(true);
    await api.leave().catch(() => {});
    // 셸 전체를 다시 그려야 하므로 통째로 다시 읽습니다. 라우터로만
    // 옮기면 이미 받아 둔 남의 목록이 화면에 남습니다.
    window.location.assign("/who");
  };

  return (
    <div className={s.whoami}>
      <button
        type="button"
        className={s.whoBtn}
        onClick={leave}
        disabled={busy}
        title="사용자 바꾸기 — 이 기기만 나갑니다"
      >
        <span className={`${s.whoDot} ${isOwner ? s.whoOwner : ""}`} aria-hidden="true">
          {name.slice(0, 1)}
        </span>
        <span className={s.whoName}>{name}</span>
      </button>
    </div>
  );
}
