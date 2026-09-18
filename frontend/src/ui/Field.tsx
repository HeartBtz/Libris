import { forwardRef, useLayoutEffect, useRef } from "react";
import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";
import { cx } from "./Button";
import { Icon } from "./icons";

/** Label, control and help text in one block; the wrapping label names the control. */
export function Field({
  label,
  hint,
  error,
  children,
  className,
  inline = false,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  children: ReactNode;
  className?: string;
  inline?: boolean;
}) {
  return (
    <label className={cx("field", inline && "field-inline", className)}>
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
      {error && (
        <span className="field-error" role="alert">
          {error}
        </span>
      )}
    </label>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...rest }, ref) {
    return <input ref={ref} className={cx("input", className)} {...rest} />;
  },
);

export function SearchInput({
  className,
  ...rest
}: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <span className={cx("search-input", className)}>
      <Icon name="search" />
      <input type="search" className="input" {...rest} />
    </span>
  );
}

type TextAreaProps = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  /** Grows with its content instead of scrolling internally. */
  autoGrow?: boolean;
};

export const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(function TextArea(
  { className, autoGrow = false, ...rest },
  forwarded,
) {
  const own = useRef<HTMLTextAreaElement | null>(null);
  useLayoutEffect(() => {
    const element = own.current;
    if (!autoGrow || !element) return;
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight + 2}px`;
  });
  return (
    <textarea
      ref={(element) => {
        own.current = element;
        if (typeof forwarded === "function") forwarded(element);
        else if (forwarded) forwarded.current = element;
      }}
      className={cx("input", "textarea", autoGrow && "textarea-auto", className)}
      {...rest}
    />
  );
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({ className, children, ...rest }, ref) {
    return (
      <span className={cx("select", className)}>
        <select ref={ref} className="input" {...rest}>
          {children}
        </select>
        <Icon name="chevronDown" className="select-chevron" />
      </span>
    );
  },
);

export function Checkbox({
  label,
  description,
  className,
  ...rest
}: Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & {
  label: ReactNode;
  description?: ReactNode;
}) {
  return (
    <label className={cx("checkbox", className)}>
      <input type="checkbox" {...rest} />
      <span className="checkbox-text">
        <span>{label}</span>
        {description && <span className="field-hint">{description}</span>}
      </span>
    </label>
  );
}

export function Switch({
  label,
  description,
  className,
  ...rest
}: Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & {
  label: ReactNode;
  description?: ReactNode;
}) {
  return (
    <label className={cx("switch", className)}>
      <input type="checkbox" role="switch" {...rest} />
      <span className="switch-track" aria-hidden="true" />
      <span className="checkbox-text">
        <span>{label}</span>
        {description && <span className="field-hint">{description}</span>}
      </span>
    </label>
  );
}

export function FormGrid({ children, columns = 2 }: { children: ReactNode; columns?: 1 | 2 | 3 }) {
  return <div className={`form-grid form-grid-${columns}`}>{children}</div>;
}
