import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import type {
  BlogStatus,
  Pipeline,
  Reviewer,
  Run,
  RunEvent,
  RunStats,
  RunnableJob,
  Track,
} from "../api";
import { Screen } from "../components/Screen";
import { Button, Chip, Empty, ErrorState, Loading, Panel } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { num, tokens } from "../lib/format";
import { useMe } from "../me";
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
  // 발행은 이 목록에 오지 않습니다(`/runs` 가 거릅니다). 그래도 남겨
  // 둡니다 — 없으면 `cycle` 로 떨어져서, 한 편 올린 기록에 "발견 0 ·
  // 자막 0 · 요약 0" 세 칸이 붙습니다. 안 한 일을 0 으로 적는 셈입니다.
  publish: [{ key: "published", label: "발행" }],
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
  publish: "블로그",
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
  return `${span(Date.now() - new Date(iso).getTime())} 전`;
}

/** 시작한 지 얼마나 됐나 — "9분째". "9분 전 시작"보다 한 낱말 짧고,
    지금도 하고 있다는 뜻이 같이 담깁니다. */
function elapsed(iso: string): string {
  return `${span(Date.now() - new Date(iso).getTime())}째`;
}

/** 그때까지 얼마나 남았나 — "37분 뒤". 이미 지났으면 "곧" 입니다.
    다음 차례를 적어 두고 그 시각이 지나도 아무 일이 없는 것이 흔한데,
    "-2분 뒤"로 보이면 화면이 고장 난 것처럼 읽힙니다. */
function left(iso: string): string {
  const ms = new Date(iso).getTime() - Date.now();
  return ms <= 0 ? "곧" : `${span(ms)} 뒤`;
}

function span(ms: number): string {
  const sec = Math.max(0, Math.round(ms / 1000));
  if (sec < 60) return `${sec}초`;
  if (sec < 3600) return `${Math.round(sec / 60)}분`;
  return `${Math.round(sec / 3600)}시간`;
}

/** 확인 주기를 사람 말로. 30 → "30초마다", 60 → "1분마다". */
function every(sec: number): string {
  return sec < 60 ? `${sec}초마다` : `${Math.round(sec / 60)}분마다`;
}

/** 한 편을 이보다 오래 붙들고 있으면 붙들린 것으로 봅니다.
    워커의 워치독과 **같은 값입니다**(scripts/worker.py STALL_FLOOR_SEC).
    갈라 두면 로그는 경고를 내는데 화면은 아무 말도 안 하게 됩니다. */
const STUCK_MIN = 30;

export function Runs() {
  const runs = useAsync(() => api.listRuns(), []);
  const pipe = useAsync(() => api.getPipeline(), []);
  // **시작 버튼이 여기 있는 이유.** 대시보드에 "지금 실행" 하나를 두었을
  // 때는 그 버튼이 무엇을 하는지가 화면 어디에도 없었습니다(검색만
  // 돌립니다). 무엇이 도는 중이고 언제 다음인지를 말하는 화면이 여기가
  // 됐으니, 앞당기는 자리도 같은 줄이 맞습니다.
  const me = useMe();
  const [starting, setStarting] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const say = useCallback((msg: string) => {
    setNote(msg);
    window.setTimeout(() => setNote(null), 6000);
  }, []);

  const start = useCallback(
    async (job: RunnableJob, label: string) => {
      setStarting(job);
      try {
        await api.requestRun(job);
        // 워커가 다음 틱(30~60초)에 집어갑니다. 여기서 기다리지 않습니다 —
        // 한 사이클이 몇 분씩 걸려서 브라우저가 먼저 끊깁니다.
        say(`${label} 을(를) 시작하라고 알렸습니다. 곧 이 줄이 도는 중으로 바뀝니다.`);
        pipe.reload();
        runs.reload();
      } catch (e) {
        say(e instanceof Error ? e.message : "요청에 실패했습니다.");
      } finally {
        setStarting(null);
      }
    },
    [pipe, runs, say],
  );

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
      {note && (
        <p className={s.note} role="status">
          {note}
        </p>
      )}
      {pipe.data && (
        <Now
          p={pipe.data}
          canStart={me.isOwner}
          starting={starting}
          onStart={start}
          onFixed={(msg) => {
            say(msg);
            pipe.reload();
          }}
        />
      )}
      {pipe.data?.blog?.enabled && <BlogPanel b={pipe.data.blog} />}

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
type StartFn = (job: RunnableJob, label: string) => void;

function Now({
  p,
  canStart,
  starting,
  onStart,
  onFixed,
}: {
  p: Pipeline;
  canStart: boolean;
  starting: string | null;
  onStart: StartFn;
  onFixed: (msg: string) => void;
}) {
  const stuck = p.stuck.filter((x) => x.count > 0);

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
          <TrackRow
            key={t.key}
            t={t}
            reviewers={t.key === "review" ? p.reviewers : undefined}
            canStart={canStart}
            starting={starting}
            onStart={onStart}
          />
        ))}
        {/* 블로그도 한 줄을 줍니다. 트랙 셋만 있던 때는 5분에 한 편씩
            글이 나가는 중에도 이 패널이 아무 말을 하지 않아서, 발행이
            도는지 멎었는지 화면으로는 알 길이 없었습니다. */}
        {p.blog?.enabled && (
          <BlogRow b={p.blog} canStart={canStart} starting={starting} onStart={onStart} />
        )}
      </ul>

      {stuck.length > 0 && (
        <FailedPanel stuck={stuck} canFix={canStart} onFixed={onFixed} />
      )}

      {/* 스스로 손본 것. 조용히 지나가는 것이 정상이라 있을 때만 나옵니다. */}
      {p.upkeep && <p className={s.upkeep}>스스로 손봄 · {p.upkeep}</p>}
    </Panel>
  );
}

/** 실패한 것들 — **여기서 바로 손댈 수 있어야 합니다.**

    예전에는 "손봐야 할 것: 자막 실패 107건 · 요약 실패 58건" 한 줄이
    전부였습니다. 세어서 보여 주기만 하고 손댈 자리는 없었으니, 읽고 나면
    할 수 있는 일이 터미널을 여는 것뿐이었습니다
    (`scripts/revive_transcripts.py` 가 그래서 생겼습니다).

    **요약 실패는 대개 그 편의 문제가 아닙니다** — 세션이 죽었거나 모델이
    스키마를 어긴 것이라, 고쳐 놓고 통째로 다시 돌리면 그냥 됩니다. 그
    자리가 여기입니다. 반대로 자막 실패에는 "다시 해도 같은 것"(자막이
    8자, 영상이 세 시간)이 섞여 있어 골라내야 하고, 고르는 일은 목록이
    있는 화면에서 해야 합니다. */
function FailedPanel({
  stuck,
  canFix,
  onFixed,
}: {
  stuck: { key: string; label: string; count: number }[];
  canFix: boolean;
  onFixed: (msg: string) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);

  const kindOf = (key: string): "review" | "transcript" =>
    key === "failedReview" ? "review" : "transcript";

  const retry = async (key: string, label: string) => {
    setBusy(key);
    try {
      const { restored } = await api.retryFailed({ kind: kindOf(key), onlyRetryable: true });
      onFixed(
        restored > 0
          ? `${label} 중 ${restored}편을 줄에 다시 세웠습니다. 워커가 다음 차례부터 집어갑니다.`
          : `${label} 중 다시 해 볼 만한 것이 없습니다 — 대기 목록에서 골라 보세요.`,
      );
    } catch (e) {
      onFixed(e instanceof Error ? e.message : "다시 돌리지 못했습니다.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className={s.failed}>
      <span className={s.failedHead}>손봐야 할 것</span>
      {stuck.map((x) => (
        <div key={x.key} className={s.failedRow}>
          <span className={s.failedLabel}>
            {x.label} <strong>{num(x.count)}건</strong>
          </span>
          {canFix && (
            <Button
              size="small"
              onClick={() => void retry(x.key, x.label)}
              disabled={busy !== null}
            >
              {busy === x.key ? "…" : "다시 돌리기"}
            </Button>
          )}
          {/* 고르는 일은 목록이 있는 화면에서. 여기서 60줄을 그리면 이
              패널이 다시 벽이 되고, 정작 "지금 무엇을 하나"가 밀립니다. */}
          <Link className={s.failedLink} to="/queue">
            골라내기 →
          </Link>
        </div>
      ))}
      <p className={s.failedNote}>
        “다시 돌리기” 는 <strong>다시 해 볼 만한 것만</strong> 줄에 세웁니다 — 자막이 8자거나
        영상이 너무 긴 것처럼 해 봐야 같은 것은 그대로 둡니다. 그런 것을 아주 빼려면
        대기 목록에서 고르세요.
      </p>
    </div>
  );
}

/** 이 트랙을 지금 시작할 수 있는가, 없다면 왜 없는가.
 *
 *  처음에는 "막힌 것은 눌러서 넘기지 않는다"로 한 줄로 잠갔습니다. 차단된
 *  문을 두드리면 차단만 길어지니까요. **그건 같은 IP 일 때 맞는 말이었습니다.**
 *  사람이 VPN 을 바꾸면 냉각이 지키려던 조건 자체가 사라지는데, 화면은
 *  04:09 까지 기다리라고만 했습니다 — 우리가 모르는 사실을 이유로 사람을
 *  붙잡아 둔 셈입니다.
 *
 *  이제 **서버가 갈라 줍니다**(`hold.forcible`). 사람이 조건을 바꿀 수
 *  있는 멈춤(IP 차단·회사 세션)은 열고, 바꿀 수 없는 것(유튜브 하루
 *  할당량)은 잠급니다. */
function startable(t: Track): string | null {
  if (t.status === "running" || t.working) return "지금 도는 중입니다";
  if (t.hold?.tone === "stop" && !t.hold.forcible) {
    return t.hold.until
      ? `${t.hold.title} — ${clock(t.hold.until)} 이후에 눌러 주세요`
      : t.hold.title;
  }
  return null;
}

function TrackRow({
  t,
  reviewers,
  canStart,
  starting,
  onStart,
}: {
  t: Track;
  reviewers?: Reviewer[];
  canStart: boolean;
  starting: string | null;
  onStart: StartFn;
}) {
  const blocked = startable(t);
  const live = t.status === "running" || t.working !== null;
  // 붙들려 있는 것은 도는 것과 다릅니다. 오래 쥐고 있으면 색을 바꿔
  // 두어야, 워커 로그의 워치독 경고를 화면에서도 만날 수 있습니다.
  const stuck =
    t.working !== null &&
    Date.now() - new Date(t.working.since).getTime() > STUCK_MIN * 60_000;

  return (
    <li className={s.track}>
      <span className={live && !stuck ? s.dotLive : stuck ? s.dotStuck : s.dotIdle} aria-hidden="true" />
      <span className={s.trackName}>{t.label}</span>
      <span className={s.trackWait}>대기 {num(t.waiting)}</span>

      <div className={s.trackBody}>
        <div className={s.trackHead}>
          {t.working ? (
            <>
              <span className={s.trackTitle}>{t.working.title}</span>
              <em className={stuck ? s.trackHeld : s.trackSince}>
                · {elapsed(t.working.since)}
                {stuck && " — 오래 붙들려 있습니다"}
              </em>
            </>
          ) : t.status === "running" ? (
            <span className={s.trackTitle}>{t.runLabel ?? "도는 중"}</span>
          ) : t.hold ? (
            <span className={s.holdTitle} data-tone={t.hold.tone}>
              {t.hold.tone === "stop" ? "멈춤" : t.hold.tone === "warn" ? "일부 멈춤" : "우회 중"}
              {" · "}
              {t.hold.title}
            </span>
          ) : (
            <span className={s.trackTitle}>
              {t.waiting > 0 ? "차례를 기다리는 중" : "대기 없음 — 새 영상이 들어오면 시작합니다"}
            </span>
          )}
        </div>

        {/* 왜 그런지. 한 문장이면 손댈지 기다릴지가 정해집니다.
            **도는 중에도 나옵니다** — 한쪽 회사가 상한에 닿은 채로 다른
            쪽이 도는 상황이 흔한데, 첫 줄을 지금 하는 일에 내주고 나면
            그 사실을 적을 자리가 여기밖에 없습니다. 그때는 제목을 문장
            앞에 붙여, 무슨 이야기인지 모른 채로 읽지 않게 합니다. */}
        {t.hold && (
          <p className={s.holdDetail}>
            {(t.working || t.status === "running") && (
              <span className={s.holdInline} data-tone={t.hold.tone}>
                {t.hold.title} —{" "}
              </span>
            )}
            {t.hold.detail}
          </p>
        )}
        {/* **사람이 할 일이 있을 때만** 눈에 걸리게 합니다. 없는 줄까지
            같은 색으로 칠하면 전부 불안하게 읽혀서, 정작 손대야 할 때
            구분이 안 됩니다. */}
        {t.hold?.fix && <p className={s.holdFix}>→ {t.hold.fix}</p>}

        <Schedule t={t} />
        {reviewers && reviewers.length > 0 && <Reviewers list={reviewers} />}
      </div>

      {canStart && (
        <span className={s.trackAct}>
          <Button
            size="small"
            onClick={() => onStart(t.key as RunnableJob, t.label)}
            disabled={blocked !== null || starting !== null}
            title={
              blocked ??
              (t.hold?.forcible
                ? `${t.hold.title} — 회선을 바꿨다면 지금 시작하세요. 눌러 두면 냉각을 풀고 바로 돕니다.`
                : `${t.label} 을(를) 다음 차례를 기다리지 않고 지금 시작합니다`)
            }
          >
            {starting === t.key ? "…" : "시작"}
          </Button>
        </span>
      )}
    </li>
  );
}

/** 시각 줄 — **시작 · 다음 · 확인 주기.**

    "다음 차례 04:08" 만 적으면 지금이 몇 시인지 머릿속으로 빼야 합니다.
    남은 시간을 같이 적으면 그 계산이 없어집니다. 반대로 남은 시간만
    적으면 언제 다시 와 볼지를 정할 수가 없습니다 — 둘 다 필요합니다. */
function Schedule({ t }: { t: Track }) {
  const startedAt = t.working?.since ?? t.startedAt;
  const bits: string[] = [];
  if (startedAt) bits.push(`시작 ${clock(startedAt)} (${elapsed(startedAt)})`);
  if (t.nextAt) bits.push(`다음 ${clock(t.nextAt)} (${left(t.nextAt)})`);
  bits.push(`${every(t.everySec)} 확인`);
  // 마지막으로 무언가 옮긴 때. 시작도 다음도 없을 때만 — 셋을 다 적으면
  // 줄이 길어져서 정작 다음 차례가 눈에 안 들어옵니다.
  if (!startedAt && !t.nextAt && t.lastAt) bits.push(`마지막 ${ago(t.lastAt)}`);
  return <p className={s.trackWhen}>{bits.join(" · ")}</p>;
}

/** 요약은 **두 회사가 나눠 합니다.** 한 줄로 뭉뚱그리면 "안티그래비티가
    안 도는데 왜 그런가"에 답할 자리가 없습니다 — 실제로 그 답이 워커
    로그 안에만 있었습니다. */
function Reviewers({ list }: { list: Reviewer[] }) {
  return (
    <p className={s.reviewers}>
      {list.map((r) => (
        <span key={r.provider} className={s.reviewer}>
          {/* **쥐고 있을 때만 깜빡입니다.** 막히지 않았다는 것과 일하는
              중이라는 것은 다릅니다 — 대기 0 인 순간에 둘 다 도는 중으로
              보이던 것이 그 차이를 뭉갠 결과였습니다. */}
          <span className={r.working ? s.dotLive : s.dotIdle} aria-hidden="true" />
          {r.label}
          <em>
            {r.capped
              ? " 상한 도달"
              : r.restingUntil
                ? ` 쉬는 중 · ${clock(r.restingUntil)}`
                : r.working
                  ? ` 도는 중 · ${elapsed(r.working.since)}`
                  : " 대기 중"}
          </em>
        </span>
      ))}
    </p>
  );
}

/** 블로그의 "지금". **다른 트랙과 생김새를 맞춥니다** — 같은 줄에 서는데
    혼자 다르게 생기면 읽는 사람이 두 번 봅니다.

    점은 늘 쉬는 중입니다. 발행은 한 편을 몇 초에 올리고 끝나서, 화면을
    볼 때 걸려 있을 일이 사실상 없습니다 — 도는 척 깜빡이게 두면 없는
    일을 있다고 하는 셈입니다. */
function BlogRow({
  b,
  canStart,
  starting,
  onStart,
}: {
  b: BlogStatus;
  canStart: boolean;
  starting: string | null;
  onStart: StartFn;
}) {
  const last = b.recent[0];
  // 하루 상한에 닿으면 "쉬는 중"의 뜻이 달라집니다 — 간격이 돌아온 것이
  // 아니라 오늘은 끝난 것입니다. 이걸 안 적었을 때, 왜 안 올라가는지의
  // 답이 워커 로그 안에만 있었습니다.
  const full = b.dailyCap > 0 && b.postedToday >= b.dailyCap;
  return (
    <li className={s.track}>
      <span className={s.dotIdle} aria-hidden="true" />
      <span className={s.trackName}>블로그</span>
      <span className={s.trackWait}>대기 {num(b.waiting)}</span>
      <span className={s.trackWhat}>
        {/* 세션이 먼저입니다. 죽어 있으면 다음 차례가 언제든 아무것도
            안 나가므로, "쉬는 중" 이라고 적으면 거짓말이 됩니다. */}
        <span className={b.sessionBadSince ? s.trackStuck : s.trackTitle}>
          {b.sessionBadSince
            ? `로그인 필요 · ${ago(b.sessionBadSince)}부터 — 터미널에서 tistory login`
            : full
              ? "오늘 몫 다 씀 · 내일 이어감"
              : b.nextAt
                ? `쉬는 중 · 다음 차례 ${clock(b.nextAt)}`
                : "쉬는 중"}
        </span>
        {b.dailyCap > 0 && (
          <em className={s.trackSince}>
            · 오늘 {b.postedToday}/{b.dailyCap}
          </em>
        )}
        {!full && last && <em className={s.trackSince}>· 마지막 {ago(last.at)}</em>}
      </span>
      {canStart && (
        <span className={s.trackAct}>
          <Button
            size="small"
            onClick={() => onStart("publish", "블로그")}
            disabled={starting !== null || full || b.sessionBadSince !== null || b.waiting === 0}
            title={
              b.sessionBadSince
                ? "로그인이 필요합니다 — 터미널에서 tistory login"
                : full
                  ? "오늘 몫을 다 썼습니다 — 저쪽이 정한 상한이라 눌러서 넘길 수 없습니다"
                  : b.waiting === 0
                    ? "올릴 글이 없습니다"
                    : "한 편을 지금 올리고, 다음 차례를 그 시각부터 다시 잡습니다"
            }
          >
            {starting === "publish" ? "…" : "시작"}
          </Button>
        </span>
      )}
    </li>
  );
}

/** 올라간 글 — **최근 몇 편만 묶어서.**

    예전에는 한 편에 실행 기록이 하나씩 남아 "지나간 실행"에 섞여 나왔습니다.
    5~20분에 한 편이 나가므로 반나절이면 목록이 통째로 블로그 줄이 되고,
    검색·자막·요약이 무엇을 했는지는 스크롤 아래로 밀려 안 보였습니다.
    게다가 그 줄들은 펼쳐도 "옮긴 영상이 없습니다" 뿐이었습니다 — 발행 잡은
    파이프라인 이벤트를 남기지 않으니까요. 자리만 먹고 읽을 것이 없던 것.

    전부를 여기 옮기지도 않습니다. 그러면 덮는 자리만 바뀝니다. 이 화면이
    답해야 하는 것은 "돌고 있나"이고, 발행 이력 전체는 블로그에 있습니다. */
function BlogPanel({ b }: { b: BlogStatus }) {
  return (
    <Panel title={`블로그 — 지금까지 ${num(b.posted)}편`}>
      {b.recent.length === 0 ? (
        <Empty>아직 올라간 글이 없습니다.</Empty>
      ) : (
        <ul className={s.blogList}>
          {b.recent.map((p) => (
            <li key={p.postId ?? p.at}>
              <span className={s.blogWhen}>{clock(p.at)}</span>
              <span className={s.blogTitle}>
                {p.url ? (
                  <a href={p.url} target="_blank" rel="noreferrer">
                    {p.title}
                  </a>
                ) : (
                  p.title
                )}
              </span>
              <span className={s.blogWhere}>
                {p.category}
                {p.postId && ` #${p.postId}`}
              </span>
            </li>
          ))}
        </ul>
      )}
      {/* 세 번 해 보고 접은 글. 저절로 풀리지 않으므로 0 이 아니면 말합니다. */}
      {b.failed > 0 && <p className={s.stuck}>세 번 해 보고 접은 글 {num(b.failed)}편</p>}
    </Panel>
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
