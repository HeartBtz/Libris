import type { HTMLAttributes, ReactNode } from "react";
import { cx } from "./Button";
import { Icon } from "./icons";
import type { IconName } from "./icons";
import type { Tone } from "./Badge";

export function Card({
  title,
  description,
  actions,
  children,
  className,
  padded = true,
  as: Tag = "section",
  ...rest
}: Omit<HTMLAttributes<HTMLElement>, "title"> & {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  padded?: boolean;
  as?: "section" | "div" | "article" | "aside";
}) {
  return (
    <Tag className={cx("card", padded && "card-padded", className)} {...rest}>
      {(title || actions) && (
        <header className="card-header">
          <div className="card-heading">
            {title && <h2 className="card-title">{title}</h2>}
            {description && <p className="card-description">{description}</p>}
          </div>
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </Tag>
  );
}

const calloutIcons: Record<Tone, IconName> = {
  neutral: "info",
  accent: "sparkles",
  info: "info",
  success: "check",
  warning: "alert",
  danger: "alert",
};

export function Callout({
  tone = "info",
  title,
  children,
  actions,
  role,
  className,
  icon,
}: {
  tone?: Tone;
  title?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  role?: "status" | "alert";
  className?: string;
  icon?: IconName;
}) {
  return (
    <div className={cx("callout", `tone-${tone}`, className)} role={role}>
      <Icon name={icon || calloutIcons[tone]} className="callout-icon" />
      <div className="callout-body">
        {title && <strong className="callout-title">{title}</strong>}
        {children && <div className="callout-text">{children}</div>}
        {actions && <div className="callout-actions">{actions}</div>}
      </div>
    </div>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  tone?: Tone;
}) {
  return (
    <div className={cx("stat", tone && `tone-${tone}`)}>
      <span className="stat-value tabular">{value}</span>
      <span className="stat-label">{label}</span>
      {hint && <span className="stat-hint">{hint}</span>}
    </div>
  );
}

export function PageHeader({
  breadcrumb,
  title,
  description,
  actions,
  meta,
}: {
  breadcrumb?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div className="page-header-main">
        {breadcrumb && <div className="breadcrumb">{breadcrumb}</div>}
        <h1 className="page-title">{title}</h1>
        {description && <p className="page-description">{description}</p>}
        {meta}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}
