import { useState } from "react";

import { api } from "../api";
import type { Failure, Overview, ProviderUsage, Usage } from "../api";
import { Screen } from "../components/Screen";
import { Button, Chip, ErrorState, Loading, Meter, Panel } from "../components/ui";
import { num, tokens, when } from "../lib/format";
import { useMe } from "../me";
import s from "./Dashboard.module.css";

/** 회사 이름을 사람이 읽는 말로. 모르는 이름은 그대로 보여 줍니다 —
 *  새 회사를 붙였는데 화면에 빈칸이 뜨면 그게 더 헷갈립니다. */
const PROVIDER_LABEL: Record<string, string> = {
  claude: "클로드",
  antigravity: "안티그래비티",
};

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
  // "지금 실행"은 요청만 남깁니다. 워커가 다음 틱에 집어가므로 버튼을 누른
  // 뒤 몇 초 안에 실행 로그에 나타납니다 — 여기서 결과를 기다리지 않습니다.
  const [requesting, setRequesting] = useState(false);
  const [runNote, setRunNote] = useState<string | null>(null);
  const me = useMe();

  const requestRun = async () => {
    setRequesting(true);
    setRunNote(null);
    try {
      await api.requestRun();
      setRunNote("실행을 요청했습니다. 곧 시작됩니다 — 실행 로그에서 진행을 볼 수 있습니다.");
      onRetry();
    } catch (e) {
      setRunNote(e instanceof Error ? e.message : "요청에 실패했습니다.");
    } finally {
      setRequesting(false);
      window.setTimeout(() => setRunNote(null), 6000);
    }
  };

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

  // **"오늘 한 일"입니다** — 한 영상이 네 칸을 다 지나간 수가 아닙니다.
  //
  // 검색·자막·요약을 따로 돌리고 대기가 몇 시간씩 쌓이므로, 오늘 발견한
  // 것은 대개 내일 요약됩니다. 칸끼리 빼서 "몇 개가 떨어졌다"를 읽으면
  // 안 됩니다 — 각 칸은 서로 다른 영상들의 오늘치 처리량입니다.
  const f = overview.funnel;
  const steps = [
    { label: "검색 발견", value: f.discovered },
    { label: "룰 통과", value: f.rulePassed },
    { label: "자막 확보", value: f.transcribed },
    { label: "AI 요약", value: f.reviewed },
    { label: "공개", value: f.published },
  ];

  // 칸끼리 크기가 뒤집힐 수 있어(자막 68 > 룰 통과 62) 발견 수가 아니라
  // 최대값을 기준으로 그립니다. 안 그러면 막대가 100%를 넘습니다.
  const maxStep = Math.max(1, ...steps.map((x) => x.value));

  const used = usage.inputTokens + usage.outputTokens;
  const limit = usage.limitTokens;
  // 상한을 바꾸는 것은 **주인만** 입니다. 자물쇠가 셋입니다 — 이 화면 자체가
  // 주인 전용 라우트이고(App.tsx), 여기서 편집 UI 를 가리고, 서버가 403 으로
  // 막습니다. 지금은 식구가 이 화면에 못 들어오지만, 나중에 이 패널을 다른
  // 곳에 옮겨도 편집이 새어 나가지 않게 여기서도 봅니다.
  const isOwner = me.isOwner;
  const perCall = f.reviewed > 0 ? Math.round(used / f.reviewed) : 0;
  const maxContrib = Math.max(1, ...overview.contributions.map((c) => c.published));
  const idleKeywords = 0; // 서버가 내려주기 전까지는 표시하지 않는다

  return (
    <Screen
      title="대시보드"
      subtitle={`마지막 실행 ${when(overview.lastRunAt)}`}
      actions={
        <Button variant="primary" onClick={() => void requestRun()} disabled={requesting}>
          {requesting ? "요청 중…" : "지금 실행"}
        </Button>
      }
    >
      {runNote && (
        <p className={s.runNote} role="status">
          {runNote}
        </p>
      )}

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
              자막 {overview.queued.transcript} · 요약 {overview.queued.review}
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
        <Panel
          title="오늘 한 일"
          aside={<span className="eyebrow">단계별 오늘 처리량 · 같은 영상이 아닙니다</span>}
        >
          <div className={s.funnel}>
            {steps.map((step, i) => (
              <div key={step.label}>
                <div className={s.fnRow}>
                  <span className={s.fnLabel}>{step.label}</span>
                  <span className={s.fnBar}>
                    <i
                      style={{
                        width: `${maxStep > 0 ? (step.value / maxStep) * 100 : 0}%`,
                        background: SEQ[i === steps.length - 1 ? 4 : i],
                      }}
                    />
                  </span>
                  <span className={s.fnNum}>{step.value}</span>
                </div>
              </div>
            ))}
          </div>

          <div className={s.chips}>
            {/* 칸끼리 같은 영상이 아니므로 "통과율"이 아닙니다 — 오늘
                각 단계에서 처리한 양입니다. */}
            <Chip>
              오늘 공개 <span className="mono">{f.published}</span>편
            </Chip>
            <Chip>
              건당 평균 <span className="mono">{tokens(perCall)}</span> 토큰
            </Chip>
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
              검색·자막·요약이 <strong>따로 돕니다.</strong> 오늘 발견한 것이 오늘 요약되는
              것은 아니라, 칸끼리 빼서 "몇 개가 떨어졌다"로 읽으면 안 됩니다. 지금 어디까지
              왔는지는 실행 로그의 <strong>지금</strong> 패널에서 봅니다.
            </p>
          </div>
        </Panel>

        <div className={s.side}>
          <Panel title={`${usage.windowHours}시간 토큰`}>
            <div className={s.usageTop}>
              <span className={s.usageBig}>{tokens(used)}</span>
              <span className={s.limitBtn} style={{ cursor: "default" }}>
                / {limit ? tokens(limit) : "무제한"} 토큰
              </span>
            </div>
            {limit && (
              <>
                <Meter value={used} max={limit} height={8} />
                <div className={s.usageFoot}>
                  <span>{Math.round((used / limit) * 100)}% 소진</span>
                  <span>{when(usage.windowResetsAt)} 초기화</span>
                </div>
              </>
            )}

            {/* **회사별로 나눠 봅니다.** 상한이 각 구독에 따로 걸리는데
                합친 숫자만 보면 어느 쪽이 닿아서 멈췄는지 알 수 없습니다 —
                실제로 한쪽 쿼터가 떨어졌는데 "많이 썼네"로만 보였습니다. */}
            <div className={s.providers}>
              {usage.providers.map((p) => (
                <ProviderRow key={p.provider} p={p} canEdit={isOwner} onSaved={onRetry} />
              ))}
            </div>

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
              {/* 창과 하루를 나란히 둡니다 — 상한은 창 기준이지만 "오늘
                  얼마나 했나"도 궁금하니까요. */}
              오늘 하루로는 <span className="mono">{tokens(usage.todayTokens)}</span>,
              요약 <span className="mono">{f.reviewed}</span>건. 상한에 닿으면 새 요약은
              멈추고 진행 중인 건만 마칩니다.
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


/** 회사 한 곳의 사용량과 상한. 상한은 그 자리에서 고칩니다.
 *
 *  **회사마다 따로 겁니다.** 상한이 각 구독에 따로 걸리는데 한 값으로
 *  묶으면, 한쪽이 많이 쓴 것 때문에 아직 여유가 있는 쪽까지 멈춥니다 —
 *  토큰이 모자라서 회사를 늘렸는데 정반대가 됩니다.
 *
 *  **설정 파일이 아니라 화면에 둡니다.** .env 를 고치고 프로세스를
 *  재시작해야 한다면, 쓰다가 "조금만 올려 보자"를 할 수 없습니다.
 *  값은 DB 에 남아서 워커와 화면이 같은 것을 봅니다.
 *
 *  단위는 **백만(M)** 으로 받습니다. 3,000,000 을 손으로 치게 하면 0 을
 *  하나 더 넣거나 빠뜨리기 쉽고, 그 실수가 곧바로 사용량 폭주나 정지로
 *  이어집니다.
 *
 *  **바꾸는 것은 주인만입니다.** 눌러도 안 되는 버튼을 보여 주면 "왜 나만
 *  안 되지"가 되므로, 주인이 아니면 편집 손잡이 없이 숫자만 보여 줍니다. */
function ProviderRow({
  p,
  canEdit,
  onSaved,
}: {
  p: ProviderUsage;
  canEdit: boolean;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const used = p.inputTokens + p.outputTokens;
  const name = PROVIDER_LABEL[p.provider] ?? p.provider;

  const start = () => {
    setDraft(p.limitTokens ? String(p.limitTokens / 1_000_000) : "0");
    setErr(null);
    setEditing(true);
  };

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      onSaved();
      setEditing(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "바꾸지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  const save = () => {
    const m = Number(draft);
    if (!Number.isFinite(m) || m < 0) {
      setErr("0 이상의 숫자를 넣어 주세요.");
      return;
    }
    void run(() => api.setTokenLimit(Math.round(m * 1_000_000), p.provider));
  };

  return (
    <div className={s.provider}>
      <div className={s.providerTop}>
        <span className={s.providerName}>{name}</span>
        {editing ? (
          <span className={s.limitEdit}>
            /
            <input
              type="number"
              step="0.5"
              min="0"
              value={draft}
              autoFocus
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") save();
                if (e.key === "Escape") setEditing(false);
              }}
            />
            <span className={s.limitUnit}>M</span>
            <Button onClick={save} disabled={busy}>
              {busy ? "…" : "저장"}
            </Button>
            {/* 자기 값이 있을 때만 "공용으로" 가 의미가 있습니다. */}
            {p.hasOwnLimit && (
              <button
                type="button"
                className={s.limitCancel}
                disabled={busy}
                onClick={() => void run(() => api.inheritTokenLimit(p.provider))}
                title="이 회사만 걸어 둔 값을 지우고 공용 값을 씁니다"
              >
                공용으로
              </button>
            )}
            <button type="button" className={s.limitCancel} onClick={() => setEditing(false)}>
              취소
            </button>
          </span>
        ) : (
          <span className={s.providerNums}>
            <span className="mono">{tokens(used)}</span>
            {canEdit ? (
              <button
                type="button"
                className={s.limitBtn}
                onClick={start}
                title="눌러서 이 회사의 상한만 바꾸기"
              >
                / {p.limitTokens ? tokens(p.limitTokens) : "무제한"}
                {!p.hasOwnLimit && <span className={s.inherited}> 공용</span>}
              </button>
            ) : (
              <span className={s.providerCap}>
                / {p.limitTokens ? tokens(p.limitTokens) : "무제한"}
              </span>
            )}
          </span>
        )}
      </div>
      {p.limitTokens && <Meter value={used} max={p.limitTokens} height={5} />}
      <div className={s.providerFoot}>
        <span>
          {p.limitTokens ? `${Math.round((used / p.limitTokens) * 100)}% 소진` : "상한 없음"}
        </span>
        <span>요약 {p.calls}건</span>
      </div>
      {/* **쉬는 중이면 말해 줍니다.** 막혔을 때 1분마다 다시 두드리던 것을
          멈췄더니 로그도 조용해졌습니다 — 그만큼 여기서 보여야, 왜 아무것도
          안 하는지를 로그를 뒤져 알아내지 않아도 됩니다. */}
      {p.restingUntil && (
        <p className={s.resting}>쉬는 중 — {when(p.restingUntil)} 에 다시 봅니다</p>
      )}
      {err && (
        <p className={s.providerErr} role="alert">
          {err}
        </p>
      )}
    </div>
  );
}
