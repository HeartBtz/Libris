import { useEffect, useId, useRef } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { useI18n } from "../i18n";
import { IconButton, cx } from "./Button";

const FOCUSABLE =
  'a[href], button:not(:disabled), iframe, input:not(:disabled), select:not(:disabled), summary, textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

// Nested dialogs (a confirmation above a form) must only trap focus in the topmost one.
const stack: HTMLElement[] = [];

export function useFocusTrap<T extends HTMLElement>(active: boolean, close: () => void) {
  const container = useRef<T>(null);
  const closeRef = useRef(close);
  closeRef.current = close;

  useEffect(() => {
    const element = container.current;
    if (!active || !element) return;
    const previous = document.activeElement as HTMLElement | null;
    stack.push(element);
    const focusable = () =>
      Array.from(element.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (item) => !item.closest("[inert]"),
      );
    const autofocus = element.querySelector<HTMLElement>("[data-autofocus]");
    (autofocus || focusable()[0] || element).focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (stack[stack.length - 1] !== element) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        closeRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const controls = focusable();
      if (!controls.length) {
        event.preventDefault();
        return;
      }
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    const onFocusIn = (event: FocusEvent) => {
      if (stack[stack.length - 1] !== element) return;
      const target = event.target as Element;
      // Menus opened from inside the trapped area render in <body> but still belong to it.
      if (!element.contains(target) && !target.closest?.('[role="menu"]')) (focusable()[0] || element).focus();
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("focusin", onFocusIn);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("focusin", onFocusIn);
      stack.splice(stack.indexOf(element), 1);
      if (previous?.isConnected) previous.focus();
    };
  }, [active]);

  return container;
}

export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = "md",
  variant = "center",
  ariaLabel,
  closeLabel,
  className,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
  variant?: "center" | "sheet";
  ariaLabel?: string;
  closeLabel?: string;
  className?: string;
}) {
  const { t } = useI18n();
  const titleId = useId();
  const descriptionId = useId();
  const panel = useFocusTrap<HTMLDivElement>(open, onClose);
  if (!open) return null;
  return createPortal(
    <div
      className={cx("dialog-backdrop", `dialog-backdrop-${variant}`)}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
        aria-labelledby={ariaLabel ? undefined : titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className={cx("dialog", `dialog-${variant}`, `dialog-${size}`, className)}
      >
        <header className="dialog-header">
          <div className="dialog-heading">
            <h2 id={titleId} className="dialog-title">
              {title}
            </h2>
            {description && (
              <p id={descriptionId} className="dialog-description">
                {description}
              </p>
            )}
          </div>
          <IconButton icon="x" label={closeLabel || t("Fermer")} onClick={onClose} tooltip={false} />
        </header>
        {children != null && <div className="dialog-body">{children}</div>}
        {footer && <footer className="dialog-footer">{footer}</footer>}
      </div>
    </div>,
    document.body,
  );
}
