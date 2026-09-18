import { useRef } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { cx } from "./Button";
import { Icon } from "./icons";
import type { IconName } from "./icons";

export interface TabItem {
  id: string;
  label: ReactNode;
  count?: number;
  icon?: IconName;
  /** Starts a visual group: a thin separator is drawn before the tab. */
  groupStart?: boolean;
}

function rovingKeys(
  event: KeyboardEvent<HTMLElement>,
  container: HTMLElement | null,
  selector: string,
) {
  if (!container || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const items = Array.from(container.querySelectorAll<HTMLElement>(selector));
  const index = items.indexOf(document.activeElement as HTMLElement);
  if (index < 0) return;
  event.preventDefault();
  const next =
    event.key === "Home"
      ? 0
      : event.key === "End"
        ? items.length - 1
        : (index + (event.key === "ArrowRight" ? 1 : -1) + items.length) % items.length;
  items[next].focus();
  items[next].click();
}

export function Tabs({
  items,
  value,
  onChange,
  label,
  idPrefix,
  className,
}: {
  items: TabItem[];
  value: string;
  onChange: (id: string) => void;
  label: string;
  idPrefix: string;
  className?: string;
}) {
  const list = useRef<HTMLDivElement>(null);
  return (
    <div
      ref={list}
      className={cx("tabs", className)}
      role="tablist"
      aria-label={label}
      onKeyDown={(event) => rovingKeys(event, list.current, '[role="tab"]')}
    >
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          role="tab"
          id={`${idPrefix}-tab-${item.id}`}
          aria-selected={item.id === value}
          aria-controls={`${idPrefix}-panel`}
          tabIndex={item.id === value ? 0 : -1}
          className={cx("tab", item.groupStart && "tab-group-start")}
          onClick={() => onChange(item.id)}
        >
          {item.icon && <Icon name={item.icon} />}
          <span>{item.label}</span>
          {!!item.count && <span className="tab-count tabular">{item.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function TabPanel({
  idPrefix,
  value,
  children,
  className,
}: {
  idPrefix: string;
  value: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      id={`${idPrefix}-panel`}
      role="tabpanel"
      aria-labelledby={`${idPrefix}-tab-${value}`}
      tabIndex={-1}
      className={className}
    >
      {children}
    </div>
  );
}

export interface SegmentOption<T extends string> {
  value: T;
  label: ReactNode;
  count?: number;
  icon?: IconName;
  /** Required when the option only shows an icon. */
  ariaLabel?: string;
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  label,
  size = "md",
  className,
}: {
  options: SegmentOption<T>[];
  value: T;
  onChange: (value: T) => void;
  label: string;
  size?: "sm" | "md";
  className?: string;
}) {
  const group = useRef<HTMLDivElement>(null);
  return (
    <div
      ref={group}
      className={cx("segmented", `segmented-${size}`, className)}
      role="radiogroup"
      aria-label={label}
      onKeyDown={(event) => rovingKeys(event, group.current, '[role="radio"]')}
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          aria-label={option.ariaLabel}
          title={option.ariaLabel}
          tabIndex={option.value === value ? 0 : -1}
          className="segment"
          onClick={() => onChange(option.value)}
        >
          {option.icon && <Icon name={option.icon} />}
          {option.label != null && option.label !== "" && <span>{option.label}</span>}
          {option.count !== undefined && <span className="segment-count tabular">{option.count}</span>}
        </button>
      ))}
    </div>
  );
}
