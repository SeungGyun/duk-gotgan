import type { ReactNode } from "react";
import s from "../App.module.css";

/** 화면 공통 껍데기 — 제목·부제·우측 액션 + 본문 */
export function Screen({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <>
      <div className={s.head}>
        <div>
          <h1>{title}</h1>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {actions && <div className={s.headActions}>{actions}</div>}
      </div>
      <div className={s.body}>{children}</div>
    </>
  );
}
