import type { ReactNode } from "react";
import { labels } from "../api";
import { useI18n } from "../i18n";
import { cx } from "./Button";

export type Tone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

export function Badge({
  tone = "neutral",
  dot = false,
  className,
  title,
  children,
}: {
  tone?: Tone;
  dot?: boolean;
  className?: string;
  title?: string;
  children: ReactNode;
}) {
  return (
    <span className={cx("badge", `tone-${tone}`, className)} title={title}>
      {dot && <span className="badge-dot" aria-hidden="true" />}
      {children}
    </span>
  );
}

const statusTones: Record<string, Tone> = {
  pending: "neutral",
  ready: "neutral",
  none: "neutral",
  archived: "neutral",
  cancelled: "neutral",
  interrupted: "neutral",
  abandoned: "neutral",
  source_retained: "neutral",
  analyzing: "accent",
  translating: "accent",
  reviewing: "accent",
  syncing: "accent",
  running: "accent",
  paused: "warning",
  waiting: "warning",
  check: "warning",
  warning: "warning",
  blocked: "danger",
  failed: "danger",
  error: "danger",
  refused: "danger",
  completed: "success",
  ok: "success",
  success: "success",
  validated: "success",
  info: "info",
};

export function statusTone(status: string): Tone {
  return statusTones[status] || "neutral";
}

/** A job, book or passage state rendered as a soft coloured pill with its translated label. */
export function StatusPill({ status, label }: { status: string; label?: string }) {
  useI18n();
  return (
    <Badge tone={statusTone(status)} dot>
      {label || labels[status]}
    </Badge>
  );
}
