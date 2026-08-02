import { useEffect, useState } from "react";

import { api } from "../api";
import type { Pipeline, Run, RunEvent, RunStats, Track } from "../api";
import { Screen } from "../components/Screen";
import { Chip, Empty, ErrorState, Loading, Panel } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { num, tokens } from "../lib/format";
import s from "./Runs.module.css";

/** 도는 중일 때 새로 고치는 간격. 한 사이클이 몇 분씩 걸려서, 화면이
    멈춰 있으면 "눌렀는데 아무 일도 안 일어난다"로 보입니다. */
const POLL_ACTIVE_MS = 5_000;
/** 놀고 있을 때. 정기 실행이 언제 시작될지 몰라 아주 끊지는 않습니다. */
const POLL_IDLE_MS = 30_000;

/** 잡마다 **자기가 한 일만** 보여 줍니다.
 *
 *  예전에는 실행마다 5단계 막대를 그렸습니다. 한 사이클이 발견→자막→요약을
 *  다 하던 때는 흐름이 보였지만, 셋을 따로 돌리는 지금은 한 실행이 한
 *  가지만 합니다. 그래서 다섯 칸 중 넷이 0 이고, 척도로 쓰던 "발견" 이
 *  0 이라 막대 폭이 300%까지 튀었습니다. */
const JOB_STATS: Record<string, { key: keyof RunStats; label: string }[]> = {
  discover: [
    { key: "discovered", label: "발견" },
    { key: "rulePassed", label: "룰 통과" },
  ],
  transcript: [{ key: "transcribed", label: "자막" }],
  review: [
    { key: "reviewed", label: "요약" },
    { key: "published", label: "공개" },
  ],
  cycle: [
    { key: "discovered", label: "발견" },
    { key: "transcribed", label: "자막" },
    { key: "reviewed", label: "요약" },
    { key: "published", label: "공개" },
  ],
};

/** **`interrupted` 는 실패가 아닙니다.** 워커가 사이클 도중에 멈춘 것이고
    (재시작·강제 종료), 남은 일은 다음 사이클이 그대로 이어받습니다.
    실패로 칠하면 손댈 것이 있는 것처럼 보여 헛걸음을 시킵니다. */
const statusChip: Record<Run["status"], { tone: "pass" | "warn" | "fail" | "neutral"; label: string }> =
  {
    succeeded: { tone: "pass", label: "완료" },
    partial: { tone: "warn", label: "일부 실패" },
    failed: { tone: "fail", label: "실패" },
    running: { tone: "neutral", label: "진행 중" },
    queued: { tone: "neutral", label: "대기 중" },
    interrupted: { tone: "neutral", label: "중단됨" },
  };

/** 잡 이름. 실행 로그에 셋이 섞여 나오므로 한눈에 갈려야 합니다. */
const jobLabel: Record<string, string> = {
  discover: "검색",
  transcript: "자막",
  review: "요약",
  cycle: "통합",
};

/** 파이프라인 단계 이름. **"검토"라고 쓰지 않습니다** — AI 가 더 이상
    심사해서 떨어뜨리지 않고 요약만 하므로, 검토는 하는 일과 맞지 않는
    말이 됐습니다. 화면 곳곳에서 같은 말을 써야 헷갈리지 않습니다. */
const stageLabel: Record<string, string> = {
  discover: "발견",
  transcript: "자막",
  review: "요약",
};

function clock(iso: string): string {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function ago(iso: string): string {
  const sec = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (sec < 60) return `${sec}초 전`;
  if (sec < 3600) return `${Math.round(sec / 60)}분 전`;
  return `${Math.round(sec / 3600)}시간 전`;
}

export function Runs() {
  const runs = useAsync(() => api.listRuns(), []);
  const pipe = useAsync(() => api.getPipeline(), []);

  // **한 번만 불러오면 안 됩니다.** "지금 실행"은 요청만 남기고 워커가
  // 다음 틱에 집어가는 구조라, 눌러 놓고 이 화면을 봐도 대기 중인 실행이
  // 나타나지 않았습니다. 진행 중인 것이 있으면 자주, 없으면 뜸하게 봅니다.
  // 트랙 중 하나라도 돌면 자주 봅니다. "지금 도는 실행" 하나만 보던
  // 때는 자막이 도는데도 요약 기준으로 판단해 갱신이 느렸습니다.
  const active = (pipe.data?.tracks ?? []).some((t) => t.status === "running");
  const { reload: reloadRuns } = runs;
  const { reload: reloadPipe } = pipe;
  useEffect(() => {
    const tick = () => {
      reloadRuns();
      reloadPipe();
    };
    const id = window.setInterval(tick, active ? POLL_ACTIVE_MS : POLL_IDLE_MS);
    return () => window.clearInterval(id);
  }, [active, reloadRuns, reloadPipe]);

  if (runs.error) return <ErrorState message={runs.error} onRetry={runs.reload} />;
  // 첫 로딩에만 스피너를 보입니다. `loading` 을 그대로 보면 5초마다
  // 화면이 통째로 깜빡여서, 새로 고치지 않는 것보다 읽기 나쁩니다.
  if (!runs.data) {
    return (
      <Screen title="실행 로그">
        <Loading />
      </Screen>
    );
  }

  const rows = runs.data;

  return (
    <Screen
      title="실행 로그"
      subtitle="지금 어디까지 왔는지, 기다리면 되는지"
    >
      {pipe.data && <Now p={pipe.data} />}

      <Panel title="지나간 실행" bodyless>
        {rows.length === 0 ? (
          <Empty>아직 실행 이력이 없습니다.</Empty>
        ) : (
          rows.map((r) => <RunRow key={r.id} run={r} />)
        )}
      </Panel>
    </Screen>
  );
}

/** 지금 상태. **이 화면에서 가장 중요한 부분입니다** — 실행 기록은 지나간
    일이지만, 사용자가 알고 싶은 것은 "지금 어디쯤이고 얼마나 남았나"입니다.

    셋을 따로 돌리게 된 뒤로 트랙을 각각 보여 줍니다. 하나만 보여 주면
    나머지가 멈춘 것처럼 읽힙니다 — 실제로 자막과 요약이 나란히 도는데
    화면에는 나중에 시작한 것만 떴습니다. */
function Now({ p }: { p: Pipeline }) {
  const cooling = p.transcriptCoolingUntil ? new Date(p.transcriptCoolingUntil) : null;
  const stuckTotal = p.stuck.reduce((a, x) => a + x.count, 0);

  return (
    <Panel title="지금" className={s.nowPanel}>
      <div className={s.funnel}>
        {p.funnel.map((st, i) => (
          <div key={st.key} className={s.stage}>
            {i > 0 && <span className={s.arrow} aria-hidden="true">→</span>}
            <span className={s.stageCount}>{num(st.count)}</span>
            <span className={s.stageLabel}>{st.label}</span>
          </div>
        ))}
      </div>

      <ul className={s.tracks}>
        {p.tracks.map((t) => (
          <TrackRow key={t.key} t={t} />
        ))}
      </ul>

      {/* 냉각은 실패가 아니라 기다리면 풀리는 상태입니다. 이 한 줄이
          "손대야 하나"에 대한 답이 됩니다. */}
      {cooling && cooling.getTime() > Date.now() && (
        <p className={s.paused}>
          유튜브 자막이 막혀 쉬는 중입니다 — {clock(p.transcriptCoolingUntil!)} 이후 재개.
          그동안은 소리를 받아 직접 받아씁니다.
        </p>
      )}

      {stuckTotal > 0 && (
        <p className={s.stuck}>
          손봐야 할 것:{" "}
          {p.stuck
            .filter((x) => x.count > 0)
            .map((x) => `${x.label} ${x.count}건`)
            .join(" · ")}
        </p>
      )}
    </Panel>
  );
}

function TrackRow({ t }: { t: Track }) {
  const running = t.status === "running";
  return (
    <li className={s.track}>
      <span className={running ? s.dotLive : s.dotIdle} aria-hidden="true" />
      <span className={s.trackName}>{t.label}</span>
      <span className={s.trackWait}>대기 {num(t.waiting)}</span>
      <span className={s.trackWhat}>
        {t.working ? (
          <>
            {t.working.title}
            <em className={s.trackSince}> · {ago(t.working.since)} 시작</em>
          </>
        ) : running ? (
          (t.runLabel ?? "도는 중")
        ) : t.nextAt ? (
          `쉬는 중 · 다음 차례 ${clock(t.nextAt)}`
        ) : t.lastAt ? (
          `쉬는 중 · 마지막 ${ago(t.lastAt)}`
        ) : (
          "쉬는 중"
        )}
      </span>
    </li>
  );
}

function RunRow({ run: r }: { run: Run }) {
  const [open, setOpen] = useState(false);
  const [events, setEvents] = useState<RunEvent[] | null>(null);
  const st = statusChip[r.status];

  // 상세는 **펼칠 때만** 받습니다. 50개 실행의 이벤트를 미리 받으면
  // 목록을 여는 것만으로 수천 줄을 끌어옵니다.
  useEffect(() => {
    if (!open || events) return;
    void api.listRunEvents(r.id).then(setEvents).catch(() => setEvents([]));
  }, [open, events, r.id]);

  return (
    <div className={s.runWrap}>
      <button
        type="button"
        className={s.run}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <div>
          <div className={s.when}>
            {r.startedAt.slice(0, 10)} {clock(r.startedAt)}
            {r.finishedAt && ` → ${clock(r.finishedAt)}`}
          </div>
          <div className={s.label}>
            <span className={s.job}>{jobLabel[r.job] ?? r.job}</span>
            {r.label}
          </div>
          <Chip tone={st.tone}>{st.label}</Chip>
          {/* 잡마다 쓰는 자원이 다릅니다. 자막은 토큰도 유닛도 안 쓰는데
              "0 토큰 · 0 유닛"이 붙어 있으면 읽을 것이 하나 늘 뿐입니다. */}
          {(r.tokens > 0 || r.youtubeUnits > 0) && (
            <div className={s.cost}>
              {[
                r.tokens > 0 && `${tokens(r.tokens)} 토큰`,
                r.youtubeUnits > 0 && `${num(r.youtubeUnits)} 유닛`,
              ]
                .filter(Boolean)
                .join(" · ")}
            </div>
          )}
        </div>

        <div>
          <div className={s.figures}>
            {(JOB_STATS[r.job] ?? JOB_STATS.cycle!).map((m) => (
              <div key={m.key} className={s.figure}>
                <span className={r.stats[m.key] ? s.figValue : s.figZero}>
                  {r.stats[m.key]}
                </span>
                <span className={s.figLabel}>{m.label}</span>
              </div>
            ))}
          </div>
          {r.error && <p className={s.error}>{r.error}</p>}
          <span className={s.caret}>{open ? "▲ 접기" : "▼ 무엇을 했는지"}</span>
        </div>
      </button>

      {open && (
        <div className={s.events}>
          {events === null ? (
            <p className={s.eventNote}>불러오는 중…</p>
          ) : events.length === 0 ? (
            <p className={s.eventNote}>이 실행에서 옮긴 영상이 없습니다.</p>
          ) : (
            <ul className={s.eventList}>
              {events.map((e, i) => (
                <li key={i} className={e.ok ? "" : s.eventBad}>
                  <span className={s.eventTime}>{clock(e.at)}</span>
                  <span className={s.eventStage}>{stageLabel[e.stage] ?? e.stage}</span>
                  <span className={s.eventTitle}>{e.title || e.videoId}</span>
                  <span className={s.eventTo}>{e.ok ? e.toState : e.detail || e.toState}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
