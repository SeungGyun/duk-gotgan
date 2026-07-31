import { useCallback, useEffect, useRef, useState } from "react";

interface State<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

/**
 * 한 번 부르고 끝나는 조회용. 로딩·오류·재시도를 한 곳에 모읍니다.
 * deps 가 바뀌면 다시 부르고, 늦게 도착한 이전 응답은 버립니다.
 */
export function useAsync<T>(
  fn: () => Promise<T>,
  deps: unknown[],
): State<T> & { reload: () => void } {
  const [state, setState] = useState<State<T>>({ data: null, error: null, loading: true });
  const [nonce, setNonce] = useState(0);
  const runId = useRef(0);

  // fn 은 매 렌더 새로 만들어지므로 ref 로 고정 — deps 만 재실행을 결정한다
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    const id = ++runId.current;
    let alive = true;
    setState((p) => ({ ...p, loading: true, error: null }));

    fnRef
      .current()
      .then((data) => {
        if (!alive || id !== runId.current) return;
        setState({ data, error: null, loading: false });
      })
      .catch((e: unknown) => {
        if (!alive || id !== runId.current) return;
        const message = e instanceof Error ? e.message : "알 수 없는 오류가 발생했습니다.";
        setState({ data: null, error: message, loading: false });
      });

    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { ...state, reload };
}
