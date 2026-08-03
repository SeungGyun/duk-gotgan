import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api, apiMode } from "./api";
import { TopBar } from "./components/TopBar";
import { Loading } from "./components/ui";
import { useAsync } from "./hooks/useAsync";
import { Dashboard } from "./screens/Dashboard";
import { Keywords } from "./screens/Keywords";
import { Lectures } from "./screens/Lectures";
import { Excluded } from "./screens/Excluded";
import { Queue } from "./screens/Queue";
import { Runs } from "./screens/Runs";
import { Who } from "./screens/Who";
import s from "./App.module.css";

export default function App() {
  const { pathname } = useLocation();

  // **누구인지 먼저 압니다.** 이게 끝나기 전에 다른 것을 부르면, 쿠키가
  // 없는 사람에게 401 이 여러 개 한꺼번에 나면서 화면이 깜빡였다가
  // 선택 화면으로 갑니다.
  const me = useAsync(() => api.getMe(), []);

  // 선택 화면은 로그인 전 화면이라 셸(상단바·집계) 밖에 있습니다.
  if (pathname === "/who") return <Who />;

  if (me.loading && !me.data) return <Loading />;
  if (!me.data) return <Navigate to="/who" replace />;

  return <Shell name={me.data.name} isOwner={me.data.isOwner} pinIsDefault={me.data.pinIsDefault} />;
}

function Shell({
  name,
  isOwner,
  pinIsDefault,
}: {
  name: string;
  isOwner: boolean;
  pinIsDefault: boolean;
}) {
  // 상단바 카운트·미터는 화면과 무관하게 항상 필요하므로 셸에서 한 번만 부른다
  const usage = useAsync(() => api.getUsage(), []);
  const overview = useAsync(() => api.getOverview(), []);
  const keywords = useAsync(() => api.listKeywords(), []);
  const runs = useAsync(() => api.listRuns(), []);

  return (
    <>
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

      {/* 첫 비밀번호(0000) 그대로면 선택 화면에서 누구나 주인으로 들어갈 수
          있습니다 — 그러면 "주인만 지금 실행" 이 잠금이 아니라 표시가 됩니다. */}
      {pinIsDefault && (
        <div className={s.pinNote}>
          <div className={s.mockNoteInner}>
            <b>비밀번호가 0000 입니다</b>
            <span>
              같은 공유기에 붙은 사람 누구나 <b>주인</b>을 눌러 들어올 수 있습니다.
              키워드 화면에서 바꿔 주세요.
            </span>
          </div>
        </div>
      )}

      <TopBar
        usage={usage.data}
        lectureCount={overview.data?.totalLectures ?? null}
        keywordCount={keywords.data?.length ?? null}
        runCount={runs.data?.length ?? null}
        name={name}
        isOwner={isOwner}
      />

      <main>
        <Routes>
          <Route
            path="/"
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
          <Route path="/lectures" element={<Lectures />} />
          <Route path="/lectures/:videoId" element={<Lectures />} />
          <Route path="/keywords" element={<Keywords list={keywords} />} />
          <Route path="/queue" element={<Queue />} />
          <Route path="/excluded" element={<Excluded />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  );
}
