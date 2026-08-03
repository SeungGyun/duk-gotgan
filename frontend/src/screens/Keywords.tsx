import { Fragment, useState } from "react";
import { api } from "../api";
import type {
  Keyword,
  KeywordDraft,
  KeywordStatus,
  Language,
  Schedule,
  SourceType,
} from "../api";
import { Screen } from "../components/Screen";
import { Button, Chip, ErrorState, Loading, Meter, Panel } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { languageLabel, num, scheduleLabel, when } from "../lib/format";
import s from "./Keywords.module.css";

/** 수집 방식마다 최소 길이 기본값이 다릅니다.
 *
 *  검색(20분) — 유튜브 검색이 short/medium/long 세 칸으로만 걸러서, 20분보다
 *  낮게 잡아도 실제로는 20분 초과만 들어옵니다. **API 제약에서 나온 숫자**이지
 *  강의 품질 기준이 아닙니다.
 *
 *  채널(10분) — 업로드 목록에는 그 제약이 없어 우리가 직접 거릅니다. 실측해
 *  보니 채널은 보통 "5분 미만 클립"과 "10분 이상 본편"으로 갈립니다
 *  (가인지TV 50건: ~5분 35건, 5~10분 0건, 10분 이상 15건). 20분으로 두면
 *  10~20분대 본편이 통째로 버려집니다. */
const MIN_DURATION: Record<SourceType, number> = { search: 0, channel: 0 };

const DEFAULT_DRAFT: KeywordDraft = {
  term: "",
  sourceType: "search",
  language: "any",
  schedule: "daily",
  minDurationSec: MIN_DURATION.search,
  minExpertScore: 45,
  maxPerRun: 10,
};

const statusChip: Record<KeywordStatus, { tone: "pass" | "warn" | "accent" | "neutral"; label: string }> =
  {
    active: { tone: "pass", label: "활성" },
    quota_wait: { tone: "warn", label: "쿼터 대기" },
    pending: { tone: "accent", label: "첫 실행 대기" },
    paused: { tone: "neutral", label: "일시정지" },
    archived: { tone: "neutral", label: "보관됨" },
  };

interface ListState {
  data: Keyword[] | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

export function Keywords({ list }: { list: ListState }) {
  const [draft, setDraft] = useState<KeywordDraft>(DEFAULT_DRAFT);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [justAdded, setJustAdded] = useState<string | null>(null);

  // 수정 중인 행. 표를 인풋으로 바꾸면 칸이 좁아 값이 잘리므로,
  // 행 아래에 추가 폼과 같은 배치의 편집 줄을 펼칩니다.
  const [editing, setEditing] = useState<string | null>(null);
  // 방금 지운 것 — 되돌리기를 그 자리에서 한 번 더 보여줍니다
  const [justDeleted, setJustDeleted] = useState<Keyword | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  const [binOpen, setBinOpen] = useState(false);
  const [blocksOpen, setBlocksOpen] = useState(false);
  const [blockHandle, setBlockHandle] = useState("");

  const usage = useAsync(() => api.getUsage(), []);
  const bin = useAsync(() => api.listArchivedKeywords(), []);
  const blocks = useAsync(() => api.listChannelBlocks(), []);

  const reloadAll = () => {
    list.reload();
    bin.reload();
    blocks.reload();
  };

  /** 행 단위 동작의 공통 처리 — 실패 문구를 표 위에 한 줄로 띄웁니다. */
  const rowAction = async (fn: () => Promise<unknown>) => {
    setRowError(null);
    try {
      await fn();
      reloadAll();
    } catch (e) {
      setRowError(e instanceof Error ? e.message : "요청이 실패했습니다.");
    }
  };

  /** 되돌리기 줄은 잠깐만 띄웁니다. 계속 남겨두면 이미 되살린 키워드를
   *  다시 되돌리라고 권하게 됩니다(그리고 그 요청은 실패합니다). */
  const noteDeleted = (k: Keyword) => {
    setJustDeleted(k);
    window.setTimeout(() => setJustDeleted((cur) => (cur?.id === k.id ? null : cur)), 8000);
  };

  const restore = (k: Keyword) => {
    setJustDeleted((cur) => (cur?.id === k.id ? null : cur));
    void rowAction(() => api.restoreKeyword(k.id));
  };

  const isChannel = draft.sourceType === "channel";

  const set = <K extends keyof KeywordDraft>(k: K, v: KeywordDraft[K]) =>
    setDraft((d) => ({ ...d, [k]: v }));

  /** 방식을 바꾸면 최소 길이 기본값도 따라갑니다. 다만 직접 고쳐 둔 값은
   *  건드리지 않습니다 — 손댄 설정을 말없이 되돌리면 신뢰를 잃습니다. */
  const switchSource = (next: SourceType) =>
    setDraft((d) => ({
      ...d,
      sourceType: next,
      minDurationSec:
        d.minDurationSec === MIN_DURATION[d.sourceType]
          ? MIN_DURATION[next]
          : d.minDurationSec,
    }));

  async function submit() {
    setSubmitting(true);
    setFormError(null);
    try {
      const created = await api.createKeyword(draft);
      setDraft(DEFAULT_DRAFT);
      setJustAdded(created.id);
      list.reload();
      window.setTimeout(() => setJustAdded(null), 1600);
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "등록에 실패했습니다.");
    } finally {
      setSubmitting(false);
    }
  }

  const rows = list.data ?? [];
  const counts = rows.reduce(
    (acc, k) => {
      if (k.status === "active") acc.active += 1;
      else if (k.status === "paused") acc.paused += 1;
      else if (k.status === "pending") acc.pending += 1;
      return acc;
    },
    { active: 0, paused: 0, pending: 0 },
  );

  const activeCount = rows.filter((k) => k.status === "active" || k.status === "quota_wait").length;

  return (
    <Screen
      title="키워드"
      subtitle={
        list.data
          ? `활성 ${counts.active} · 일시정지 ${counts.paused} · 대기 ${counts.pending}`
          : undefined
      }
    >
      <PinPanel />

      {/* 추가 폼 — 항상 펼침 */}
      <form
        className={s.add}
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <div className={s.addHead}>
          <span className="eyebrow">새로 추가</span>
          <div className={s.seg} role="group" aria-label="수집 방식">
            {(
              [
                ["search", "검색어"],
                ["channel", "관심 채널"],
              ] as const
            ).map(([v, label]) => (
              <button
                key={v}
                type="button"
                aria-pressed={draft.sourceType === v}
                className={draft.sourceType === v ? s.segOn : undefined}
                onClick={() => switchSource(v)}
              >
                {label}
              </button>
            ))}
          </div>
          <span className={s.addHint}>
            {isChannel
              ? "채널의 새 영상을 그대로 가져옵니다 — 검색보다 50배 쌉니다"
              : "등록하면 몇 분 안에 첫 수집이 돕니다"}
          </span>
        </div>

        <div className={s.row}>
          <label className={`${s.fld} ${s.grow}`}>
            <span className={s.label}>{isChannel ? "채널 핸들" : "검색어"}</span>
            <input
              type="text"
              placeholder={isChannel ? "예: @gaingetv" : "예: 카프카 파티셔닝 전략"}
              value={draft.term}
              onChange={(e) => set("term", e.target.value)}
              required
            />
          </label>
          {!isChannel && (
            <label className={s.fld}>
              <span className={s.label}>언어</span>
              <select
                value={draft.language}
                onChange={(e) => set("language", e.target.value as Language)}
              >
                {(["ko", "en", "any"] as const).map((v) => (
                  <option key={v} value={v}>
                    {languageLabel[v]}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className={s.fld}>
            <span className={s.label}>주기</span>
            <select
              value={draft.schedule}
              onChange={(e) => set("schedule", e.target.value as Schedule)}
            >
              {(["daily", "twice_weekly", "weekly"] as const).map((v) => (
                <option key={v} value={v}>
                  {scheduleLabel[v]}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className={s.row}>
          <label className={`${s.fld} ${s.sm}`}>
            <span className={s.label}>최소 길이</span>
            <span className={s.unit}>
              <input
                type="number"
                min={1}
                max={240}
                value={Math.round(draft.minDurationSec / 60)}
                onChange={(e) => set("minDurationSec", Number(e.target.value) * 60)}
              />
              <em>분</em>
            </span>
          </label>
          <label className={`${s.fld} ${s.sm}`}>
            <span className={s.label}>기준 점수</span>
            <span className={s.unit}>
              <input
                type="number"
                min={0}
                max={100}
                value={draft.minExpertScore}
                onChange={(e) => set("minExpertScore", Number(e.target.value))}
              />
              <em>점</em>
            </span>
          </label>
          <label className={`${s.fld} ${s.sm}`}>
            <span className={s.label}>1회 최대</span>
            <span className={s.unit}>
              <input
                type="number"
                min={1}
                max={50}
                value={draft.maxPerRun}
                onChange={(e) => set("maxPerRun", Number(e.target.value))}
              />
              <em>편</em>
            </span>
          </label>
          <p className={s.hint}>
            홍보물과 주제 무관은 <strong>점수와 상관없이</strong> 빠집니다. 나머지는 기준
            점수만 넘으면 담기므로, <strong>45점</strong>이면 개론·실무 영상까지 들어오고
            올릴수록 전문 강의만 남습니다.{" "}
            {isChannel ? (
              <>
                직접 고른 채널이라 <strong>조회수는 보지 않습니다</strong>. 최근{" "}
                <strong>6개월</strong> 안에 올라온 영상만 봅니다.
              </>
            ) : (
              <>
                최근 <strong>6개월</strong> 안에 올라온 영상만 봅니다. 최소 길이가{" "}
                <strong>0이면 쇼츠도</strong> 들어오고, 20분 이상으로 올리면 유튜브 검색
                단계에서부터 긴 영상만 받아 후보가 알차집니다.
              </>
            )}
          </p>
          <Button variant="primary" type="submit" disabled={submitting || !draft.term.trim()}>
            {submitting ? "등록 중…" : "추가하고 바로 실행"}
          </Button>
        </div>

        {formError && <p className={s.error}>{formError}</p>}
      </form>

      {rowError && (
        <p className={s.rowError} role="alert">
          {rowError}
        </p>
      )}

      {justDeleted && (
        <div className={s.undo} role="status">
          <span>
            <strong>{justDeleted.term}</strong> 을(를) 삭제 영역으로 옮겼습니다.
          </span>
          <Button size="small" onClick={() => restore(justDeleted)}>
            되돌리기
          </Button>
        </div>
      )}

      {list.error ? (
        <ErrorState message={list.error} onRetry={list.reload} />
      ) : list.loading && rows.length === 0 ? (
        <Loading />
      ) : (
        <Panel bodyless>
          <div className={s.wrap}>
            <table className={s.table}>
              <thead>
                <tr>
                  <th>키워드</th>
                  <th>상태</th>
                  <th>주기</th>
                  <th className={s.num}>최소 길이</th>
                  <th className={s.num}>기준 점수</th>
                  <th className={s.num}>수집</th>
                  <th>마지막 실행</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((k) => {
                  const st = statusChip[k.status];
                  const open = editing === k.id;
                  return (
                    <Fragment key={k.id}>
                      <tr className={k.id === justAdded ? s.rowNew : undefined}>
                        <td>
                          <div className={s.term}>
                            {k.sourceType === "channel" && <span className={s.kind}>채널</span>}
                            {k.channelTitle || k.term}
                          </div>
                          <div className={s.sub}>
                            {k.sourceType === "channel"
                              ? `${k.term} · 1회 최대 ${k.maxPerRun}편`
                              : `${k.language} · 1회 최대 ${k.maxPerRun}편`}
                            {/* 수집 설정은 키워드에 붙어 있어서, 고치면
                                같이 보는 사람 **모두에게** 적용됩니다.
                                모르고 바꾸면 남의 것을 건드린 셈이 되므로
                                미리 알립니다. */}
                            {k.subscriberCount > 1 && (
                              <span className={s.shared}>
                                {" · "}
                                {k.subscriberCount}명이 함께 봅니다
                              </span>
                            )}
                          </div>
                        </td>
                        <td>
                          <Chip tone={st.tone}>{st.label}</Chip>
                        </td>
                        <td className={s.mutedCell}>
                          {k.status === "pending"
                            ? "등록 직후"
                            : k.status === "paused"
                              ? "—"
                              : scheduleLabel[k.schedule]}
                        </td>
                        <td className={s.num}>{Math.round(k.minDurationSec / 60)}분</td>
                        <td className={s.num}>{k.minExpertScore}</td>
                        <td className={s.num}>{k.lectureCount || "—"}</td>
                        <td className={s.mutedCell}>{when(k.lastRunAt)}</td>
                        <td>
                          <div className={s.actions}>
                            <Button
                              size="small"
                              onClick={() => setEditing(open ? null : k.id)}
                              aria-expanded={open}
                            >
                              {open ? "닫기" : "수정"}
                            </Button>
                            <Button
                              size="small"
                              onClick={() =>
                                void rowAction(() =>
                                  api.setKeywordStatus(
                                    k.id,
                                    k.status === "paused" ? "active" : "paused",
                                  ),
                                )
                              }
                            >
                              {k.status === "paused" ? "재개" : "일시정지"}
                            </Button>
                            {/* 되돌릴 수 있는 동작이라 확인 창을 띄우지 않습니다.
                                대신 바로 위에 되돌리기를 한 번 더 내줍니다. */}
                            <Button
                              size="small"
                              className={s.danger}
                              onClick={() => {
                                setEditing(null);
                                noteDeleted(k);
                                void rowAction(() => api.deleteKeyword(k.id));
                              }}
                            >
                              삭제
                            </Button>
                          </div>
                        </td>
                      </tr>
                      {open && (
                        <tr className={s.editRow}>
                          <td colSpan={8}>
                            <EditForm
                              keyword={k}
                              onCancel={() => setEditing(null)}
                              onSave={async (patch) => {
                                await rowAction(() => api.updateKeyword(k.id, patch));
                                setEditing(null);
                              }}
                            />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      <Others mine={rows} onSubscribed={list.reload} />

      {/* 삭제 영역 — 비어 있으면 자리를 차지하지 않습니다 */}
      {(bin.data?.length ?? 0) > 0 && (
        <section className={s.bin}>
          <button
            type="button"
            className={s.binHead}
            onClick={() => setBinOpen((v) => !v)}
            aria-expanded={binOpen}
          >
            <span className={s.binCaret}>{binOpen ? "▾" : "▸"}</span>
            <span className="eyebrow">삭제 영역</span>
            <span className={s.binCount}>{bin.data!.length}</span>
            <span className={s.binHint}>
              지워도 모아둔 강의는 남습니다. 되살리면 그대로 이어집니다.
            </span>
          </button>

          {binOpen && (
            <ul className={s.binList}>
              {bin.data!.map((k) => (
                <li key={k.id}>
                  <div>
                    <div className={s.term}>{k.term}</div>
                    <div className={s.sub}>
                      {when(k.archivedAt)} 삭제
                      {k.lectureCount > 0 && ` · 모은 강의 ${k.lectureCount}편`}
                    </div>
                  </div>
                  <Button size="small" onClick={() => restore(k)}>
                    복구
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {/* 차단한 채널 — 자동 차단은 오판할 수 있어 화면에서 풀 수 있어야 합니다 */}
      <section className={s.bin}>
        <button
          type="button"
          className={s.binHead}
          onClick={() => setBlocksOpen((v) => !v)}
          aria-expanded={blocksOpen}
        >
          <span className={s.binCaret}>{blocksOpen ? "▾" : "▸"}</span>
          <span className="eyebrow">차단한 채널</span>
          <span className={s.binCount}>{blocks.data?.length ?? 0}</span>
          <span className={s.binHint}>
            무관·홍보로 반복해서 걸린 채널은 자동으로 막힙니다. 여기서 풀 수 있습니다.
          </span>
        </button>

        {blocksOpen && (
          <>
            <form
              className={s.blockAdd}
              onSubmit={(e) => {
                e.preventDefault();
                if (!blockHandle.trim()) return;
                void rowAction(() => api.blockChannel(blockHandle.trim())).then(() =>
                  setBlockHandle(""),
                );
              }}
            >
              <input
                type="text"
                placeholder="직접 막을 채널 — 예: @somechannel"
                value={blockHandle}
                onChange={(e) => setBlockHandle(e.target.value)}
              />
              <Button size="small" type="submit" disabled={!blockHandle.trim()}>
                차단
              </Button>
            </form>

            {(blocks.data?.length ?? 0) === 0 ? (
              <p className={s.blockEmpty}>차단한 채널이 없습니다.</p>
            ) : (
              <ul className={s.binList}>
                {blocks.data!.map((b) => (
                  <li key={b.channelId}>
                    <div>
                      <div className={s.term}>
                        {!b.auto && <span className={s.kind}>직접</span>}
                        {b.channelTitle}
                      </div>
                      <div className={s.sub}>
                        {b.reason}
                        {b.rejectedCount > 0 && ` · 탈락 ${b.rejectedCount}회`}
                      </div>
                    </div>
                    <Button
                      size="small"
                      onClick={() => void rowAction(() => api.unblockChannel(b.channelId))}
                    >
                      차단 해제
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </section>

      {usage.data && (
        <Panel title="쿼터 배분" className={s.quotaPanel}>
          <p className={s.quotaText}>
            유튜브 검색은 호출당 100유닛, 일 상한 {num(usage.data.youtubeUnitLimit)}유닛입니다.{" "}
            <strong>하루에 가능한 검색은 100회</strong>이고, 키워드 하나당 최대 2회를 씁니다.
          </p>
          <Meter value={usage.data.youtubeUnits} max={usage.data.youtubeUnitLimit} height={8} />
          <div className={s.quotaFoot}>
            <span>
              오늘 {num(usage.data.youtubeUnits)} 유닛 (활성 키워드 {activeCount}개)
            </span>
            <span>{num(usage.data.youtubeUnitLimit)}</span>
          </div>
          <p className={s.note}>
            키워드가 45개를 넘으면 실행 주기를 주 2회로 자동 하향합니다.
          </p>
        </Panel>
      )}
    </Screen>
  );
}

// ── 수정 폼 ───────────────────────────────────────────────
// 표 칸 안에서 인풋으로 바꾸면 폭이 좁아 값이 잘립니다. 행 아래에
// 추가 폼과 같은 배치로 펼쳐, 무엇을 고치는지가 한눈에 보이게 합니다.
function EditForm({
  keyword,
  onSave,
  onCancel,
}: {
  keyword: Keyword;
  onSave: (patch: Partial<KeywordDraft>) => Promise<void>;
  onCancel: () => void;
}) {
  const [d, setD] = useState<KeywordDraft>({
    term: keyword.term,
    sourceType: keyword.sourceType,
    language: keyword.language,
    schedule: keyword.schedule,
    minDurationSec: keyword.minDurationSec,
    minExpertScore: keyword.minExpertScore,
    maxPerRun: keyword.maxPerRun,
  });
  const [saving, setSaving] = useState(false);
  // 종류는 등록 후에 바꾸지 않습니다 — 검색어를 채널로 바꾸면 지금까지
  // 모은 강의와의 연결이 뜻을 잃습니다. 바꾸려면 새로 등록하는 편이 맞습니다.
  const isChannel = keyword.sourceType === "channel";

  const set = <K extends keyof KeywordDraft>(k: K, v: KeywordDraft[K]) =>
    setD((prev) => ({ ...prev, [k]: v }));

  // 바뀐 값만 보냅니다 — 안 건드린 필드까지 덮어쓰면 다른 곳에서 바뀐 값이
  // 되돌아갈 수 있습니다.
  const patch = Object.fromEntries(
    Object.entries(d).filter(([k, v]) => v !== keyword[k as keyof KeywordDraft]),
  ) as Partial<KeywordDraft>;
  const dirty = Object.keys(patch).length > 0;

  return (
    <form
      className={s.edit}
      onSubmit={(e) => {
        e.preventDefault();
        setSaving(true);
        void onSave(patch).finally(() => setSaving(false));
      }}
    >
      <div className={s.row}>
        <label className={`${s.fld} ${s.grow}`}>
          <span className={s.label}>{isChannel ? "채널 핸들" : "검색어"}</span>
          <input
            type="text"
            value={d.term}
            onChange={(e) => set("term", e.target.value)}
            required
          />
        </label>
        <label className={s.fld} hidden={isChannel}>
          <span className={s.label}>언어</span>
          <select value={d.language} onChange={(e) => set("language", e.target.value as Language)}>
            {(["ko", "en", "any"] as const).map((v) => (
              <option key={v} value={v}>
                {languageLabel[v]}
              </option>
            ))}
          </select>
        </label>
        <label className={s.fld}>
          <span className={s.label}>주기</span>
          <select value={d.schedule} onChange={(e) => set("schedule", e.target.value as Schedule)}>
            {(["daily", "twice_weekly", "weekly"] as const).map((v) => (
              <option key={v} value={v}>
                {scheduleLabel[v]}
              </option>
            ))}
          </select>
        </label>
        <label className={`${s.fld} ${s.sm}`}>
          <span className={s.label}>최소 길이</span>
          <span className={s.unit}>
            <input
              type="number"
              min={1}
              max={240}
              value={Math.round(d.minDurationSec / 60)}
              onChange={(e) => set("minDurationSec", Number(e.target.value) * 60)}
            />
            <em>분</em>
          </span>
        </label>
        <label className={`${s.fld} ${s.sm}`}>
          <span className={s.label}>기준 점수</span>
          <span className={s.unit}>
            <input
              type="number"
              min={0}
              max={100}
              value={d.minExpertScore}
              onChange={(e) => set("minExpertScore", Number(e.target.value))}
            />
            <em>점</em>
          </span>
        </label>
        <label className={`${s.fld} ${s.sm}`}>
          <span className={s.label}>1회 최대</span>
          <span className={s.unit}>
            <input
              type="number"
              min={1}
              max={50}
              value={d.maxPerRun}
              onChange={(e) => set("maxPerRun", Number(e.target.value))}
            />
            <em>편</em>
          </span>
        </label>
        <div className={s.editActions}>
          <Button type="button" size="small" onClick={onCancel}>
            취소
          </Button>
          <Button variant="primary" size="small" type="submit" disabled={saving || !dirty}>
            {saving ? "저장 중…" : "저장"}
          </Button>
        </div>
      </div>
    </form>
  );
}

/** 다른 사람이 이미 등록해 둔 키워드.
 *
 *  **여기서 구독하면 수집 비용이 전혀 늘지 않습니다.** 같은 검색어를 두
 *  사람이 봐도 유튜브 호출도 자막도 요약도 한 번입니다 — `keywords` 의
 *  `UNIQUE(term)` 이 그걸 보장합니다. 같은 말을 새로 등록하는 것보다
 *  언제나 이쪽이 낫습니다.
 *
 *  아무도 안 쓰는 것이 없으면 패널 자체가 안 나옵니다 — 혼자 쓰는 동안
 *  빈 상자가 자리를 차지할 이유가 없습니다. */
function Others({ mine, onSubscribed }: { mine: Keyword[]; onSubscribed: () => void }) {
  const all = useAsync(() => api.listAllKeywords(), []);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const others = (all.data ?? []).filter((k) => !k.isMine);
  if (others.length === 0) return null;

  const subscribe = async (k: Keyword) => {
    setBusy(k.id);
    setError(null);
    try {
      await api.subscribeKeyword(k.id);
      all.reload();
      onSubscribed();
    } catch (e) {
      setError(e instanceof Error ? e.message : "구독하지 못했습니다.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <Panel
      title="다른 사람도 보는 키워드"
      aside={
        <span className={s.othersHint}>
          구독해도 수집은 그대로 한 번입니다 · 내 것 {mine.length}개
        </span>
      }
    >
      {error && <p className={s.othersErr}>{error}</p>}
      <div className={s.others}>
        {others.map((k) => (
          <button
            key={k.id}
            type="button"
            className={s.other}
            onClick={() => void subscribe(k)}
            disabled={busy === k.id}
          >
            <span className={s.otherTerm}>{k.channelTitle || k.term}</span>
            <span className={s.otherMeta}>
              {k.lectureCount}편 · {k.subscriberCount}명
            </span>
            <span className={s.otherAdd} aria-hidden="true">
              +
            </span>
          </button>
        ))}
      </div>
    </Panel>
  );
}


/** 비밀번호 바꾸기.
 *
 *  **주인은 비울 수 없습니다.** 선택 화면에 주인이 그냥 떠 있어서,
 *  비밀번호가 없으면 같은 공유기에 붙은 누구나 눌러서 주인이 됩니다 —
 *  그러면 "주인만 지금 실행" 이 잠금이 아니라 그냥 표시가 됩니다.
 *
 *  첫 비밀번호(0000) 그대로일 때만 펼친 채로 시작합니다. 이미 바꾼
 *  사람에게는 접혀 있어서 자리를 차지하지 않습니다. */
function PinPanel() {
  const me = useAsync(() => api.getMe(), []);
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const needsChange = me.data?.pinIsDefault ?? false;
  const showing = open || needsChange;
  if (!me.data) return null;

  const only4 = (v: string) => v.replace(/\D/g, "").slice(0, 4);

  const save = async () => {
    if (next.length !== 4) return setError("비밀번호는 숫자 네 자리입니다.");
    setBusy(true);
    setError(null);
    try {
      await api.setPin(me.data!.hasPin ? current : null, next);
      setDone(true);
      setCurrent("");
      setNext("");
      me.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "바꾸지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  if (!showing) {
    return (
      <button type="button" className={s.pinOpen} onClick={() => setOpen(true)}>
        비밀번호 {me.data.hasPin ? "바꾸기" : "걸기"}
      </button>
    );
  }

  return (
    <Panel
      title={needsChange ? "비밀번호가 아직 0000 입니다" : "비밀번호"}
      aside={
        <span className={s.othersHint}>
          {me.data.isOwner
            ? "주인은 비밀번호가 있어야 합니다"
            : "안 걸어도 되지만, 걸면 남이 내 것을 못 봅니다"}
        </span>
      }
    >
      {done ? (
        <p className={s.pinDone}>바꿨습니다. 다음에 들어올 때부터 새 비밀번호를 씁니다.</p>
      ) : (
        <div className={s.pinForm}>
          {me.data.hasPin && (
            <label className={s.fld}>
              <span className={s.label}>지금 비밀번호</span>
              <input
                type="tel"
                inputMode="numeric"
                value={current}
                onChange={(e) => setCurrent(only4(e.target.value))}
                placeholder="0000"
              />
            </label>
          )}
          <label className={s.fld}>
            <span className={s.label}>새 비밀번호</span>
            <input
              type="tel"
              inputMode="numeric"
              value={next}
              onChange={(e) => setNext(only4(e.target.value))}
              onKeyDown={(e) => e.key === "Enter" && void save()}
              placeholder="숫자 네 자리"
            />
          </label>
          <Button onClick={() => void save()} disabled={busy}>
            바꾸기
          </Button>
          {!needsChange && (
            <Button onClick={() => setOpen(false)} disabled={busy}>
              닫기
            </Button>
          )}
        </div>
      )}
      {error && <p className={s.othersErr}>{error}</p>}
    </Panel>
  );
}
