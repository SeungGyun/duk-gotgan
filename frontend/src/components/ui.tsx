import type { ButtonHTMLAttributes, ReactNode } from "react";
import s from "./ui.module.css";

// ── 칩 ────────────────────────────────────────────────────
type Tone = "pass" | "warn" | "fail" | "neutral" | "accent";

export function Chip({
  tone = "neutral",
  dot = true,
  children,
}: {
  tone?: Tone;
  dot?: boolean;
  children: ReactNode;
}) {
  return (
    <span className={`${s.chip} ${s[tone]} ${dot ? "" : s.plain}`}>{children}</span>
  );
}

// ── 버튼 ──────────────────────────────────────────────────
interface BtnProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "primary";
  size?: "default" | "small";
}

export function Button({
  variant = "default",
  size = "default",
  className = "",
  type = "button",
  ...rest
}: BtnProps) {
  const cls = [s.btn, variant === "primary" && s.primary, size === "small" && s.small, className]
    .filter(Boolean)
    .join(" ");
  return <button type={type} className={cls} {...rest} />;
}

// ── 패널 ──────────────────────────────────────────────────
export function Panel({
  title,
  aside,
  children,
  bodyless = false,
  className = "",
}: {
  title?: string;
  aside?: ReactNode;
  children: ReactNode;
  /** 표·목록처럼 자체 여백을 갖는 내용이면 true */
  bodyless?: boolean;
  className?: string;
}) {
  return (
    <section className={`${s.panel} ${className}`}>
      {title && (
        <header className={s.panelHead}>
          <h2>{title}</h2>
          {aside}
        </header>
      )}
      {bodyless ? children : <div className={s.panelBody}>{children}</div>}
    </section>
  );
}

// ── 미터 ──────────────────────────────────────────────────
export function Meter({
  value,
  max,
  height = 6,
  className = "",
}: {
  value: number;
  max: number;
  height?: number;
  className?: string;
}) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div
      className={`${s.meter} ${pct >= 90 ? s.meterOver : ""} ${className}`}
      style={{ height }}
      role="meter"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <i style={{ width: `${pct}%` }} />
    </div>
  );
}

// ── 길이 트랙 ─────────────────────────────────────────────
export function DurationTrack({
  durationSec,
  maxDurationSec,
  markers,
}: {
  durationSec: number;
  /** 목록 내 최장 강의. 이것 대비 비율로 폭이 정해진다. */
  maxDurationSec: number;
  markers: number[];
}) {
  const width = maxDurationSec > 0 ? (durationSec / maxDurationSec) * 100 : 100;
  return (
    <div className={s.rail} aria-hidden="true">
      <div className={s.track} style={{ width: `${width}%` }}>
        {markers.map((at, i) => (
          <i
            key={`${at}-${i}`}
            className={s.tick}
            style={{ left: `${durationSec > 0 ? (at / durationSec) * 100 : 0}%` }}
          />
        ))}
      </div>
    </div>
  );
}

// ── 로딩 / 오류 / 비어 있음 ───────────────────────────────
export function Loading({ label = "불러오는 중" }: { label?: string }) {
  return (
    <p className={s.state} role="status">
      {label}…
    </p>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className={`${s.state} ${s.stateError}`} role="alert">
      {message}
      {onRetry && (
        <div className={s.stateAction}>
          <Button size="small" onClick={onRetry}>
            다시 시도
          </Button>
        </div>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className={s.state}>{children}</p>;
}
