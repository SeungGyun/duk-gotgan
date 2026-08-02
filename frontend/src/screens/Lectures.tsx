import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import type { LectureDetail, LectureQuery, LectureSort, LectureSummary } from "../api";
import { Screen } from "../components/Screen";
import { Button, Chip, Empty, ErrorState, Loading } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { usePersistentToggle } from "../hooks/usePersistentToggle";
import {
  criterionLabel,
  date,
  duration,
  num,
  scoreColor,
  timestamp,
  tokens,
  verdictLabel,
  youtubeAt,
} from "../lib/format";
import s from "./Lectures.module.css";

const SCORE_BANDS = [
  { label: "전체", value: undefined },
  { label: "85+", value: 85 },
  { label: "70+", value: 70 },
] as const;

const LENGTH_BANDS = [
  { label: "전체", min: undefined, max: undefined },
  { label: "~30분", min: undefined, max: 1800 },
  { label: "30~90분", min: 1800, max: 5400 },
  { label: "90분+", min: 5400, max: undefined },
] as const;

/** 첫 항목이 기본값입니다. **최신 등록순이 먼저**인 이유는, 점수순으로 두면
    새로 들어온 덕질이 아래에 묻혀 매번 목록을 훑어야 하기 때문입니다.
    여기서 말하는 "최신"은 영상 공개일이 아니라 곳간에 들어온 순서입니다. */
/** 첫 항목이 기본값입니다.

    **안 읽은 것이 먼저**입니다. 읽은 것이 위에 남아 있으면 새로 온 걸 찾으려고
    매번 목록을 훑게 됩니다. 각 묶음 안에서는 유튜브에 올라온 날짜 최신순 —
    어제 수집했더라도 3년 전 영상이면 최신이 아니니까요. */
const SORTS: { value: LectureSort; label: string }[] = [
  { value: "unread", label: "안 본 것 먼저" },
  { value: "recent", label: "최신순" },
  { value: "score", label: "점수순" },
  { value: "duration", label: "긴 순" },
];

/** 이만큼 머물러야 "읽었다"로 봅니다. 스크롤로 스쳐 간 것까지 읽음으로
    만들면, 안 본 것 먼저 정렬이 첫 스크롤 한 번에 무의미해집니다.

    **머문 시간만으로는 부족합니다.** 화면을 열어 두기만 해도 맨 위 강의가
    4초 뒤 읽음이 되어, 다음에 왔을 때 읽지도 않은 것이 뒤로 가 있었습니다.
    그래서 손을 댄 뒤(스크롤·클릭·키)부터 셉니다. */
const READ_DWELL_MS = 4000;

/** 새로 들어온 것이 있는지 확인하는 간격. 워커 사이클이 분 단위라 자주 볼
    이유가 없습니다. */
const NEW_POLL_MS = 60_000;

/** 목록이 비었을 때 매 렌더 새 배열이 생기지 않게 — 이어보기 옵저버의 의존값 */
const NO_ROWS: LectureSummary[] = [];

/** 바닥까지 이만큼 남으면 다음 강의를 미리 붙입니다 — 끊김 없이 이어지되,
    첫 화면에서부터 다음 편을 당겨오지는 않을 만큼의 거리. */
const PREFETCH_PX = 400;

export function Lectures() {
  const { videoId } = useParams<{ videoId: string }>();
  const navigate = useNavigate();

  // **새로 연 화면은 언제나 목록 맨 위부터.**
  //
  // 주소에 강의가 남아 있으면 거기서부터 펴는데, 그건 지난번에 보던
  // 자리입니다. 그사이 새 덕질이 들어오면 목록 첫 줄과 본문 첫 강의가
  // 어긋나 보이고, 하필 그게 목록 끝이었다면 스크롤해도 뒤에 붙을 것이
  // 없습니다. 열 때 한 번 비우면 둘이 항상 같은 데서 시작합니다.
  // (같은 화면 안에서 목록을 눌러 옮기는 것은 그대로 동작합니다.)
  const cleared = useRef(false);
  useEffect(() => {
    if (cleared.current) return;
    cleared.current = true;
    if (videoId) navigate("/lectures", { replace: true });
  }, [videoId, navigate]);

  const [selectedKeywords, setSelectedKeywords] = useState<string[]>([]);
  const [scoreBand, setScoreBand] = useState(0);
  const [lengthBand, setLengthBand] = useState(0);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<LectureSort>(SORTS[0]!.value);
  // 이번 화면에서 읽은 것. 서버 응답을 기다리지 않고 점을 지웁니다.
  const [readIds, setReadIds] = useState<Set<string>>(() => new Set());

  // 읽기에 집중하고 싶을 때 양옆 패널을 접습니다. 둘 다 접으면 본문이 화면 정중앙.
  const [listOpen, toggleList] = usePersistentToggle("ui.lectures.list", true);
  const [chaptersOpen, toggleChapters] = usePersistentToggle("ui.lectures.chapters", true);

  const keywords = useAsync(() => api.listKeywords(), []);

  const query: LectureQuery = useMemo(() => {
    const band = LENGTH_BANDS[lengthBand]!;
    return {
      keywordIds: selectedKeywords.length ? selectedKeywords : undefined,
      minScore: SCORE_BANDS[scoreBand]!.value,
      minDurationSec: band.min,
      maxDurationSec: band.max,
      q: q.trim() || undefined,
      sort,
    };
  }, [selectedKeywords, scoreBand, lengthBand, q, sort]);

  const queryKey = JSON.stringify(query);
  const list = useAsync(() => api.listLectures(query), [queryKey]);

  // 목록이 준비되면 아무것도 안 고른 상태로 두지 않는다 — 첫 항목을 편다
  const rows = list.data ?? NO_ROWS;

  // 피드를 어디서부터 쌓을지. 주소에 강의가 있으면 그것부터(목록에서 고른
  // 경우), 없으면 정렬 첫 항목부터.
  const seedId = videoId ?? rows[0]?.videoId;

  // **지금 화면에 보이는 강의.** 주소와 분리해서 둡니다.
  //
  // 예전에는 스크롤할 때마다 주소를 바꿨는데(navigate replace), 그러면
  // 새로고침했을 때 마지막으로 스쳐 간 강의부터 다시 시작합니다. 목록 끝까지
  // 내려간 뒤였다면 뒤에 붙일 것이 없어 스크롤해도 아무것도 안 나오고,
  // 왼쪽 목록은 처음부터 보이는데 본문은 중간부터라 둘이 안 맞아 보입니다.
  //
  // 주소는 **사용자가 직접 고를 때만** 바꿉니다. 스크롤은 이 상태만 옮깁니다.
  const [viewingId, setViewingId] = useState<string | undefined>(undefined);
  const currentId = viewingId ?? seedId;

  // ── 이어보기 ──────────────────────────────────────────────
  // 읽던 강의가 끝나면 다음 강의를 아래에 이어 붙이고, 스크롤 위치에 따라
  // 주소와 목록 강조를 따라 옮깁니다. 화면 전환 없이 계속 읽게 하는 게 목적.
  const [stack, setStack] = useState<string[]>([]);
  const feedRef = useRef<HTMLDivElement | null>(null);
  const tailRef = useRef<HTMLDivElement | null>(null);
  const itemRefs = useRef(new Map<string, HTMLDivElement>());
  const seedRef = useRef<string | null>(null);
  const activeRef = useRef<string | undefined>(currentId);
  activeRef.current = currentId;

  // 필터가 바뀌면 처음부터, 목록에서 다른 강의를 고르면 그 강의부터 다시 쌓습니다.
  // 스크롤로 주소만 바뀐 경우(이미 스택에 있는 id)는 그대로 둡니다.
  useEffect(() => {
    if (!seedId) {
      setStack([]);
      return;
    }
    const fresh = seedRef.current !== queryKey;
    seedRef.current = queryKey;
    setStack((prev) => (!fresh && prev.includes(seedId) ? prev : [seedId]));
    setViewingId(undefined);  // 새로 쌓으면 보는 위치도 머리로
  }, [seedId, queryKey]);

  const lastId = stack[stack.length - 1];
  const nextId = useMemo(() => {
    if (!lastId) return undefined;
    const i = rows.findIndex((r) => r.videoId === lastId);
    return i >= 0 ? rows[i + 1]?.videoId : undefined;
  }, [rows, lastId]);

  // 바닥이 가까워지면 다음 강의를 붙인다.
  // 마지막 강의가 다 그려진 뒤에만 관측합니다 — 로딩 자리표시자는 짧아서,
  // 그대로 두면 감시선이 계속 화면 안에 남아 목록 전체가 한 번에 딸려옵니다.
  const [readyId, setReadyId] = useState<string | null>(null);
  const markReady = useCallback((id: string) => setReadyId(id), []);

  useEffect(() => {
    const el = tailRef.current;
    if (!el || !nextId || readyId !== lastId) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setStack((prev) => (prev.includes(nextId) ? prev : [...prev, nextId]));
        }
      },
      { rootMargin: `${PREFETCH_PX}px 0px` },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [nextId, readyId, lastId]);

  // 화면 위쪽 띠에 걸린 강의를 "지금 읽는 강의"로 본다.
  // 좌표를 비교하지 않고 문서 순서(stack)로 고르므로 관측 시점이 엇갈려도 안전합니다.
  useEffect(() => {
    if (stack.length < 2) return;
    const visible = new Set<string>();
    const obs = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const id = (e.target as HTMLElement).dataset.lecture;
          if (!id) continue;
          if (e.isIntersecting) visible.add(id);
          else visible.delete(id);
        }
        const active = stack.find((id) => visible.has(id));
        if (active && active !== activeRef.current) {
          activeRef.current = active;
          setViewingId(active);
        }
      },
      { rootMargin: "-120px 0px -55% 0px" },
    );
    for (const id of stack) {
      const el = itemRefs.current.get(id);
      if (el) obs.observe(el);
    }
    return () => obs.disconnect();
  }, [stack]);

  // 보고 있는 강의를 읽음으로 표시합니다.
  //
  // **목록을 다시 부르지 않습니다.** 읽는 순간 다시 부르면 "안 본 것 먼저"
  // 정렬이 발밑에서 순서를 바꿔, 읽던 글이 화면 밖으로 튑니다. 표시는
  // 서버와 화면에만 반영하고, 순서는 다음에 목록을 열 때 맞춰집니다.
  const readSent = useRef(new Set<string>());

  // 손을 댄 적이 있는지. 열어만 둔 화면은 아무것도 읽지 않은 것입니다.
  const [engaged, setEngaged] = useState(false);
  useEffect(() => {
    if (engaged) return;
    const on = () => setEngaged(true);
    const opts = { passive: true, once: true } as const;
    window.addEventListener("wheel", on, opts);
    window.addEventListener("touchmove", on, opts);
    window.addEventListener("keydown", on, opts);
    window.addEventListener("pointerdown", on, opts);
    return () => {
      window.removeEventListener("wheel", on);
      window.removeEventListener("touchmove", on);
      window.removeEventListener("keydown", on);
      window.removeEventListener("pointerdown", on);
    };
  }, [engaged]);

  useEffect(() => {
    if (!engaged) return;
    if (!currentId || readSent.current.has(currentId)) return;
    const id = currentId;
    const timer = window.setTimeout(() => {
      readSent.current.add(id);
      void api.markRead(id).catch(() => readSent.current.delete(id));
      setReadIds((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
    }, READ_DWELL_MS);
    return () => window.clearTimeout(timer);
  }, [currentId, engaged]);

  // ── 새로 온 것 알림 ──────────────────────────────────────
  //
  // **목록을 몰래 갈아 끼우지 않습니다.** 다시 부르면 "안 본 것 먼저" 정렬이
  // 발밑에서 순서를 바꿔 읽던 글이 화면 밖으로 튑니다. 개수만 확인해 알리고,
  // 갈아 끼울지는 사용자가 정합니다.
  //
  // 기준 시각은 **목록에 실제로 담긴 것 중 가장 최근에 들어온 때**입니다.
  // 브라우저 시계로 재면 몇 초 어긋나 새 글을 놓칩니다.
  const [newCount, setNewCount] = useState(0);
  const since = useMemo(
    () => rows.reduce((max, r) => (r.addedAt > max ? r.addedAt : max), ""),
    [rows],
  );

  useEffect(() => {
    setNewCount(0);
    if (!since) return;
    let alive = true;
    const check = () => {
      void api
        .countNewLectures(query, since)
        .then((n) => alive && setNewCount(n))
        .catch(() => {});
    };
    const id = window.setInterval(check, NEW_POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [since, query]);

  const showNew = useCallback(() => {
    setNewCount(0);
    readSent.current.clear();
    setReadIds(new Set());
    list.reload();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [list]);

  // 목록이 판 안에서 굴러가므로, 보고 있는 줄이 판 밖으로 나가면 끌어옵니다.
  // `nearest` 라서 이미 보이면 아무것도 하지 않습니다 — 읽는 내내 목록이
  // 덜컹거리면 그게 더 거슬립니다.
  useEffect(() => {
    if (!currentId) return;
    const el = document.querySelector<HTMLElement>(`a[href$="/lectures/${currentId}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [currentId]);

  // 목록에서 다른 강의를 고르면 읽던 위치가 아니라 그 강의의 머리로 보냅니다
  const scrollToFeed = useCallback(() => {
    const el = feedRef.current;
    if (!el) return;
    const top = el.getBoundingClientRect().top + window.scrollY - 72;
    if (window.scrollY > top + 8) window.scrollTo({ top, behavior: "smooth" });
  }, []);

  const toggleKeyword = (id: string) =>
    setSelectedKeywords((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );

  return (
    <Screen
      title="덕질"
      subtitle={list.data ? `${num(rows.length)}편` : undefined}
      actions={
        <Button onClick={() => setSort(sort === "score" ? "recent" : "score")}>
          {SORTS.find((x) => x.value === sort)?.label}
        </Button>
      }
    >
      {/* 키워드 필터 — 칩 자체가 설명이라 머리글·선택 수는 두지 않습니다 */}
      <div className={s.kwFilter}>
        <div className={s.kwChips}>
          {(keywords.data ?? [])
            .filter((k) => k.status !== "archived")
            .map((k) => {
              const on = selectedKeywords.includes(k.id);
              return (
                <button
                  key={k.id}
                  type="button"
                  className={`${s.kwChip} ${on ? s.kwChipOn : ""}`}
                  aria-pressed={on}
                  onClick={() => toggleKeyword(k.id)}
                >
                  {k.term} <span className={s.c}>{k.lectureCount}</span>
                </button>
              );
            })}
          {selectedKeywords.length > 0 && (
            <button
              type="button"
              className={`${s.kwChip} ${s.kwClear}`}
              onClick={() => setSelectedKeywords([])}
            >
              전체 해제
            </button>
          )}
          <Link to="/keywords" className={`${s.kwChip} ${s.kwAdd}`}>
            ＋ 키워드 추가
          </Link>
        </div>
      </div>

      <div className={s.filters}>
        <button
          type="button"
          className={`${s.toggle} ${listOpen ? s.toggleOn : ""}`}
          onClick={toggleList}
          aria-pressed={listOpen}
          title={listOpen ? "목록을 접고 본문을 넓게 봅니다" : "목록을 펼칩니다"}
        >
          <span className={s.caret}>{listOpen ? "◀" : "▶"}</span>
          목록
        </button>
        <input
          className={s.search}
          type="search"
          placeholder="제목 · 요약 · 용어 검색"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div className={s.seg} role="group" aria-label="점수 필터">
          {SCORE_BANDS.map((b, i) => (
            <button
              key={b.label}
              type="button"
              aria-pressed={scoreBand === i}
              className={scoreBand === i ? s.segOn : undefined}
              onClick={() => setScoreBand(i)}
            >
              {b.label}
            </button>
          ))}
        </div>
        <div className={s.seg} role="group" aria-label="길이 필터">
          {LENGTH_BANDS.map((b, i) => (
            <button
              key={b.label}
              type="button"
              aria-pressed={lengthBand === i}
              className={lengthBand === i ? s.segOn : undefined}
              onClick={() => setLengthBand(i)}
            >
              {b.label}
            </button>
          ))}
        </div>
      </div>

      {list.error ? (
        <ErrorState message={list.error} onRetry={list.reload} />
      ) : list.loading ? (
        <Loading />
      ) : rows.length === 0 ? (
        <Empty>조건에 맞는 덕질이 없습니다. 필터를 넓혀 보세요.</Empty>
      ) : (
        <div className={`${s.split} ${listOpen ? "" : s.splitSolo}`}>
          {listOpen && (
          <div className={s.listPane}>
            <div className={s.lpHead}>
              <span className="eyebrow">{rows.length}편</span>
              <button
                type="button"
                className={s.lpSort}
                onClick={() => {
                  const i = SORTS.findIndex((x) => x.value === sort);
                  setSort(SORTS[(i + 1) % SORTS.length]!.value);
                }}
              >
                {SORTS.find((x) => x.value === sort)?.label}
              </button>
            </div>

            {/* 새로 들어온 것이 있으면 알리기만 합니다. 누를 때 갈아 끼웁니다 —
                읽는 중에 순서가 바뀌면 보던 글이 화면 밖으로 튑니다. */}
            {newCount > 0 && (
              <button type="button" className={s.newPill} onClick={showNew}>
                새로 온 덕질 {newCount}편 보기
              </button>
            )}

            {rows.map((l) => (
              <Link
                key={l.videoId}
                to={`/lectures/${l.videoId}`}
                replace
                className={[
                  s.row,
                  l.videoId === currentId ? s.rowOn : "",
                  l.isRead || readIds.has(l.videoId) ? s.rowRead : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                aria-current={l.videoId === currentId ? "true" : undefined}
                onClick={scrollToFeed}
                title={`${l.title}\n${l.channelTitle} · ${duration(l.durationSec)} · 전문성 ${l.expertScore}`}
              >
                {/* 안 본 것에만 점을 찍습니다. 본 것에 표시를 하면 목록
                    대부분에 표식이 붙어 아무것도 구분되지 않습니다. */}
                <span
                  className={`${s.dot} ${l.isRead || readIds.has(l.videoId) ? s.dotRead : ""}`}
                  aria-hidden="true"
                />
                <span className={s.rowTitle}>{l.title}</span>
              </Link>
            ))}
          </div>
          )}

          {stack.length > 0 ? (
            <div className={s.feed} ref={feedRef}>
              {stack.map((id, i) => (
                <div
                  key={id}
                  data-lecture={id}
                  ref={(el) => {
                    if (el) itemRefs.current.set(id, el);
                    else itemRefs.current.delete(id);
                  }}
                >
                  {i > 0 && (
                    <div className={s.nextCue}>
                      <span>다음 덕질</span>
                    </div>
                  )}
                  <Reading
                    videoId={id}
                    chaptersOpen={chaptersOpen}
                    onToggleChapters={toggleChapters}
                    onReady={markReady}
                  />
                </div>
              ))}
              <div ref={tailRef} className={s.tail} aria-hidden="true" />
              {!nextId && <p className={s.feedEnd}>마지막 덕질입니다</p>}
            </div>
          ) : (
            <Empty>왼쪽에서 덕질을 고르세요.</Empty>
          )}
        </div>
      )}
    </Screen>
  );
}

// ── 읽기 패널 ─────────────────────────────────────────────
function Reading({
  videoId,
  chaptersOpen,
  onToggleChapters,
  onReady,
}: {
  videoId: string;
  chaptersOpen: boolean;
  onToggleChapters: () => void;
  onReady?: (videoId: string) => void;
}) {
  const detail = useAsync(() => api.getLecture(videoId), [videoId]);

  const loaded = Boolean(detail.data);
  useEffect(() => {
    if (loaded) onReady?.(videoId);
  }, [loaded, videoId, onReady]);

  // 즐겨찾기는 낙관적으로 먼저 뒤집고, 서버가 거절하면 되돌립니다.
  // null 이면 "아직 손대지 않음" — 서버가 준 값을 그대로 씁니다.
  const [favOverride, setFavOverride] = useState<boolean | null>(null);
  useEffect(() => setFavOverride(null), [videoId]);

  if (detail.error) {
    return (
      <div className={s.readPane}>
        <ErrorState message={detail.error} onRetry={detail.reload} />
      </div>
    );
  }
  if (detail.loading || !detail.data) {
    return (
      <div className={`${s.readPane} ${s.readLoading}`}>
        <Loading label="요약 불러오는 중" />
      </div>
    );
  }

  const d = detail.data;
  // 섹션이 챕터를 겸합니다 — 둘이 같은 구간을 두 번 말하고 있었습니다.
  // 옛 데이터는 chapters 를 그대로 씁니다.
  const sections = d.sections ?? [];
  const rail =
    sections.length > 0
      ? sections.map((sec, i) => ({
          title: sec.title,
          startSec: sec.startSec,
          endSec: sections[i + 1]?.startSec ?? d.durationSec,
        }))
      : d.chapters;
  const totalChapterSec = rail.reduce((sum, c) => sum + (c.endSec - c.startSec), 0);
  const prettyUrl = d.youtubeUrl.replace(/^https?:\/\//, "");
  const isFavorite = favOverride ?? d.isFavorite;

  const toggleFavorite = () => {
    const next = !isFavorite;
    setFavOverride(next);
    api.setFavorite(d.videoId, next).catch(() => setFavOverride(!next));
  };

  return (
    <div className={s.readPane}>
      <div className={`${s.detail} ${chaptersOpen ? "" : s.detailSolo}`}>
        <div className={s.detailMain}>
        <div className={s.mainCol}>
      <header className={s.rpHead}>
        <div className={s.rpMeta}>
          <Chip tone={d.verdict === "expert" ? "pass" : "neutral"}>
            {verdictLabel[d.verdict]} {d.expertScore}
          </Chip>
          <span className="mono">{duration(d.durationSec)}</span>
          <span>·</span>
          <span>{d.channelTitle}</span>
          <span>·</span>
          <span className="mono">{date(d.publishedAt)}</span>
          <span>·</span>
          <a className={s.srcLink} href={d.youtubeUrl} target="_blank" rel="noreferrer">
            {prettyUrl}
          </a>
        </div>

        <div className={s.rpTitleRow}>
          <h2 className={s.rpTitle}>{d.title}</h2>
          <div className={s.rpActions}>
            <a
              className={s.iconBtn}
              href={d.youtubeUrl}
              target="_blank"
              rel="noreferrer"
              title="유튜브에서 보기"
              aria-label="유튜브에서 보기"
            >
              <IconPlay />
            </a>
            <button
              type="button"
              className={`${s.iconBtn} ${isFavorite ? s.iconOn : ""}`}
              onClick={toggleFavorite}
              aria-pressed={isFavorite}
              title={isFavorite ? "즐겨찾기 해제" : "즐겨찾기"}
              aria-label={isFavorite ? "즐겨찾기 해제" : "즐겨찾기"}
            >
              <IconStar filled={isFavorite} />
            </button>
            <button
              type="button"
              className={`${s.iconBtn} ${chaptersOpen ? s.iconOn : ""}`}
              onClick={onToggleChapters}
              aria-pressed={chaptersOpen}
              title={chaptersOpen ? "챕터 접기" : "챕터 펼치기"}
              aria-label={chaptersOpen ? "챕터 접기" : "챕터 펼치기"}
            >
              <IconChapters />
            </button>
          </div>
        </div>
      </header>

        <article className={s.read}>
          {/* 섹션이 요약의 본체입니다. 예전에는 개요·핵심 포인트·챕터가
              따로 있었는데 셋이 같은 이야기를 세 번 하고 있었습니다.
              용어·인용·실무 적용도 별도 목록 대신 불릿 안에 녹아 있습니다.
              섹션이 없는 옛 데이터(시드)는 아래 예전 배치로 떨어집니다. */}
          {sections.length > 0 ? (
            <>
              <dl className={s.facts}>
                <dt>대상</dt>
                <dd>{d.targetAudience}</dd>
                {d.prerequisites.length > 0 && (
                  <>
                    <dt>선수 지식</dt>
                    <dd>{d.prerequisites.join(", ")}</dd>
                  </>
                )}
              </dl>

              {d.coverageNote && <p className={s.coverage}>{d.coverageNote}</p>}

              {sections.map((sec, i) => (
                <section key={i} className={s.sec}>
                  <h2 className={s.secHead}>
                    <span className={s.secNo}>{i + 1}</span>
                    <span className={s.secTitle}>{sec.title}</span>
                    <a
                      className={s.ts}
                      href={youtubeAt(d.youtubeUrl, sec.startSec)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {timestamp(sec.startSec)}
                    </a>
                  </h2>
                  <ul className={s.secList}>
                    {sec.bullets.map((b, j) => (
                      <li key={j}>{b}</li>
                    ))}
                  </ul>
                </section>
              ))}

              {d.closing && (
                <p className={s.closing}>
                  <b>한 줄 요약</b> {d.closing}
                </p>
              )}
            </>
          ) : (
            <LegacyBody d={d} />
          )}
        </article>
        </div>

        {chaptersOpen && (
          <aside className={s.timeline}>
            <div className={s.tlCap}>
              <span>{sections.length > 0 ? "목차" : "챕터"}</span>
              <button type="button" className={s.capBtn} onClick={onToggleChapters}>
                접기
              </button>
            </div>
            {/* 높이를 챕터 수에 맞춰 늘립니다. 고정 높이면 챕터가 많을 때
                짧은 챕터의 제목이 반 줄만 남고 잘립니다. */}
            <div
              className={s.tl}
              style={{ height: Math.max(420, rail.length * 38) }}
            >
              {rail.map((c) => (
                <a
                  key={c.startSec}
                  className={s.tlCh}
                  href={youtubeAt(d.youtubeUrl, c.startSec)}
                  target="_blank"
                  rel="noreferrer"
                  style={{ flex: totalChapterSec > 0 ? c.endSec - c.startSec : 1 }}
                  title={`${timestamp(c.startSec)} ${c.title}`}
                >
                  <span className={s.tlTime}>{timestamp(c.startSec)}</span>
                  <span className={s.tlBar} />
                  <span className={s.tlName}>{c.title}</span>
                </a>
              ))}
            </div>
            <p className={s.tlNote}>막대 높이 = 챕터 길이 (짧은 챕터는 제목이 보이도록 최소 높이)</p>
          </aside>
        )}
        </div>

        {/* 부록. 챕터 레일이 여기까지 따라오지 않도록 격자 밖에 둡니다. */}
        <div className={s.tailCol}>
          <details className={s.disc}>
            <summary>전문성 판정 근거 · 종합 {d.expertScore}점</summary>
            <div className={s.discBody}>
              <div className={s.crit}>
                {d.review.criteria.map((c) => (
                  <div key={c.criterion} className={s.critRow}>
                    <span className={s.critName}>{criterionLabel[c.criterion]}</span>
                    <span className={s.cBar}>
                      <i
                        style={{
                          width: `${c.score}%`,
                          background:
                            c.criterion === "commercial" ? "var(--pass)" : scoreColor(c.score),
                        }}
                      />
                    </span>
                    <span className={s.critScore}>{c.score}</span>
                    <span className={s.critEvidence}>{c.evidence}</span>
                  </div>
                ))}
              </div>

              {d.review.redFlags.length > 0 && (
                <ul className={s.flags}>
                  {d.review.redFlags.map((f, i) => (
                    <li key={i}>{f}</li>
                  ))}
                </ul>
              )}

              <p className={s.reviewFoot}>
                <span className="mono">{d.review.model}</span> · 확신도 {d.review.confidence} ·
                입력 <span className="mono">{tokens(d.review.inputTokens)}</span> · 출력{" "}
                <span className="mono">{tokens(d.review.outputTokens)}</span> 토큰 ·{" "}
                {d.review.turns}턴 · 프롬프트 <span className="mono">{d.review.promptVersion}</span>
              </p>
            </div>
          </details>

          <details className={s.disc}>
            <summary>
              자막 원문
              {d.transcriptExpiresAt
                ? ` · 보관 만료 ${date(d.transcriptExpiresAt)}`
                : " · 보관 기간 만료"}
            </summary>
            <div className={s.discBody}>
              <p className={s.reviewFoot} style={{ margin: 0, padding: 0, border: 0 }}>
                {d.transcriptExpiresAt
                  ? "자막 원문은 요약 생성 후 30일간만 보관합니다."
                  : "보관 기간이 지나 원문이 삭제되었고, 타임스탬프 색인만 남아 있습니다."}
              </p>
            </div>
          </details>
        </div>
      </div>
    </div>
  );
}


// ── 옛 형식 본문 ──────────────────────────────────────────
// 섹션 구조 이전에 만들어진 요약(시드 데이터)을 위한 배치입니다.
// 새로 수집되는 강의는 이 경로를 타지 않습니다.
function LegacyBody({ d }: { d: LectureDetail }) {
  return (
    <>
      <p className={s.lead}>{d.oneLiner}</p>
      <p className={s.abstract}>{d.abstract}</p>
      <dl className={s.facts}>
        <dt>대상</dt>
        <dd>{d.targetAudience}</dd>
        {d.prerequisites.length > 0 && (
          <>
            <dt>선수 지식</dt>
            <dd>{d.prerequisites.join(", ")}</dd>
          </>
        )}
      </dl>
      {d.coverageNote && <p className={s.coverage}>{d.coverageNote}</p>}
      <h2>핵심 포인트</h2>
      <div className={s.kp}>
        {d.keyPoints.map((k, i) => (
          <div key={i} className={s.kpItem}>
            <span className={s.kpNo}>{String(i + 1).padStart(2, "0")}</span>
            <div>
              <h3>{k.heading}</h3>
              <p>{k.detail}</p>
              <a className={s.ts} href={youtubeAt(d.youtubeUrl, k.timestampSec)} target="_blank" rel="noreferrer">
                {timestamp(k.timestampSec)}
              </a>
            </div>
          </div>
        ))}
      </div>
      {d.terms.length > 0 && (
        <>
          <h2>용어</h2>
          <dl className={s.terms}>
            {d.terms.map((t) => (
              <div key={t.term} style={{ display: "contents" }}>
                <dt>{t.term}</dt>
                <dd>{t.definition}</dd>
              </div>
            ))}
          </dl>
        </>
      )}
      {d.takeaways.length > 0 && (
        <>
          <h2>실무 적용</h2>
          <ul className={s.take}>
            {d.takeaways.map((t, i) => (
              <li key={i}>{t}</li>
            ))}
          </ul>
        </>
      )}
      {d.quotes.length > 0 && (
        <>
          <h2>인용</h2>
          {d.quotes.map((qq, i) => (
            <blockquote key={i} className={s.quote}>
              <p>&ldquo;{qq.text}&rdquo;</p>
              <footer>
                <a className={s.ts} href={youtubeAt(d.youtubeUrl, qq.timestampSec)} target="_blank" rel="noreferrer">
                  {timestamp(qq.timestampSec)}
                </a>
                <span>{qq.why}</span>
              </footer>
            </blockquote>
          ))}
        </>
      )}
    </>
  );
}

// ── 아이콘 ────────────────────────────────────────────────
// 외부 아이콘 라이브러리를 쓰지 않습니다 — 세 개뿐이고, 선 굵기를
// 본문 타이포와 맞춰야 해서 직접 그리는 편이 정확합니다.
function IconPlay() {
  return (
    <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect x="1" y="3" width="14" height="10" rx="3" stroke="currentColor" strokeWidth="1.4" />
      <path d="M6.6 6.2 10 8l-3.4 1.8V6.2Z" fill="currentColor" />
    </svg>
  );
}

function IconStar({ filled }: { filled: boolean }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M8 1.8l1.85 3.75 4.15.6-3 2.92.71 4.13L8 11.25 4.29 13.2 5 9.07l-3-2.92 4.15-.6L8 1.8Z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
        fill={filled ? "currentColor" : "none"}
      />
    </svg>
  );
}

function IconChapters() {
  return (
    <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M2 3.5h5M2 8h5M2 12.5h5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <rect x="10" y="2.4" width="4" height="4.2" rx="1" stroke="currentColor" strokeWidth="1.3" />
      <rect x="10" y="9.4" width="4" height="4.2" rx="1" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}
