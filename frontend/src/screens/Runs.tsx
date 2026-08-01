import { useEffect } from "react";

import { api } from "../api";
import type { Run, RunStats } from "../api";
import { Screen } from "../components/Screen";
import { Chip, Empty, ErrorState, Loading, Panel } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { num, tokens } from "../lib/format";
import s from "./Runs.module.css";

const MAX_BAR_PX = 40;
const STAGES: { key: keyof RunStats; label: string; seq: string }[] = [
  { key: "discovered", label: "발견", seq: "var(--seq-1)" },
  { key: "rulePassed", label: "룰", seq: "var(--seq-2)" },
  { key: "transcribed", label: "자막", seq: "var(--seq-3)" },
  { key: "reviewed", label: "검토", seq: "var(--seq-4)" },
  { key: "published", label: "공개", seq: "var(--seq-5)" },
];

const statusChip: Record<Run["status"], { tone: "pass" | "warn" | "fail" | "neutral"; label: string }> =
  {
    succeeded: { tone: "pass", label: "완료" },
    partial: { tone: "warn", label: "일부 실패" },
    failed: { tone: "fail", label: "실패" },
    running: { tone: "neutral", label: "진행 중" },
    queued: { tone: "neutral", label: "대기 중" },
  };

function clock(iso: string): string {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** 도는 중일 때 새로 고치는 간격. 한 사이클이 몇 분씩 걸려서, 화면이
    멈춰 있으면 "눌렀는데 아무 일도 안 일어난다"로 보입니다. */
const POLL_ACTIVE_MS = 5_000;
/** 놀고 있을 때. 정기 실행이 언제 시작될지 몰라 아주 끊지는 않습니다. */
const POLL_IDLE_MS = 30_000;

export function Runs() {
  const runs = useAsync(() => api.listRuns(), []);

  // **한 번만 불러오면 안 됩니다.** "지금 실행"은 요청만 남기고 워커가
  // 다음 틱에 집어가는 구조라, 눌러 놓고 이 화면을 봐도 대기 중인 실행이
  // 나타나지 않았습니다. 진행 중인 것이 있으면 자주, 없으면 뜸하게 봅니다.
  const active = (runs.data ?? []).some(
    (r) => r.status === "queued" || r.status === "running",
  );
  const { reload } = runs;
  useEffect(() => {
    const id = window.setInterval(reload, active ? POLL_ACTIVE_MS : POLL_IDLE_MS);
    return () => window.clearInterval(id);
  }, [active, reload]);

  if (runs.error) {
    return (
      <Screen title="실행 로그">
        <ErrorState message={runs.error} onRetry={runs.reload} />
      </Screen>
    );
  }
  // **첫 로딩에만** 스피너를 보입니다. `loading` 을 그대로 보면 5초마다
  // 화면이 통째로 깜빡여서, 새로 고치지 않는 것보다 읽기 나쁩니다.
  if (!runs.data) {
    return (
      <Screen title="실행 로그">
        <Loading />
      </Screen>
    );
  }

  const rows = runs.data;
  const failCount = rows.filter((r) => r.status !== "succeeded").length;

  // 실행 간 비교가 목적이라 모든 실행이 같은 축척을 씁니다
  const scaleMax = Math.max(1, ...rows.map((r) => r.stats.discovered));

  return (
    <Screen
      title="실행 로그"
      subtitle={`최근 ${rows.length}회 · 실패 ${failCount}건`}
    >
      {rows.length === 0 ? (
        <Empty>아직 실행 이력이 없습니다.</Empty>
      ) : (
        <Panel bodyless>
          {rows.map((r) => {
            const st = statusChip[r.status];
            return (
              <div key={r.id} className={s.run}>
                <div>
                  <div className={s.when}>
                    {r.startedAt.slice(0, 10)} {clock(r.startedAt)}
                    {r.finishedAt && ` → ${clock(r.finishedAt)}`}
                  </div>
                  <div className={s.label}>{r.label}</div>
                  <Chip tone={st.tone}>{st.label}</Chip>
                  <div className={s.cost}>
                    {tokens(r.tokens)} 토큰 · {num(r.youtubeUnits)} 유닛
                  </div>
                </div>

                <div>
                  <div className={s.mini}>
                    {STAGES.map((stage) => {
                      const v = r.stats[stage.key];
                      const h = Math.max(2, Math.round((v / scaleMax) * MAX_BAR_PX));
                      return (
                        <div key={stage.key} className={s.col}>
                          <span className={s.count}>{v}</span>
                          <span
                            className={s.bar}
                            style={{
                              height: h,
                              background: v === 0 ? "var(--surface-sink)" : stage.seq,
                            }}
                          />
                        </div>
                      );
                    })}
                  </div>
                  <div className={s.labels}>
                    {STAGES.map((stage) => (
                      <span key={stage.key}>{stage.label}</span>
                    ))}
                  </div>
                  {r.error && <p className={s.error}>{r.error}</p>}
                </div>
              </div>
            );
          })}
        </Panel>
      )}
    </Screen>
  );
}
