import { createContext, useContext } from "react";
import type { Me } from "./api";

/** 지금 보고 있는 사람. 셸이 한 번 받아서 아래로 내려 줍니다.
 *
 *  화면마다 `getMe()` 를 따로 부르지 않습니다. 같은 값을 여러 번 받는 것도
 *  낭비지만, 더 나쁜 것은 **화면마다 도착 시점이 달라서** 관리자 전용 버튼이
 *  잠깐 나타났다 사라지는 것입니다 — 그 사이에 누르면 403 을 봅니다.
 */
const MeContext = createContext<Me | null>(null);

export const MeProvider = MeContext.Provider;

export function useMe(): Me {
  const me = useContext(MeContext);
  if (!me) {
    // 셸 밖(선택 화면)에서 부른 것입니다. 조용히 빈 값을 주면 그 화면이
    // "식구" 로 그려져서 원인을 찾기 어려워집니다.
    throw new Error("useMe 는 로그인한 화면 안에서만 쓸 수 있습니다.");
  }
  return me;
}
