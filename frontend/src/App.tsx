import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api, apiMode } from "./api";
import type { Me } from "./api";
import { TopBar } from "./components/TopBar";
import { Loading } from "./components/ui";
import { useAsync } from "./hooks/useAsync";
import { MeProvider } from "./me";
import { Dashboard } from "./screens/Dashboard";
import { Keywords } from "./screens/Keywords";
import { Lectures } from "./screens/Lectures";
import { Excluded } from "./screens/Excluded";
import { Queue } from "./screens/Queue";
import { Runs } from "./screens/Runs";
import { About } from "./screens/About";
import { Who } from "./screens/Who";
import s from "./App.module.css";

export default function App() {
  const { pathname } = useLocation();

  // **누구인지 먼저 압니다.** 이게 끝나기 전에 다른 것을 부르면, 쿠키가
  // 없는 사람에게 401 이 여러 개 한꺼번에 나면서 화면이 깜빡였다가
  // 선택 화면으로 갑니다.
  const me = useAsync(() => api.getMe(), []);

  // 선택 화면은 로그인 전 화면이라 셸(상단바·집계) 밖에 있습니다.
  // 다 고르면 `Who` 가 페이지를 통째로 다시 읽습니다 — 아래 `me` 가
  // 쿠키 없던 시절의 401 을 들고 있어서, 주소만 바꾸면 도로 튕겨납니다.
  if (pathname === "/who") return <Who />;
  // 소개도 로그인 앞입니다 — 처음 온 사람이 "여기가 뭐 하는 곳인지" 를
  // 먼저 볼 수 있어야 고를지 말지 정합니다.
  if (pathname === "/about") return <About />;

  if (me.loading && !me.data) return <Loading />;
  if (!me.data) return <Navigate to="/who" replace />;

  return <Shell me={me.data} onMeChanged={me.reload} />;
}

function Shell({ me, onMeChanged }: { me: Me; onMeChanged: () => void }) {
  const { name, isOwner, pinIsDefault } = me;
  // 상단바 카운트·미터는 화면과 무관하게 항상 필요하므로 셸에서 한 번만 부른다
  const usage = useAsync(() => api.getUsage(), []);
  const overview = useAsync(() => api.getOverview(), []);
  const keywords = useAsync(() => api.listKeywords(), []);
  // 실행 기록은 관리자만 봅니다 — 식구에게는 세어 봐야 쓸 데가 없습니다.
  const runs = useAsync(() => (isOwner ? api.listRuns() : Promise.resolve([])), [isOwner]);

  // 메뉴의 덕질 숫자는 **안 본 편수**입니다. 읽는 동안 줄어야 하는데,
  // 개요를 다시 부르면 목록 정렬이 발밑에서 바뀌므로 화면이 알려 주는
  // 만큼 여기서 빼기만 합니다.
  const [unread, setUnread] = useState<number | null>(null);
  useEffect(() => {
    if (overview.data) setUnread(overview.data.unreadLectures);
  }, [overview.data]);
  const noteRead = useCallback(
    () => setUnread((n) => (n == null ? n : Math.max(0, n - 1))),
    [],
  );

  return (
    <MeProvider value={me}>
      {apiMode === "mock" && (
        <div className={s.mockNote}>
          <div className={s.mockNoteInner}>
            <b>목 데이터</b>
            <span>
              백엔드 없이 브라우저 메모리에서 동작 중입니다. 키워드를 등록하면 목록에 반영되지만
              새로고침하면 초기 상태로 돌아갑니다. <code>.env</code> 의{" "}
              <code>VITE_API=http</code> 로 바꾸면 실제 API 를 호출합니다.
            </span>
          </div>
        </div>
      )}

      {/* 첫 비밀번호(0000) 그대로면 선택 화면에서 누구나 관리자로 들어갈 수
          있습니다 — 그러면 "관리자만 지금 실행" 이 잠금이 아니라 표시가 됩니다.
          **어디서 바꾸는지를 여기서 가리킵니다** — 안 그러면 띠만 보고
          어디로 가야 할지 몰라 그대로 두게 됩니다. */}
      {pinIsDefault && (
        <div className={s.pinNote}>
          <div className={s.mockNoteInner}>
            <b>비밀번호가 0000 입니다</b>
            {/* 선택 화면의 버튼에는 이 사람의 **이름**이 적혀 있습니다.
                이름을 바꾸면 문구도 따라가야 하므로 값에서 가져옵니다. */}
            <span>
              같은 공유기에 붙은 사람 누구나 <b>{name}</b>을(를) 눌러 들어올 수 있습니다.
              오른쪽 위 <b>{name}</b> 을(를) 눌러 바꿔 주세요.
            </span>
          </div>
        </div>
      )}

      <TopBar
        usage={usage.data}
        lectureCount={unread}
        keywordCount={keywords.data?.length ?? null}
        runCount={runs.data?.length ?? null}
        me={me}
        onMeChanged={onMeChanged}
      />

      <main>
        <Routes>
          {/* **메인은 덕질입니다.** 곳간에 들어오는 이유가 읽으려는
              것이지 기계가 잘 도는지 보려는 것이 아닙니다. 대시보드는
              관리자가 필요할 때 찾아가는 화면이라 주소를 따로 줍니다. */}
          <Route path="/" element={<Navigate to="/lectures" replace />} />

          <Route path="/lectures" element={<Lectures onRead={noteRead} />} />
          <Route path="/lectures/:videoId" element={<Lectures onRead={noteRead} />} />
          <Route path="/keywords" element={<Keywords list={keywords} />} />
          <Route path="/excluded" element={<Excluded />} />

          {/* 기계를 들여다보는 화면들. **주소를 직접 쳐도 막습니다** —
              메뉴에서만 감추면 링크 하나로 새어 나갑니다. */}
          {isOwner && (
            <>
              <Route
                path="/dashboard"
                element={
                  <Dashboard
                    overview={overview.data}
                    usage={usage.data}
                    loading={overview.loading || usage.loading}
                    error={overview.error ?? usage.error}
                    onRetry={() => {
                      overview.reload();
                      usage.reload();
                    }}
                  />
                }
              />
              <Route path="/queue" element={<Queue />} />
              <Route path="/runs" element={<Runs />} />
            </>
          )}

          <Route path="*" element={<Navigate to="/lectures" replace />} />
        </Routes>
      </main>
    </MeProvider>
  );
}
