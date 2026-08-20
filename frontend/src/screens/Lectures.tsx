import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import type { LectureDetail, LectureQuery, LectureSort, LectureSummary } from "../api";
import { Screen } from "../components/Screen";
import { Chip, Empty, ErrorState, Loading } from "../components/ui";
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

/** 한 쪽에 몇 편씩 받아 올지.
 *
 *  **809편을 통째로 받으면 374KB 입니다.** 폰에서 열면 첫 글자가 뜨기까지
 *  그 전부를 기다리는데, 정작 처음 보이는 것은 열몇 편입니다. 60편이면
 *  28KB 로 끊기고, 목록을 굴리거나 글을 끝까지 읽으면 그때 다음 쪽을
 *  받습니다. 한 화면에 들어가는 것보다 넉넉해야 굴리자마자 멈칫하지
 *  않습니다. */
const PAGE = 60;

/** 목록을 한 쪽씩 받아 쌓습니다.
 *
 *  필터가 바뀌면(=queryKey) 처음부터 다시 받고, 그 전에는 이어 붙이기만
 *  합니다. **받아 둔 것을 버리지 않는 것이 중요합니다** — 읽는 중에 목록이
 *  갈아 끼워지면 "안 본 것 먼저" 정렬이 발밑에서 순서를 바꿔 보던 글이
 *  화면 밖으로 튑니다. */
function useLectureList(query: LectureQuery, queryKey: string) {
  const [rows, setRows] = useState<LectureSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [latestAddedAt, setLatestAddedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  // 지금 요청이 아직 쓸모 있는지. 필터가 바뀌면 세대를 올려서, 늦게 도착한
  // 옛 쪽이 새 목록 뒤에 붙는 것을 막습니다 — 조건에 안 맞는 것이 섞입니다.
  const gen = useRef(0);
  const busy = useRef(false);
  // 콜백이 최신 값을 보되, 값이 바뀔 때마다 관측자가 다시 붙지는 않도록.
  const queryRef = useRef(query);
  queryRef.current = query;
  const countRef = useRef(0);
  countRef.current = rows.length;
  const totalRef = useRef(0);
  totalRef.current = total;

  useEffect(() => {
    const mine = ++gen.current;
    busy.current = true;
    setLoading(true);
    setError(null);
    api
      .listLectures({ ...queryRef.current, limit: PAGE, offset: 0 })
      .then((page) => {
        if (gen.current !== mine) return;
        setRows(page.items);
        setTotal(page.total);
        setLatestAddedAt(page.latestAddedAt);
      })
      .catch((e) => {
        if (gen.current !== mine) return;
        setError(e instanceof Error ? e.message : "목록을 불러오지 못했습니다.");
        setRows([]);
        setTotal(0);
      })
      .finally(() => {
        if (gen.current !== mine) return;
        busy.current = false;
        setLoading(false);
      });
  }, [queryKey, nonce]);

  const loadMore = useCallback(() => {
    if (busy.current || countRef.current >= totalRef.current) return;
    const mine = gen.current;
    busy.current = true;
    setLoadingMore(true);
    api
      .listLectures({ ...queryRef.current, limit: PAGE, offset: countRef.current })
      .then((page) => {
        if (gen.current !== mine) return;
        // 같은 것이 두 번 들어오지 않게. 받는 사이에 읽음 표시가 반영되면
        // "안 본 것 먼저" 순서가 밀려 경계에서 겹칠 수 있습니다.
        setRows((cur) => {
          const seen = new Set(cur.map((r) => r.videoId));
          return [...cur, ...page.items.filter((r) => !seen.has(r.videoId))];
        });
        setTotal(page.total);
      })
      .catch(() => {})
      .finally(() => {
        if (gen.current !== mine) return;
        busy.current = false;
        setLoadingMore(false);
      });
  }, []);

  return {
    rows,
    total,
    latestAddedAt,
    loading,
    loadingMore,
    error,
    hasMore: rows.length < total,
    loadMore,
    reload: useCallback(() => setNonce((n) => n + 1), []),
  };
}

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

/** 이만큼 머물러야 "읽었다"로 봅니다.

    **머문 시간만으로는 부족합니다.** 화면을 열어 두기만 해도 맨 위 강의가
    잠깐 뒤 읽음이 되어, 다음에 왔을 때 읽지도 않은 것이 뒤로 가 있었습니다.
    그래서 손을 댄 뒤(스크롤·클릭·키)부터 셉니다.

    다음 편으로 넘어가면 시간과 무관하게 읽은 것으로 칩니다(아래) — 그래서
    이 값이 짧아도 스쳐 간 것까지 읽음이 되지는 않습니다. */
const READ_DWELL_MS = 1000;

/** 새로 들어온 것이 있는지 확인하는 간격. 워커 사이클이 분 단위라 자주 볼
    이유가 없습니다. */
const NEW_POLL_MS = 60_000;

/** 바닥까지 이만큼 남으면 다음 강의를 미리 붙입니다 — 끊김 없이 이어지되,
    첫 화면에서부터 다음 편을 당겨오지는 않을 만큼의 거리. */
const PREFETCH_PX = 400;

export function Lectures({ onRead }: { onRead?: () => void }) {
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
  // **좁은 화면에서는 접고 시작합니다.** 390px 에서 목록판이 762px 를
  // 차지해, 덕질 화면을 열면 첫 화면이 통째로 목록이고 본문은 한참
  // 아래에 있었습니다. 읽으러 들어온 화면이니 본문이 먼저 보여야 합니다.
  // 챕터도 같은 이유로 접습니다.
  const [listOpen, toggleList] = usePersistentToggle("ui.lectures.list", true, false);
  // 필터가 390px 화면에서 267px(32%)를 먹고 있었습니다. 읽으러 들어온
  // 화면이니 본문이 먼저 보여야 합니다 — 넓은 화면에서는 그대로 둡니다.
  const [filtersOpen, toggleFilters] = usePersistentToggle("ui.lectures.filters", true, false);
  const [chaptersOpen, toggleChapters] = usePersistentToggle(
    "ui.lectures.chapters",
    true,
    false,
  );

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
  const list = useLectureList(query, queryKey);

  // 방금 뺀 것. **목록을 다시 부르지 않고** 화면에서만 걷어냅니다 —
  // 다시 부르면 정렬이 발밑에서 바뀌어 읽던 자리가 튑니다.
  const [excludedIds, setExcludedIds] = useState<Set<string>>(() => new Set());

  const all = list.rows;
  const rows = useMemo(
    () => (excludedIds.size ? all.filter((r) => !excludedIds.has(r.videoId)) : all),
    [all, excludedIds],
  );
  // 화면에 쓰는 편수는 **걸린 것 전체**입니다. 받아 둔 쪽만 세면 굴릴 때마다
  // 숫자가 늘어나 "805편"이 아니라 "지금까지 받은 편수"가 됩니다.
  const totalShown = Math.max(0, list.total - excludedIds.size);

  const handleExcluded = useCallback((id: string) => {
    setExcludedIds((prev) => new Set(prev).add(id));
    setStack((prev) => prev.filter((x) => x !== id));
    // 보던 것이 빠졌으면 시드로 되돌립니다 — 안 그러면 없는 강의를
    // 가리킨 채 목록 강조가 아무 데도 안 붙습니다.
    setViewingId((cur) => (cur === id ? undefined : cur));
  }, []);

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
  const stackRef = useRef<string[]>(stack);
  stackRef.current = stack;
  const feedRef = useRef<HTMLDivElement | null>(null);
  const tailRef = useRef<HTMLDivElement | null>(null);
  // 이어보기의 위쪽 끝. 여기가 화면에 들어오면 앞 편을 위에 붙입니다.
  const headRef = useRef<HTMLDivElement | null>(null);
  // 목록 판과 그 바닥의 감시선 — 굴려서 다음 쪽을 받는 데 씁니다.
  const listPaneRef = useRef<HTMLDivElement | null>(null);
  const listTailRef = useRef<HTMLDivElement | null>(null);

  // 필터 줄의 실제 높이를 --filters-h 로 내보냅니다.
  //
  // 좁은 화면에서 목록 판은 **필터 줄 바로 아래**에 붙어야 합니다. 그런데 그
  // 높이가 접힘 상태마다 다릅니다(올릴 때 66px, 맨 위에서 107px). 숫자를
  // 박아 두면 한쪽에서 반드시 어긋나므로, 상단바(--tb-off)와 같은 방식으로
  // 재서 넘깁니다.
  const filtersRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = filtersRef.current;
    if (!el) return;
    const write = () =>
      document.documentElement.style.setProperty(
        "--filters-h",
        `${el.getBoundingClientRect().height}px`,
      );
    write();
    const ro = new ResizeObserver(write);
    ro.observe(el);
    return () => {
      ro.disconnect();
      document.documentElement.style.removeProperty("--filters-h");
    };
  }, []);
  const itemRefs = useRef(new Map<string, HTMLDivElement>());
  const seedRef = useRef<string | null>(null);
  const activeRef = useRef<string | undefined>(currentId);
  activeRef.current = currentId;

  /** 읽는 줄이 시작되는 높이 — 화면 꼭대기가 아니라 **떠 있는 것들 아래**입니다.
   *
   *  상단바, 좁은 화면에서는 필터 줄과 목록 판까지가 본문 위에 떠 있습니다.
   *  고른 글을 여기에 놓아야 제목부터 보이고, "지금 보고 있는 줄"도 여기를
   *  기준으로 재야 맞습니다.
   *
   *  **위치가 아니라 높이로 잽니다.** 읽는 중에는 이것들이 접혀 화면 위로
   *  밀려나 있어서 위치로 재면 0 에 가깝게 잡히는데, 옮기고 나면 다시 내려와
   *  첫 줄을 덮습니다. */
  const readingTop = useCallback(() => {
    const h = (el: HTMLElement | null, floating: string[]) =>
      el && floating.includes(getComputedStyle(el).position)
        ? el.getBoundingClientRect().height
        : 0;
    return (
      h(document.querySelector<HTMLElement>("header"), ["sticky", "fixed"]) +
      h(filtersRef.current, ["sticky", "fixed"]) +
      // 넓은 화면의 목록 판은 본문 옆 칸이라 덮지 않습니다(sticky). 한 칸으로
      // 접히면 본문 위에 뜨므로(fixed) 그때만 셉니다.
      h(listPaneRef.current, ["fixed"]) +
      14
    );
  }, []);

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
  // 위로도 같은 방식입니다 — 목록에서 중간을 골라 들어와도 그 앞의 것들이
  // 위에 이어 붙어야, 올려서 되짚어 볼 수 있습니다.
  const headId = stack[0];
  const prevId = useMemo(() => {
    if (!headId) return undefined;
    const i = rows.findIndex((r) => r.videoId === headId);
    return i > 0 ? rows[i - 1]?.videoId : undefined;
  }, [rows, headId]);
  // 받아 둔 쪽의 끝까지 읽었을 뿐인데 "마지막 덕질입니다" 라고 하면 안 됩니다.
  const atRealEnd = !nextId && !list.hasMore;

  // 바닥이 가까워지면 다음 강의를 붙인다.
  // 마지막 강의가 다 그려진 뒤에만 관측합니다 — 로딩 자리표시자는 짧아서,
  // 그대로 두면 감시선이 계속 화면 안에 남아 목록 전체가 한 번에 딸려옵니다.
  //
  // **하나가 아니라 집합입니다.** 위로도 붙이게 되면서 "마지막으로 다 그려진
  // 것"이 머리 쪽 글일 수 있게 됐습니다 — 그걸로 꼬리를 재면 아래로 잇는
  // 것이 그대로 멈춰 섭니다.
  const [readyIds, setReadyIds] = useState<Set<string>>(() => new Set());
  const markReady = useCallback((id: string, ready: boolean) => {
    setReadyIds((prev) => {
      if (prev.has(id) === ready) return prev;
      const next = new Set(prev);
      if (ready) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  const loadMore = list.loadMore;
  useEffect(() => {
    const el = tailRef.current;
    if (!el || atRealEnd || !lastId || !readyIds.has(lastId)) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (!entries.some((e) => e.isIntersecting)) return;
        // 다음 것이 이미 목록에 있으면 붙이고, 없으면 **다음 쪽부터 받습니다** —
        // 끊어 받는 목록이라 이어 읽기가 쪽 경계에서 멈추면 안 됩니다.
        if (nextId) setStack((prev) => (prev.includes(nextId) ? prev : [...prev, nextId]));
        else loadMore();
      },
      { rootMargin: `${PREFETCH_PX}px 0px` },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [nextId, readyIds, lastId, atRealEnd, loadMore]);

  // ── 위로 이어 붙이기 ─────────────────────────────────────
  //
  // 목록 아래쪽에서 하나 골라 들어오면 그 글이 통째로 첫 편이 되어, 올려 봐도
  // 위에 아무것도 없었습니다. 아래로 잇는 것과 같은 방식으로 위로도 잇습니다.
  //
  // **위에 무언가가 끼어들면 보던 줄이 그만큼 아래로 밀립니다.** 크롬·파이어
  // 폭스는 스스로 되돌리지만 사파리는 안 합니다. 그래서 화면 위쪽에 걸려
  // 있던 글과 그 위치를 적어 두었다가, 어긋난 만큼만 되돌립니다. 이미 브라우저
  // 가 맞춰 놓았으면 어긋난 값이 0 이라 아무 일도 하지 않습니다.
  const anchorRef = useRef<{ id: string; top: number } | null>(null);

  /** 지금 읽는 줄에 걸려 있는 글과 그 위치.
   *
   *  **화면 꼭대기(0)로 재면 안 됩니다.** 방금 위에 붙인 자리표시자가 그 위를
   *  덮고 있어서, 정작 읽고 있는 글 대신 자리표시자를 붙잡습니다. 그러면
   *  자리표시자가 본문으로 바뀌며 자란 만큼 읽던 글이 통째로 아래로 밀립니다
   *  (실측 564px). 반대로 올려서 앞 편으로 넘어간 뒤라면 그 앞 편이 이 줄을
   *  덮고 있으므로, 같은 기준으로 재도 올바로 앞 편을 붙잡습니다. */
  const readAnchor = useCallback(() => {
    const line = readingTop() + 1;
    for (const id of stackRef.current) {
      const el = itemRefs.current.get(id);
      if (!el) continue;
      const r = el.getBoundingClientRect();
      if (r.bottom > line) return { id, top: r.top };
    }
    return null;
  }, [readingTop]);

  /** 적어 둔 글을 그 자리로 되돌립니다.
   *
   *  **붙잡는 글은 바뀌지 않습니다.** 되돌린 뒤 다시 고르게 두었더니, 문서가
   *  짧아 다 못 되돌린 경우(더 내려갈 자리가 없으면 브라우저가 잘라 냅니다)
   *  기준선이 그새 위쪽 자리표시자 안으로 들어가 버려서, 다음 번엔 자리표시자를
   *  붙잡고 읽던 글을 그대로 흘려보냈습니다. 못 되돌린 만큼은 그 자리를 새
   *  기준으로 적어 둡니다 — 자란 만큼 또 밀리지는 않게. */
  const restoreAnchor = useCallback(() => {
    const a = anchorRef.current;
    if (!a) return;
    const el = itemRefs.current.get(a.id);
    if (!el) return;
    const delta = el.getBoundingClientRect().top - a.top;
    if (Math.abs(delta) > 0.5) window.scrollBy({ top: delta });
    a.top = el.getBoundingClientRect().top;
  }, []);

  // 붙였지만 아직 다 그려지지 않은 글. 자리표시자(70vh)가 본문(수천 px)으로
  // 바뀌는 동안에도 붙잡고 있어야 합니다.
  const [growing, setGrowing] = useState<string | null>(null);

  const prevIdRef = useRef(prevId);
  prevIdRef.current = prevId;

  const prepend = useCallback(() => {
    const id = prevIdRef.current;
    if (!id || stackRef.current.includes(id)) return;
    anchorRef.current = readAnchor();
    setGrowing(id);
    setStack((cur) => (cur.includes(id) ? cur : [id, ...cur]));
  }, [readAnchor]);

  useEffect(() => {
    const el = headRef.current;
    // 하나 붙이는 중에는 멈춥니다 — 자리표시자가 짧아 감시선이 화면에 남아
    // 있으면 앞쪽 전체가 한꺼번에 딸려옵니다(꼬리와 같은 이유).
    if (!el || !prevId || growing) return;
    const obs = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) prepend();
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, [prevId, growing, prepend]);

  // 붙자마자 한 번 — 그리기가 끝나고 화면에 나가기 전에 되돌립니다.
  // **방금 위에 붙인 경우에만입니다.** 목록에서 다른 글을 골라 통째로 다시
  // 쌓은 것이라면 되돌릴 자리 자체가 없어진 것이라, 옛 좌표로 맞추면 엉뚱한
  // 데로 끌려갑니다.
  useLayoutEffect(() => {
    if (!growing || stack[0] !== growing) return;
    restoreAnchor();
  }, [stack, growing, restoreAnchor]);

  // 붙이던 글이 스택에서 빠지면(다시 쌓기·제외) 붙잡기를 놓습니다 —
  // 안 그러면 위로 잇는 것이 그대로 잠깁니다.
  useEffect(() => {
    if (!growing || stack.includes(growing)) return;
    anchorRef.current = null;
    setGrowing(null);
  }, [stack, growing]);

  // 그 뒤 키가 자라는 동안. 그사이 사용자가 굴리면 적어 둔 자리도 따라
  // 옮겨야 합니다 — 안 그러면 다 그려진 순간 읽던 자리에서 화면을 끌어당깁니다.
  useEffect(() => {
    if (!growing) return;
    const el = feedRef.current;
    if (!el) return;
    let lastY = window.scrollY;
    let queued = false;
    const track = () => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => {
        queued = false;
        const y = window.scrollY;
        // 위로 붙이는 보정은 언제나 아래로 미는 쪽이라, 올라간 것은 사용자가
        // 올린 것뿐입니다. **올렸으면 놓아 줍니다** — 앞 편을 보러 간 것인데
        // 뒤 글을 붙잡고 있으면, 자리표시자가 본문으로 바뀌는 순간 보던
        // 자리가 통째로 위로 밀립니다.
        const up = y < lastY - 1;
        lastY = y;
        const a = anchorRef.current;
        if (!a) return;
        if (up) {
          anchorRef.current = null;
          return;
        }
        const cur = itemRefs.current.get(a.id);
        if (cur) a.top = cur.getBoundingClientRect().top;
      });
    };
    const ro = new ResizeObserver(() => restoreAnchor());
    ro.observe(el);
    window.addEventListener("scroll", track, { passive: true });
    return () => {
      ro.disconnect();
      window.removeEventListener("scroll", track);
    };
  }, [growing, restoreAnchor]);

  // 다 그려졌으면 붙잡기를 놓습니다. 계속 잡고 있으면 아래에서 접힌 것을
  // 펼칠 때마다 화면이 따라 움직입니다.
  useEffect(() => {
    if (!growing || !readyIds.has(growing)) return;
    restoreAnchor();
    anchorRef.current = null;
    setGrowing(null);
  }, [growing, readyIds, restoreAnchor]);

  // 목록 판을 바닥까지 굴리면 다음 쪽. 판이 화면보다 짧아 스크롤이 아예
  // 없을 때도(넓은 화면) 감시선이 바로 보이므로 그때는 그 자리에서 채웁니다.
  useEffect(() => {
    const el = listTailRef.current;
    if (!el || !listOpen || !list.hasMore) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) loadMore();
      },
      { root: listPaneRef.current, rootMargin: `${PREFETCH_PX}px 0px` },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [listOpen, list.hasMore, list.loadingMore, loadMore]);

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

  // 읽음 표시는 여기 한 곳으로 모읍니다 — 재는 방법이 둘(머문 시간, 다음
  // 편으로 넘어감)이라, 각자 보내면 같은 것을 두 번 보내고 상단바 숫자도
  // 두 번 깎입니다.
  const rowsRef = useRef(rows);
  rowsRef.current = rows;
  const markRead = useCallback(
    (id: string) => {
      if (readSent.current.has(id)) return;
      readSent.current.add(id);
      // **원래 안 본 것이었을 때만** 메뉴 숫자에서 뺍니다. 이미 읽은 것을
      // 다시 지나가는 일이 흔한데, 그때마다 깎으면 숫자가 0 으로 흘러내립니다.
      if (!rowsRef.current.find((r) => r.videoId === id)?.isRead) onRead?.();
      void api.markRead(id).catch(() => readSent.current.delete(id));
      setReadIds((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
    },
    [onRead],
  );

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
    const timer = window.setTimeout(() => markRead(id), READ_DWELL_MS);
    return () => window.clearTimeout(timer);
  }, [currentId, engaged, markRead]);

  // **다음 편으로 넘어갔으면 앞 편은 읽은 것입니다.** 끝까지 내려서 다음으로
  // 넘어갔다는 것이 머문 시간보다 확실한 신호입니다.
  //
  // 위로 올라간 경우는 아닙니다 — 그건 아직 안 읽은 앞 편을 붙인 것이라,
  // 스택 안에서 **뒤로 간 경우에만** 셉니다.
  const wasViewing = useRef<string | undefined>(undefined);
  useEffect(() => {
    const before = wasViewing.current;
    wasViewing.current = currentId;
    if (!engaged || !before || !currentId || before === currentId) return;
    const from = stack.indexOf(before);
    const to = stack.indexOf(currentId);
    if (from >= 0 && to > from) markRead(before);
  }, [currentId, stack, engaged, markRead]);

  // ── 새로 온 것 알림 ──────────────────────────────────────
  //
  // **목록을 몰래 갈아 끼우지 않습니다.** 다시 부르면 "안 본 것 먼저" 정렬이
  // 발밑에서 순서를 바꿔 읽던 글이 화면 밖으로 튑니다. 개수만 확인해 알리고,
  // 갈아 끼울지는 사용자가 정합니다.
  //
  // 기준 시각은 **목록에 실제로 담긴 것 중 가장 최근에 들어온 때**입니다.
  // 브라우저 시계로 재면 몇 초 어긋나 새 글을 놓칩니다.
  // **기준 시각은 서버가 줍니다.** 받아 둔 쪽에서 제일 최근 것을 고르면,
  // 더 최근 것이 다음 쪽에 있을 때 기준이 과거로 잡혀 이미 목록에 있는 것을
  // "새로 왔다"고 셉니다. 서버는 걸린 것 전체를 두고 고릅니다.
  const [newCount, setNewCount] = useState(0);
  const since = list.latestAddedAt ?? "";

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
    setExcludedIds(new Set());
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

  /** 그 글의 머리로 데려갑니다. 아직 안 붙은 글이면 false. */
  const scrollToLecture = useCallback(
    (id: string) => {
      const el = itemRefs.current.get(id);
      if (!el) return false;
      const top = el.getBoundingClientRect().top + window.scrollY - readingTop();
      // 부드럽게 움직이지 않습니다. 수천 px 을 흘려 보내면 어디로 가는지
      // 읽히지도 않을뿐더러, 그 사이에 앞 편이 위에 붙으면 자리 보정과 서로를
      // 밀어내며 엉뚱한 데서 멈춥니다.
      window.scrollTo({ top: Math.max(0, top) });
      return true;
    },
    [readingTop],
  );

  // 아직 안 붙은 글을 골랐을 때. 새로 쌓인 다음에 데려갑니다.
  const pendingJump = useRef<string | null>(null);
  useLayoutEffect(() => {
    const id = pendingJump.current;
    if (!id) return;
    pendingJump.current = null;
    scrollToLecture(id);
  }, [stack, scrollToLecture]);

  // 목록에서 고르면: **이미 붙어 있는 글이면 그 자리로 옮기기만 합니다.**
  // 예전에는 무조건 다시 쌓아서, 이어 읽으려고 붙여 둔 앞뒤가 통째로 날아가고
  // 고른 글이 다시 첫 편이 됐습니다.
  const pickLecture = useCallback(
    (id: string) => {
      if (!scrollToLecture(id)) pendingJump.current = id;
    },
    [scrollToLecture],
  );

  const toggleKeyword = (id: string) =>
    setSelectedKeywords((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );

  // **정렬 손잡이는 목록 머리에 하나뿐입니다.** 화면 제목 옆에도 같은 것이
  // 있었는데, 정렬은 목록의 성질이지 화면의 성질이 아니라 멀리 떨어져 있으면
  // 무엇을 바꾸는 버튼인지 읽히지 않았습니다. 게다가 위쪽 것은 점수순↔최신순
  // 둘만 오가서 "안 본 것 먼저"로는 돌아올 수 없었습니다 — 같은 일을 하는
  // 손잡이 둘이 서로 다르게 굴었습니다.
  return (
    <Screen title="덕질" subtitle={list.loading ? undefined : `${num(totalShown)}편`}>
      {/* 키워드 필터 — 칩 자체가 설명이라 머리글·선택 수는 두지 않습니다.
          좁은 화면에서는 접습니다(아래 `필터` 버튼). */}
      <div className={`${s.kwFilter} ${filtersOpen ? "" : s.foldable}`}>
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

      <div className={s.filters} ref={filtersRef}>
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
        {/* 좁은 화면에서만 보입니다. 넓은 화면은 접을 이유가 없습니다. */}
        <button
          type="button"
          className={`${s.toggle} ${s.filterToggle} ${filtersOpen ? s.toggleOn : ""}`}
          onClick={toggleFilters}
          aria-pressed={filtersOpen}
        >
          <span className={s.caret}>{filtersOpen ? "▲" : "▼"}</span>
          필터
        </button>
        <input
          className={s.search}
          type="search"
          placeholder="제목 · 요약 · 용어 검색"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div
          className={`${s.seg} ${filtersOpen ? "" : s.foldable}`}
          role="group"
          aria-label="점수 필터"
        >
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
        <div
          className={`${s.seg} ${filtersOpen ? "" : s.foldable}`}
          role="group"
          aria-label="길이 필터"
        >
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
          <div className={s.listPane} ref={listPaneRef}>
            <div className={s.lpHead}>
              <span className="eyebrow">{totalShown}편</span>
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
                onClick={() => pickLecture(l.videoId)}
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

            {/* 목록 바닥이 보이면 다음 쪽을 받습니다. 판 안에서 굴리므로
                관측 기준(root)도 판이어야 합니다 — 화면을 기준으로 두면
                판 안쪽 바닥이 화면에는 늘 보여서 전부 한 번에 딸려옵니다. */}
            {list.hasMore && (
              <div ref={listTailRef} className={s.listTail}>
                {list.loadingMore ? "불러오는 중…" : `${totalShown - rows.length}편 더`}
              </div>
            )}
          </div>
          )}

          {stack.length > 0 ? (
            <div className={s.feed} ref={feedRef}>
              {/* 위쪽 감시선. 여기가 보이면 앞 편을 붙입니다 — 목록 중간에서
                  골라 들어와도 올려서 되짚어 볼 수 있어야 합니다. */}
              <div ref={headRef} className={s.head} aria-hidden="true" />
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
                    onExcluded={handleExcluded}
                  />
                </div>
              ))}
              <div ref={tailRef} className={s.tail} aria-hidden="true" />
              {atRealEnd && <p className={s.feedEnd}>마지막 덕질입니다</p>}
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
  onExcluded,
}: {
  videoId: string;
  chaptersOpen: boolean;
  onToggleChapters: () => void;
  onReady?: (videoId: string, ready: boolean) => void;
  onExcluded?: (videoId: string) => void;
}) {
  const detail = useAsync(() => api.getLecture(videoId), [videoId]);

  // **오류도 다 그려진 것으로 칩니다.** 안 그러면 한 편이 안 열렸다는 이유로
  // 이어보기가 위아래 양쪽 다 그 자리에 멈춰 섭니다.
  const settled = Boolean(detail.data) || Boolean(detail.error);
  useEffect(() => {
    if (!settled) return;
    onReady?.(videoId, true);
    // 빠지면 지웁니다 — 남겨 두면 다시 붙었을 때 자리표시자를 두고 "다
    // 그려졌다"고 재서, 감시선이 화면에 남은 채 앞뒤가 한꺼번에 딸려옵니다.
    return () => onReady?.(videoId, false);
  }, [settled, videoId, onReady]);

  // 제외는 되돌릴 수 있으므로 확인창 없이 바로 보냅니다 — 제외함에서
  // 되돌릴 수 있다는 것을 안내로 알립니다. 매번 묻는 쪽이 더 성가십니다.
  const [excluding, setExcluding] = useState(false);
  const exclude = async () => {
    setExcluding(true);
    try {
      await api.setExcluded(videoId, true);
      onExcluded?.(videoId);
    } finally {
      setExcluding(false);
    }
  };

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
            {/* 제외는 즐겨찾기 옆이 맞습니다 — 둘 다 "이 덕질을 어떻게 둘
                것인가"에 대한 판단이고, 읽다가 바로 누르는 동작입니다. */}
            <button
              type="button"
              className={`${s.iconBtn} ${s.iconDanger}`}
              onClick={exclude}
              disabled={excluding}
              title="제외함으로 보내기"
              aria-label="제외함으로 보내기"
            >
              <IconExclude />
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

function IconExclude() {
  return (
    <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.4" />
      <path d="M5.4 5.4l5.2 5.2" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
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
