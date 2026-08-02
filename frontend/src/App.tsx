import { Navigate, Route, Routes } from "react-router-dom";
import { api, apiMode } from "./api";
import { TopBar } from "./components/TopBar";
import { useAsync } from "./hooks/useAsync";
import { Dashboard } from "./screens/Dashboard";
import { Keywords } from "./screens/Keywords";
import { Lectures } from "./screens/Lectures";
import { Excluded } from "./screens/Excluded";
import { Queue } from "./screens/Queue";
import { Runs } from "./screens/Runs";
import s from "./App.module.css";

export default function App() {
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

      <TopBar
        usage={usage.data}
        lectureCount={overview.data?.totalLectures ?? null}
        keywordCount={keywords.data?.length ?? null}
        runCount={runs.data?.length ?? null}
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
          <Route
            path="/keywords"
            element={<Keywords list={keywords} />}
          />
          <Route path="/queue" element={<Queue />} />
          <Route path="/excluded" element={<Excluded />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  );
}
