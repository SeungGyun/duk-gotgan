import { useCallback, useMemo, useState } from "react";

import { api } from "../api";
import type { FailedGroup, FailedItem, QueueItem, QueueStage } from "../api";
import { Screen } from "../components/Screen";
import { Button, Empty, ErrorState, Loading, Panel } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { duration } from "../lib/format";
import { useMe } from "../me";
import s from "./Queue.module.css";

/** 앞으로 처리할 것을 미리 봅니다.
 *
 *  **처리 전에 빼는 것이 이 화면의 값어치입니다.** 한 편당 받아쓰기 2~7분에
 *  검토 6~8만 토큰이 듭니다. 제목만 봐도 아닌 것이 보이면, 일이 벌어지기
 *  전에 빼는 편이 요약을 만들어 놓고 제외하는 것보다 훨씬 쌉니다.
 *
 *  순서는 지어낸 것이 아닙니다 — 서버가 워커와 같은 함수로 뽑아 주므로,
 *  여기 보이는 차례가 실제 처리 차례입니다. */
export function Queue() {
  const q = useAsync(() => api.getQueue(), []);
  const me = useMe();
  const [busy, setBusy] = useState<string | null>(null);
  const [gone, setGone] = useState<Set<string>>(() => new Set());

  const act = useCallback(async (videoId: string, fn: () => Promise<void>) => {
    setBusy(videoId);
    try {
      await fn();
      // 목록을 통째로 다시 받지 않고 그 줄만 걷어냅니다 — 다시 받으면
      // 차례가 밀려 지금 보던 자리가 흔들립니다.
      setGone((prev) => new Set(prev).add(videoId));
    } finally {
      setBusy(null);
    }
  }, []);

  if (q.error) return <ErrorState message={q.error} onRetry={q.reload} />;
  if (!q.data) {
    return (
      <Screen title="대기 목록">
        <Loading />
      </Screen>
    );
  }

  const { stages, skipped, failed, asrRealtimeFactor } = q.data;
  const visible = (items: QueueItem[]) => items.filter((i) => !gone.has(i.videoId));

  return (
    <Screen
      title="대기 목록"
      subtitle="앞으로 처리할 것들 — 아닌 것은 미리 빼면 받아쓰기도 검토도 하지 않습니다"
    >
      {stages.map((st) => (
        <Stage
          key={st.key}
          stage={st}
          items={visible(st.items)}
          factor={asrRealtimeFactor}
          busy={busy}
          onSkip={(id) => void act(id, () => api.skipQueued(id))}
        />
      ))}

      {failed.some((g) => g.count > 0) && (
        <Failed groups={failed} canFix={me.isOwner} onDone={q.reload} />
      )}

      {skipped.length > 0 && (
        <Panel title={`미리 뺀 것 ${skipped.length}건`} bodyless>
          <ul className={s.list}>
            {visible(skipped).map((it) => (
              <Row
                key={it.videoId}
                item={it}
                busy={busy === it.videoId}
                actionLabel="되돌리기"
                onAction={() => void act(it.videoId, () => api.restoreQueued(it.videoId))}
              />
            ))}
          </ul>
        </Panel>
      )}
    </Screen>
  );
}

function Stage({
  stage,
  items,
  factor,
  busy,
  onSkip,
}: {
  stage: QueueStage;
  items: QueueItem[];
  factor: number;
  busy: string | null;
  onSkip: (videoId: string) => void;
}) {
  const hours = stage.totalSec / 3600;
  const eta = stage.etaSec ? stage.etaSec / 3600 : null;

  return (
    <Panel
      title={`${stage.label} ${stage.count}건`}
      aside={
        <span className={s.meta}>
          영상 {hours.toFixed(1)}시간
          {/* 어림값이라는 것과 그 근거를 같이 적습니다. 근거 없는 숫자는
              틀렸을 때 신뢰를 통째로 잃습니다. */}
          {eta !== null && ` · 받아쓰기 약 ${eta.toFixed(1)}시간 (${factor}배속 어림)`}
        </span>
      }
      bodyless
      className={s.stage}
    >
      {items.length === 0 ? (
        <Empty>비어 있습니다.</Empty>
      ) : (
        <>
          <ul className={s.list}>
            {items.map((it) => (
              <Row
                key={it.videoId}
                item={it}
                busy={busy === it.videoId}
                actionLabel="빼기"
                danger
                onAction={() => onSkip(it.videoId)}
              />
            ))}
          </ul>
          {stage.count > stage.items.length && (
            <p className={s.more}>
              앞 {stage.items.length}건만 보입니다 · 나머지 {stage.count - stage.items.length}건
            </p>
          )}
        </>
      )}
    </Panel>
  );
}

function Row({
  item,
  busy,
  actionLabel,
  danger = false,
  onAction,
}: {
  item: QueueItem;
  busy: boolean;
  actionLabel: string;
  danger?: boolean;
  onAction: () => void;
}) {
  return (
    <li className={s.row}>
      {/* 차례가 정해진 칸에서만 번호를 답니다. 발견 단계는 다음 수집 때
          키워드별 상한에 따라 올라가서 차례를 약속할 수 없습니다. */}
      <span className={s.order}>{item.order ?? "·"}</span>
      <span className={s.len}>{duration(item.durationSec)}</span>
      <div className={s.info}>
        <a
          className={s.title}
          href={`https://youtu.be/${item.videoId}`}
          target="_blank"
          rel="noreferrer"
          title={item.title}
        >
          {item.title}
        </a>
        <span className={s.sub}>
          {item.channelTitle}
          {item.keywords.length > 0 && ` · ${item.keywords.join(", ")}`}
        </span>
      </div>
      <button
        type="button"
        className={danger ? s.skip : s.restore}
        onClick={onAction}
        disabled={busy}
      >
        {busy ? "…" : actionLabel}
      </button>
    </li>
  );
}

/** 되풀이 실패는 몇 회부터 "되풀이"인가.
 *
 *  자막·요약 모두 다섯 번까지 스스로 다시 해 보고 접습니다
 *  (`MAX_TRANSCRIPT_RETRY` · `MAX_REVIEW_RETRY`). 그보다 많이 찍혔다는
 *  것은 **사람이 한 번 되살린 뒤에도 또 죽었다**는 뜻이라, 그 선에서
 *  "이건 아니다"를 물어볼 만합니다. */
const REPEAT_AT = 6;

type Filter = "all" | "retryable" | "repeat";

const FILTERS: { key: Filter; label: string; hint: string }[] = [
  { key: "all", label: "전체", hint: "실패한 것 전부" },
  {
    key: "retryable",
    label: "다시 해 볼 만한 것",
    hint: "다시 해도 같은 사유(자막이 8자·영상이 너무 김·비공개)를 뺀 나머지",
  },
  {
    key: "repeat",
    label: `${REPEAT_AT}회 이상`,
    hint: "스스로 다섯 번 해 보고도 안 된 뒤 또 죽은 것 — 완전히 뺄 후보",
  },
];

/** 손봐야 할 실패 — **고르는 화면.**
 *
 *  실행 로그에도 "다시 돌리기" 가 있지만 저건 무리 전체를 한 번에 미는
 *  버튼입니다. 여기는 반대로 하나하나 보고 고르는 자리입니다. 둘 다
 *  필요합니다 — 요약 실패는 대개 세션·모델 쪽이라 통째로 밀면 되고,
 *  자막 실패에는 다시 해도 같은 것이 섞여 있어 골라내야 합니다.
 *
 *  **보이는 것만 처리합니다.** 필터를 걸고 "보이는 것 모두"를 눌러도
 *  화면에 그려진 줄만 담깁니다. 서버에 필터를 넘기면 사용자가 본 적 없는
 *  줄까지 지워질 수 있고, 그 차이가 하필 완전 제외에서 나타납니다. */
function Failed({
  groups,
  canFix,
  onDone,
}: {
  groups: FailedGroup[];
  canFix: boolean;
  onDone: () => void;
}) {
  const live = groups.filter((g) => g.count > 0);
  const [kind, setKind] = useState(live[0]?.kind ?? "transcript");
  const [filter, setFilter] = useState<Filter>("all");
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const group = live.find((g) => g.kind === kind) ?? live[0];

  const shown = useMemo(() => {
    const items = group?.items ?? [];
    if (filter === "retryable") return items.filter((i) => i.retryable);
    if (filter === "repeat") return items.filter((i) => i.attempts >= REPEAT_AT);
    return items;
  }, [group, filter]);

  const swap = (next: Filter | typeof kind, isKind = false) => {
    // 고른 것을 들고 칸을 옮기면, 안 보이는 줄이 선택된 채로 남습니다 —
    // 그대로 "완전 제외"를 누르면 본 적 없는 것이 지워집니다.
    setPicked(new Set());
    setAsking(false);
    if (isKind) setKind(next as typeof kind);
    else setFilter(next as Filter);
  };

  const toggle = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const allShown = shown.length > 0 && shown.every((i) => picked.has(i.videoId));
  const pickAll = () =>
    setPicked(allShown ? new Set() : new Set(shown.map((i) => i.videoId)));

  const run = async (what: "retry" | "refetch" | "exclude") => {
    setBusy(true);
    setAsking(false);
    const videoIds = [...picked];
    try {
      if (what === "retry" || what === "refetch") {
        const refetch = what === "refetch";
        const { restored } = await api.retryFailed({ videoIds, refetch });
        setNote(
          refetch
            ? `${restored}편을 자막부터 다시 받습니다. 한 편에 2~7분 걸립니다.`
            : `${restored}편을 줄에 다시 세웠습니다. 워커가 다음 차례부터 집어갑니다.`,
        );
      } else {
        const { excluded } = await api.excludeFailed({ videoIds });
        setNote(`${excluded}편을 완전히 뺐습니다. 다시 수집하지 않습니다.`);
      }
      setPicked(new Set());
      onDone();
    } catch (e) {
      setNote(e instanceof Error ? e.message : "처리하지 못했습니다.");
    } finally {
      setBusy(false);
      window.setTimeout(() => setNote(null), 6000);
    }
  };

  if (!group) return null;

  return (
    <Panel
      title="실패 — 손봐야 할 것"
      aside={
        <span className={s.meta}>
          저절로 풀리지 않습니다 · 다시 세우거나 아주 빼야 줄에서 없어집니다
        </span>
      }
      bodyless
      className={s.stage}
    >
      <div className={s.tabs}>
        {live.map((g) => (
          <button
            key={g.kind}
            type="button"
            className={g.kind === group.kind ? s.tabOn : s.tab}
            onClick={() => swap(g.kind, true)}
          >
            {g.label} <span className={s.tabCount}>{g.count}</span>
          </button>
        ))}
      </div>

      <div className={s.filters}>
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            className={f.key === filter ? s.chipOn : s.chip}
            onClick={() => swap(f.key)}
            title={f.hint}
          >
            {f.label}
          </button>
        ))}
        <span className={s.filterCount}>
          {shown.length}건 보임
          {group.count > group.items.length && ` · 전체 ${group.count}건 중 앞 ${group.items.length}건`}
        </span>
      </div>

      {canFix && (
        <div className={s.bulk}>
          <label className={s.pickAll}>
            <input
              type="checkbox"
              checked={allShown}
              onChange={pickAll}
              disabled={shown.length === 0}
            />
            보이는 것 모두
          </label>
          <span className={s.pickedCount}>
            {picked.size > 0 ? `${picked.size}건 고름` : "고른 것 없음"}
          </span>
          {asking ? (
            <>
              <span className={s.ask}>
                {picked.size}편을 완전히 뺍니다 — 다시 수집하지 않습니다.
              </span>
              <Button size="small" onClick={() => void run("exclude")} disabled={busy}>
                뺍니다
              </Button>
              <Button size="small" onClick={() => setAsking(false)} disabled={busy}>
                그만
              </Button>
            </>
          ) : (
            <>
              <Button
                size="small"
                variant="primary"
                onClick={() => void run("retry")}
                disabled={busy || picked.size === 0}
                title="요약을 다시 부릅니다. 자막은 있는 것을 그대로 씁니다."
              >
                다시 돌리기
              </Button>
              {/* **자막이 못 쓸 것일 때의 길.** 요약만 다시 불러 봐야
                  같은 자막을 읽고 같은 결론이 나옵니다 — 받아쓰기가 언어를
                  잘못 잡아 한국어 강의를 일본어로 옮겨 놓은 것들이 그랬습니다. */}
              <Button
                size="small"
                onClick={() => void run("refetch")}
                disabled={busy || picked.size === 0}
                title="지금 자막을 버리고 처음부터 다시 받습니다 (한 편에 2~7분)"
              >
                자막부터 다시
              </Button>
              {/* 완전 제외는 되돌릴 수 없어서 한 번 묻습니다. 별도 창을
                  띄우지 않고 그 자리에서 물어야 무엇을 빼는지 눈에 남습니다. */}
              <Button
                size="small"
                onClick={() => setAsking(true)}
                disabled={busy || picked.size === 0}
              >
                완전 제외
              </Button>
            </>
          )}
        </div>
      )}

      {note && <p className={s.note}>{note}</p>}

      {shown.length === 0 ? (
        <Empty>이 조건에 걸리는 것이 없습니다.</Empty>
      ) : (
        <ul className={s.list}>
          {shown.map((it) => (
            <FailedRow
              key={it.videoId}
              item={it}
              picked={picked.has(it.videoId)}
              canFix={canFix}
              onToggle={() => toggle(it.videoId)}
            />
          ))}
        </ul>
      )}
    </Panel>
  );
}

function FailedRow({
  item,
  picked,
  canFix,
  onToggle,
}: {
  item: FailedItem;
  picked: boolean;
  canFix: boolean;
  onToggle: () => void;
}) {
  return (
    <li className={picked ? s.rowPicked : s.row}>
      {canFix ? (
        <input
          type="checkbox"
          className={s.check}
          checked={picked}
          onChange={onToggle}
          aria-label={`${item.title} 고르기`}
        />
      ) : (
        <span className={s.order}>·</span>
      )}
      {/* 실패 횟수가 먼저입니다 — 되풀이하는 것을 찾으러 온 화면이라,
          제목보다 이 숫자로 훑게 됩니다. */}
      <span className={item.attempts >= REPEAT_AT ? s.triesHot : s.tries}>
        {item.attempts}회
      </span>
      <div className={s.info}>
        <a
          className={s.title}
          href={`https://youtu.be/${item.videoId}`}
          target="_blank"
          rel="noreferrer"
          title={item.title}
        >
          {item.title}
        </a>
        {/* 사유는 접지 않습니다. 여기까지 온 사람은 그걸 읽으러 왔습니다. */}
        <span className={item.retryable ? s.why : s.whyDead}>
          {!item.retryable && <span className={s.dead}>다시 해도 같음</span>}
          {item.reason}
        </span>
      </div>
      <span className={s.len}>{duration(item.durationSec)}</span>
    </li>
  );
}
