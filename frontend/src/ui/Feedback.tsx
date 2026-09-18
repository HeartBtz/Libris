import type { CSSProperties, ReactNode } from "react";
import { cx } from "./Button";
import { Icon } from "./icons";
import type { IconName } from "./icons";
import type { Tone } from "./Badge";

/** Native <progress> keeps the progressbar role and value attribute; CSS draws a slim track. */
export function ProgressBar({
  value,
  max = 100,
  label,
  tone = "accent",
  size = "md",
  indeterminate = false,
  className,
}: {
  value: number;
  max?: number;
  label: string;
  tone?: Tone;
  size?: "sm" | "md";
  indeterminate?: boolean;
  className?: string;
}) {
  return (
    <progress
      className={cx("progress", `progress-${size}`, `tone-${tone}`, className)}
      aria-label={label}
      value={indeterminate ? undefined : value}
      max={max}
    />
  );
}

export function EmptyState({
  icon = "book",
  title,
  description,
  action,
  compact = false,
}: {
  icon?: IconName;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div className={cx("empty-state", compact && "empty-state-compact")}>
      <span className="empty-state-icon">
        <Icon name={icon} size={20} />
      </span>
      <h2 className="empty-state-title">{title}</h2>
      {description && <p className="empty-state-description">{description}</p>}
      {action && <div className="empty-state-action">{action}</div>}
    </div>
  );
}

export function Skeleton({
  width,
  height = 12,
  className,
}: {
  width?: CSSProperties["width"];
  height?: CSSProperties["height"];
  className?: string;
}) {
  return <span className={cx("skeleton", className)} style={{ width, height }} aria-hidden="true" />;
}

/** Placeholder lines shown while data loads; announced once to assistive technologies. */
export function LoadingBlock({ label, lines = 3 }: { label: string; lines?: number }) {
  return (
    <div className="loading-block" role="status" aria-label={label}>
      {Array.from({ length: lines }, (_, index) => (
        <Skeleton key={index} width={`${[92, 76, 84, 60, 70][index % 5]}%`} height={index ? 12 : 16} />
      ))}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return <span className="spinner" role={label ? "status" : undefined} aria-label={label} />;
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>;
}
