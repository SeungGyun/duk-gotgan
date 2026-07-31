import { useState } from "react";
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

  const usage = useAsync(() => api.getUsage(), []);

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
                  return (
                    <tr key={k.id} className={k.id === justAdded ? s.rowNew : undefined}>
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
                        <Button
                          size="small"
                          onClick={() =>
                            void api
                              .setKeywordStatus(k.id, k.status === "paused" ? "active" : "paused")
                              .then(list.reload)
                          }
                        >
                          {k.status === "paused" ? "재개" : "일시정지"}
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>
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
