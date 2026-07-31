import { Fragment, useState } from "react";
import { api } from "../api";
import type { Keyword, KeywordDraft, KeywordStatus, Language, Schedule } from "../api";
import { Screen } from "../components/Screen";
import { Button, Chip, ErrorState, Loading, Meter, Panel } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { languageLabel, num, scheduleLabel, when } from "../lib/format";
import s from "./Keywords.module.css";

const DEFAULT_DRAFT: KeywordDraft = {
  term: "",
  language: "ko",
  schedule: "daily",
  minDurationSec: 900,
  minExpertScore: 75,
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

  const usage = useAsync(() => api.getUsage(), []);
  const bin = useAsync(() => api.listArchivedKeywords(), []);

  const reloadAll = () => {
    list.reload();
    bin.reload();
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

  const set = <K extends keyof KeywordDraft>(k: K, v: KeywordDraft[K]) =>
    setDraft((d) => ({ ...d, [k]: v }));

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
      {/* 추가 폼 — 항상 펼침 */}
      <form
        className={s.add}
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <div className={s.addHead}>
          <span className="eyebrow">새 키워드 추가</span>
          <span className={s.addHint}>등록하면 몇 분 안에 첫 수집이 돕니다</span>
        </div>

        <div className={s.row}>
          <label className={`${s.fld} ${s.grow}`}>
            <span className={s.label}>검색어</span>
            <input
              type="text"
              placeholder="예: 카프카 파티셔닝 전략"
              value={draft.term}
              onChange={(e) => set("term", e.target.value)}
              required
            />
          </label>
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
            기준 점수를 올리면 수집량이 줄고 토큰도 아낍니다. <strong>75점</strong>은 전문가
            강의만, <strong>70점</strong>은 실무 튜토리얼까지 들어옵니다. 첫 실행은 백로그를
            훑으므로 <strong>1회 최대</strong>를 낮게 잡는 편이 안전합니다.
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
                          <div className={s.term}>{k.term}</div>
                          <div className={s.sub}>
                            {k.language} · 1회 최대 {k.maxPerRun}편
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
    language: keyword.language,
    schedule: keyword.schedule,
    minDurationSec: keyword.minDurationSec,
    minExpertScore: keyword.minExpertScore,
    maxPerRun: keyword.maxPerRun,
  });
  const [saving, setSaving] = useState(false);
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
          <span className={s.label}>검색어</span>
          <input
            type="text"
            value={d.term}
            onChange={(e) => set("term", e.target.value)}
            required
          />
        </label>
        <label className={s.fld}>
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
