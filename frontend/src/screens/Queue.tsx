import { useCallback, useState } from "react";

import { api } from "../api";
import type { QueueItem, QueueStage } from "../api";
import { Screen } from "../components/Screen";
import { Empty, ErrorState, Loading, Panel } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { duration } from "../lib/format";
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

  const { stages, skipped, asrRealtimeFactor } = q.data;
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
