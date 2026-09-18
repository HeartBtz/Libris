import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { KeyboardEvent, MouseEvent, ReactNode, Ref } from "react";
import { cx } from "./Button";
import { Icon } from "./icons";
import type { IconName } from "./icons";

export type MenuEntry =
  | {
      kind?: "item";
      label: ReactNode;
      icon?: IconName;
      onSelect?: () => void;
      href?: string;
      target?: string;
      danger?: boolean;
      disabled?: boolean;
      hint?: ReactNode;
      ariaLabel?: string;
    }
  | { kind: "radio"; label: ReactNode; icon?: IconName; checked: boolean; onSelect: () => void }
  | { kind: "separator" }
  | { kind: "label"; label: ReactNode };

export interface MenuTriggerProps {
  ref: Ref<HTMLButtonElement>;
  "aria-haspopup": "menu";
  "aria-expanded": boolean;
  "aria-controls": string;
  onClick: (event: MouseEvent<HTMLButtonElement>) => void;
  onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void;
}

export function Menu({
  trigger,
  items,
  align = "end",
  label,
  placement = "auto",
  className,
}: {
  trigger: (props: MenuTriggerProps) => ReactNode;
  items: MenuEntry[];
  align?: "start" | "end";
  label: string;
  placement?: "auto" | "up";
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [upward, setUpward] = useState(placement === "up");
  const id = useId();
  const root = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const items_ = () =>
    Array.from(list.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]:not([aria-disabled="true"])') || []);
  const close = (restore = true) => {
    setOpen(false);
    if (restore) button.current?.focus();
  };
  useLayoutEffect(() => {
    if (!open || !list.current || placement === "up") return;
    const rect = list.current.getBoundingClientRect();
    const triggerRect = button.current?.getBoundingClientRect();
    setUpward(rect.bottom > window.innerHeight - 8 && (triggerRect?.top || 0) > rect.height + 8);
  }, [open, placement]);
  useEffect(() => {
    if (!open) return;
    items_()[0]?.focus();
    const outside = (event: Event) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", outside);
    document.addEventListener("touchstart", outside);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("touchstart", outside);
    };
  }, [open]);
  const onMenuKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const all = items_();
    const index = all.indexOf(document.activeElement as HTMLElement);
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close();
    } else if (event.key === "Tab") {
      setOpen(false);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      all[(index + step + all.length) % all.length]?.focus();
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      all[event.key === "Home" ? 0 : all.length - 1]?.focus();
    }
  };
  return (
    <span ref={root} className={cx("menu-root", className)}>
      {trigger({
        ref: button,
        "aria-haspopup": "menu",
        "aria-expanded": open,
        "aria-controls": id,
        onClick: () => setOpen((value) => !value),
        onKeyDown: (event) => {
          if (event.key === "ArrowDown" && !open) {
            event.preventDefault();
            setOpen(true);
          }
        },
      })}
      {open && (
        <div
          ref={list}
          id={id}
          role="menu"
          aria-label={label}
          className={cx("menu", `menu-${align}`, upward && "menu-up")}
          onKeyDown={onMenuKey}
        >
          {items.map((item, index) => {
            if (item.kind === "separator") return <div key={index} role="separator" className="menu-separator" />;
            if (item.kind === "label")
              return (
                <div key={index} className="menu-label" role="presentation">
                  {item.label}
                </div>
              );
            if (item.kind === "radio")
              return (
                <button
                  key={index}
                  type="button"
                  role="menuitemradio"
                  aria-checked={item.checked}
                  tabIndex={-1}
                  className="menu-item"
                  onClick={() => {
                    item.onSelect();
                    close();
                  }}
                >
                  {item.icon ? <Icon name={item.icon} /> : <span className="menu-icon-space" />}
                  <span className="menu-item-label">{item.label}</span>
                  {item.checked && <Icon name="check" className="menu-check" />}
                </button>
              );
            const content = (
              <>
                {item.icon ? <Icon name={item.icon} /> : <span className="menu-icon-space" />}
                <span className="menu-item-label">{item.label}</span>
                {item.hint && <span className="menu-hint">{item.hint}</span>}
              </>
            );
            const itemClass = cx("menu-item", item.danger && "menu-item-danger");
            if (item.href)
              return (
                <a
                  key={index}
                  role="menuitem"
                  tabIndex={-1}
                  className={itemClass}
                  href={item.href}
                  target={item.target}
                  rel={item.target ? "noreferrer" : undefined}
                  aria-label={item.ariaLabel}
                  onClick={(event) => {
                    if (item.onSelect) {
                      event.preventDefault();
                      item.onSelect();
                    }
                    close(false);
                  }}
                >
                  {content}
                </a>
              );
            return (
              <button
                key={index}
                type="button"
                role="menuitem"
                tabIndex={-1}
                className={itemClass}
                aria-disabled={item.disabled || undefined}
                aria-label={item.ariaLabel}
                onClick={() => {
                  if (item.disabled) return;
                  close();
                  item.onSelect?.();
                }}
              >
                {content}
              </button>
            );
          })}
        </div>
      )}
    </span>
  );
}
