import type { Failure, Overview, Usage } from "../api";
import { Screen } from "../components/Screen";
import { Button, Chip, ErrorState, Loading, Meter, Panel } from "../components/ui";
import { num, tokens, when } from "../lib/format";
import s from "./Dashboard.module.css";

const SEQ = ["var(--seq-1)", "var(--seq-2)", "var(--seq-3)", "var(--seq-4)", "var(--seq-5)"];

const failTone = (kind: Failure["kind"]) =>
  kind === "review" ? ("fail" as const) : ("warn" as const);

export function Dashboard({
  overview,
  usage,
  loading,
  error,
  onRetry,
}: {
  overview: Overview | null;
  usage: Usage | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  if (error) {
    return (
      <Screen title="대시보드">
        <ErrorState message={error} onRetry={onRetry} />
      </Screen>
    );
  }
  if (loading || !overview || !usage) {
    return (
      <Screen title="대시보드">
        <Loading />
      </Screen>
    );
  }

  const f = overview.funnel;
  const steps = [
    { label: "검색 발견", value: f.discovered },
    { label: "룰 통과", value: f.rulePassed },
    { label: "자막 확보", value: f.transcribed },
    { label: "전문성 통과", value: f.published },
  ];
  const gaps = [
    `− ${f.discovered - f.rulePassed} 룰 필터 (길이·조회수·제목)`,
    `− ${f.rulePassed - f.transcribed} 자막 없음`,
    `AI 검토 ${f.reviewed}건 · 조기 종료 ${overview.earlyExitCount}건`,
  ];

  const used = usage.inputTokens + usage.outputTokens;
  const limit = usage.dailyLimitTokens;
  const publishRate = f.discovered > 0 ? (f.published / f.discovered) * 100 : 0;
  const perCall = f.reviewed > 0 ? Math.round(used / f.reviewed) : 0;
  const maxContrib = Math.max(1, ...overview.contributions.map((c) => c.published));
  const idleKeywords = 0; // 서버가 내려주기 전까지는 표시하지 않는다

  return (
    <Screen
      title="대시보드"
      subtitle={`마지막 실행 ${when(overview.lastRunAt)}`}
      actions={<Button>지금 실행</Button>}
    >
      <Panel bodyless>
        <div className={s.stats}>
          <div className={s.stat}>
            <div className={s.statKey}>오늘 새 덕질</div>
            <div className={s.statValue}>{overview.newToday}</div>
            <div className={s.statNote}>
              이번 주 <em>+{overview.weekAdded}</em>
            </div>
          </div>
          <div className={s.stat}>
            <div className={s.statKey}>전체 보관</div>
            <div className={s.statValue}>{num(overview.totalLectures)}</div>
            <div className={s.statNote}>평균 점수 {overview.avgScore}</div>
          </div>
          <div className={s.stat}>
            <div className={s.statKey}>처리 대기</div>
            <div className={s.statValue}>
              {overview.queued.transcript + overview.queued.review}
            </div>
            <div className={s.statNote}>
              자막 확보 {overview.queued.transcript} · 검토 {overview.queued.review}
            </div>
          </div>
          <div className={s.stat}>
            <div className={s.statKey}>유튜브 쿼터</div>
            <div className={s.statValue}>{num(usage.youtubeUnits)}</div>
            <div className={s.statNote}>일 상한 {num(usage.youtubeUnitLimit)}</div>
          </div>
        </div>
      </Panel>

      <div className={s.grid} style={{ marginTop: 16 }}>
        <Panel title="오늘의 파이프라인" aside={<span className="eyebrow">전체 키워드 합계</span>}>
          <div className={s.funnel}>
            {steps.map((step, i) => (
              <div key={step.label}>
                <div className={s.fnRow}>
                  <span className={s.fnLabel}>{step.label}</span>
                  <span className={s.fnBar}>
                    <i
                      style={{
                        width: `${f.discovered > 0 ? (step.value / f.discovered) * 100 : 0}%`,
                        background: SEQ[i === steps.length - 1 ? 4 : i],
                      }}
                    />
                  </span>
                  <span className={s.fnNum}>{step.value}</span>
                </div>
                {gaps[i] && (
                  <div className={s.fnGap}>
                    <span>{gaps[i]}</span>
                  </div>
                )}
              </div>
            ))}
          </div>

          <div className={s.chips}>
            <Chip>
              발견 대비 공개율 <span className="mono">{publishRate.toFixed(1)}%</span>
            </Chip>
            <Chip>
              건당 평균 <span className="mono">{tokens(perCall)}</span> 토큰
            </Chip>
            {overview.earlyExitCount > 0 && <Chip tone="pass">조기 종료 작동 중</Chip>}
          </div>

          <div className={s.contrib}>
            <div className="eyebrow">키워드별 기여 · 오늘</div>
            <div className={s.bars}>
              {overview.contributions.map((c) => (
                <div key={c.keywordId} className={s.bar}>
                  <span className={s.barName}>{c.term}</span>
                  <span className={s.barTrack}>
                    <i style={{ width: `${(c.published / maxContrib) * 100}%` }} />
                  </span>
                  <span className={s.barValue}>{c.published}</span>
                </div>
              ))}
              {idleKeywords > 0 && (
                <div className={`${s.bar} ${s.zero}`}>
                  <span className={s.barName}>나머지 {idleKeywords}개</span>
                  <span className={s.barTrack} />
                  <span className={s.barValue}>0</span>
                </div>
              )}
            </div>
            <p className={s.note}>
              공개로 이어지지 않는 키워드는 쿼터만 쓰고 있다는 신호입니다. 며칠 이어지면 주기를
              낮추거나 검색어를 좁히세요.
            </p>
          </div>
        </Panel>

        <div className={s.side}>
          <Panel title="일일 토큰">
            <div className={s.usageTop}>
              <span className={s.usageBig}>{tokens(used)}</span>
              {limit && <span className={s.usageCap}>/ {tokens(limit)} 토큰</span>}
            </div>
            {limit && (
              <>
                <Meter value={used} max={limit} height={8} />
                <div className={s.usageFoot}>
                  <span>{Math.round((used / limit) * 100)}% 소진</span>
                  <span>{when(usage.resetsAt)} 리셋</span>
                </div>
              </>
            )}
            <div className={s.split2}>
              <div>
                <div className={s.splitKey}>입력</div>
                <div className={s.splitValue}>{tokens(usage.inputTokens)}</div>
              </div>
              <div>
                <div className={s.splitKey}>출력</div>
                <div className={s.splitValue}>{tokens(usage.outputTokens)}</div>
              </div>
            </div>
            <p className={s.note}>
              검토 {f.reviewed}건 · 조기 종료 {overview.earlyExitCount}건이 입력을{" "}
              <span className="mono">{tokens(overview.earlyExitSavedInputTokens)}</span>{" "}
              아꼈습니다. 상한에 닿으면 신규 검토는 멈추고 진행 중인 건만 마칩니다.
            </p>
          </Panel>

          <Panel
            title="최근 실패"
            aside={
              overview.failures.length > 0 ? <Button size="small">모두 재시도</Button> : undefined
            }
            bodyless
          >
            {overview.failures.length === 0 ? (
              <p className={s.note} style={{ padding: 16, margin: 0 }}>
                실패한 항목이 없습니다.
              </p>
            ) : (
              <div className={s.fails}>
                {overview.failures.map((x, i) => (
                  <div key={i} className={s.fail}>
                    <Chip tone={failTone(x.kind)}>{x.label}</Chip>
                    <div className={s.failText}>
                      <b className={s.failTitle}>{x.title}</b>
                      <span className={s.failDetail}>{x.detail}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </div>
      </div>
    </Screen>
  );
}
