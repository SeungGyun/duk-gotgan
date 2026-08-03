import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import { api } from "../api";
import type { Me, Usage } from "../api";
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
  me,
  onMeChanged,
}: {
  usage: Usage | null;
  lectureCount: number | null;
  keywordCount: number | null;
  runCount: number | null;
  me: Me;
  onMeChanged: () => void;
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
      {/* 이름표는 덕질로 갑니다 — 여기가 메인입니다. */}
      <NavLink to="/lectures" className={s.brand}>
        <span className={s.mark}>
          Duk<span className={s.markSep}>!</span>gotgan
        </span>
        <span className={s.name}>
          덕<span className={s.sep}>!</span>곳간
        </span>
      </NavLink>

      {/* **읽는 화면이 먼저, 기계 화면이 나중입니다.**
          식구에게는 뒤쪽 셋이 아예 안 보입니다 — 눌러도 못 들어가는 메뉴가
          걸려 있으면 "왜 나만 안 되지" 가 되고, 볼 수 있는 것과 없는 것을
          매번 가려 읽어야 합니다. */}
      <nav className={s.nav} aria-label="주요 화면">
        <NavLink to="/lectures" className={link}>
          덕질 {lectureCount != null && <span className={s.count}>{lectureCount}</span>}
        </NavLink>
        <NavLink to="/keywords" className={link}>
          키워드 {keywordCount != null && <span className={s.count}>{keywordCount}</span>}
        </NavLink>
        <NavLink to="/excluded" className={link}>
          제외함
        </NavLink>

        {me.isOwner && (
          <>
            <span className={s.navSplit} aria-hidden="true" />
            <NavLink to="/dashboard" className={link}>
              대시보드
            </NavLink>
            <NavLink to="/queue" className={link}>
              대기 목록
            </NavLink>
            <NavLink to="/runs" className={link}>
              실행 로그 {runCount != null && <span className={s.count}>{runCount}</span>}
            </NavLink>
          </>
        )}
      </nav>

      {usage && me.isOwner && (
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

      <Whoami me={me} onChanged={onMeChanged} />
      </div>
    </header>
  );
}

/** 지금 누가 보고 있는지. 누르면 비밀번호 바꾸기와 사용자 바꾸기가 나옵니다.
 *
 *  **처음에는 누르면 곧바로 로그아웃이었습니다.** 그런데 이름 말고는
 *  계정을 만질 데가 없어서, 비밀번호를 바꾸려면 어디로 가야 하는지 알
 *  방법이 없었습니다 — 실제로 "비번은 어디서 바꾸는거야?" 를 듣고 고쳤습니다.
 *  게다가 잘못 누르면 그대로 나가지던 것도 좋지 않았습니다. */
function Whoami({ me, onChanged }: { me: Me; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  // 바깥을 누르거나 Esc 로 닫습니다. 열어 놓고 다른 데를 눌렀는데 그대로
  // 떠 있으면 화면을 가립니다.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // 첫 비밀번호 그대로면 펼친 채로 시작합니다 — 바꾸라고 띠까지 띄워 놓고
  // 한 번 더 누르게 할 이유가 없습니다.
  useEffect(() => {
    if (me.pinIsDefault) setOpen(true);
  }, [me.pinIsDefault]);

  return (
    <div className={s.whoami} ref={box}>
      <button
        type="button"
        className={s.whoBtn}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title="계정"
      >
        <span className={`${s.whoDot} ${me.isOwner ? s.whoOwner : ""}`} aria-hidden="true">
          {me.name.slice(0, 1)}
        </span>
        <span className={s.whoName}>{me.name}</span>
      </button>

      {open && <Account me={me} onChanged={onChanged} onClose={() => setOpen(false)} />}
    </div>
  );
}

function Account({
  me,
  onChanged,
  onClose,
}: {
  me: Me;
  onChanged: () => void;
  onClose: () => void;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const only4 = (v: string) => v.replace(/\D/g, "").slice(0, 4);

  const save = async () => {
    if (next.length !== 4) return setError("비밀번호는 숫자 네 자리입니다.");
    setBusy(true);
    setError(null);
    try {
      await api.setPin(me.hasPin ? current : null, next);
      setDone(true);
      setCurrent("");
      setNext("");
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "바꾸지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  const leave = async () => {
    setBusy(true);
    await api.leave().catch(() => {});
    // 셸 전체를 다시 그려야 하므로 통째로 다시 읽습니다. 라우터로만
    // 옮기면 이미 받아 둔 남의 목록이 화면에 남습니다.
    window.location.assign("/who");
  };

  return (
    <div className={s.menu} role="dialog" aria-label="계정">
      <div className={s.menuHead}>
        <b>{me.name}</b>
        <span>{me.isOwner ? "주인" : "식구"}</span>
      </div>

      <div className={s.menuBody}>
        <span className={s.menuLabel}>
          비밀번호 {me.hasPin ? "바꾸기" : "걸기"}
          {me.pinIsDefault && <em className={s.menuWarn}>지금 0000</em>}
        </span>

        {done ? (
          <p className={s.menuDone}>바꿨습니다.</p>
        ) : (
          <>
            {me.hasPin && (
              <input
                className={s.menuInput}
                type="tel"
                inputMode="numeric"
                value={current}
                onChange={(e) => setCurrent(only4(e.target.value))}
                placeholder="지금 비밀번호"
                aria-label="지금 비밀번호"
              />
            )}
            <input
              className={s.menuInput}
              type="tel"
              inputMode="numeric"
              value={next}
              onChange={(e) => setNext(only4(e.target.value))}
              onKeyDown={(e) => e.key === "Enter" && void save()}
              placeholder="새 비밀번호 네 자리"
              aria-label="새 비밀번호"
            />
            <button
              type="button"
              className={s.menuGo}
              onClick={() => void save()}
              disabled={busy}
            >
              저장
            </button>
          </>
        )}

        {error && <p className={s.menuErr}>{error}</p>}
      </div>

      <div className={s.menuFoot}>
        <button type="button" className={s.menuLeave} onClick={() => void leave()} disabled={busy}>
          사용자 바꾸기
        </button>
        <button type="button" className={s.menuClose} onClick={onClose}>
          닫기
        </button>
      </div>
    </div>
  );
}
