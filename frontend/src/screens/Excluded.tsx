import { useCallback, useState } from "react";

import { api } from "../api";
import { Screen } from "../components/Screen";
import { Button, Empty, ErrorState, Loading, Panel } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { useMe } from "../me";
import { duration } from "../lib/format";
import s from "./Excluded.module.css";

/** 뺀 것을 모아 두는 곳.
 *
 *  **되돌릴 수 있어야 제외를 편하게 누릅니다.** 뺄 때마다 "정말요?"를
 *  물으면 성가시고, 물어도 오눌림은 막지 못합니다. 대신 전부 여기 남겨
 *  두고 언제든 되돌리게 합니다.
 *
 *  완전삭제만 확인을 받습니다 — 이건 되돌릴 수 없고, 같은 영상을 다시
 *  수집하지도 않습니다. */
export function Excluded() {
  const me = useMe();
  const list = useAsync(() => api.listLectures({ excluded: true }), []);
  const [busy, setBusy] = useState<string | null>(null);
  const [asking, setAsking] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const act = useCallback(
    async (videoId: string, fn: () => Promise<void>) => {
      setBusy(videoId);
      setError(null);
      try {
        await fn();
        list.reload();
      } catch (e) {
        setError(e instanceof Error ? e.message : "처리하지 못했습니다.");
      } finally {
        setBusy(null);
        setAsking(null);
      }
    },
    [list],
  );

  if (list.error) return <ErrorState message={list.error} onRetry={list.reload} />;
  if (!list.data) {
    return (
      <Screen title="제외함">
        <Loading />
      </Screen>
    );
  }

  const rows = list.data;

  return (
    <Screen
      title="제외함"
      subtitle={`${rows.length}편 · 되돌리거나 완전히 지울 수 있습니다`}
    >
      {error && <p className={s.error}>{error}</p>}
      <Panel>
        {rows.length === 0 ? (
          <Empty>뺀 덕질이 없습니다.</Empty>
        ) : (
          <ul className={s.list}>
            {rows.map((l) => (
              <li key={l.videoId} className={s.row}>
                <div className={s.info}>
                  <span className={s.title}>{l.title}</span>
                  <span className={s.meta}>
                    {l.channelTitle} · {duration(l.durationSec)} · 전문성 {l.expertScore}
                  </span>
                  {l.oneLiner && <span className={s.liner}>{l.oneLiner}</span>}
                </div>
                <div className={s.actions}>
                  {asking === l.videoId ? (
                    <Confirm
                      busy={busy === l.videoId}
                      onCancel={() => setAsking(null)}
                      onConfirm={() => void act(l.videoId, () => api.deleteLecture(l.videoId))}
                    />
                  ) : (
                    <>
                      <Button
                        onClick={() =>
                          void act(l.videoId, () => api.setExcluded(l.videoId, false))
                        }
                        disabled={busy === l.videoId}
                      >
                        되돌리기
                      </Button>
                      {/* **완전삭제는 주인만.** 요약 행 하나를 지우면 그걸
                          구독한 다른 사람의 곳간에서도 사라지고, 그 사람은
                          지운 적이 없는데 없어진 것을 보게 됩니다. 식구는
                          제외함에 두거나 되돌리면 됩니다. */}
                      {me.isOwner && (
                      <button
                        type="button"
                        className={s.danger}
                        onClick={() => setAsking(l.videoId)}
                        disabled={busy === l.videoId}
                      >
                        완전삭제
                      </button>
                      )}
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </Screen>
  );
}

/** 완전삭제는 되돌릴 수 없어서 한 번 묻습니다. 별도 창을 띄우지 않고
 *  그 자리에서 물어야 무엇을 지우는지 눈에 남습니다. */
function Confirm({
  busy,
  onConfirm,
  onCancel,
}: {
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className={s.confirm}>
      <span className={s.confirmText}>요약을 지우고 다시 수집하지 않습니다.</span>
      <Button onClick={onCancel} disabled={busy}>
        취소
      </Button>
      <button type="button" className={s.dangerSolid} onClick={onConfirm} disabled={busy}>
        {busy ? "지우는 중…" : "지웁니다"}
      </button>
    </div>
  );
}
