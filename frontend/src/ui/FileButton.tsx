import { useRef } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { cx } from "./Button";
import { Icon } from "./icons";

/** A label styled as a button so the native file picker stays keyboard-accessible. */
export function FileButton({
  label,
  accept,
  multiple = false,
  disabled = false,
  primary = false,
  icon = "upload",
  onFiles,
  inputId,
}: {
  label: ReactNode;
  accept: string;
  multiple?: boolean;
  disabled?: boolean;
  primary?: boolean;
  icon?: "upload" | "plus";
  onFiles: (files: File[]) => void;
  inputId?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <label
      className={cx("btn", "btn-md", primary ? "btn-primary" : "btn-secondary")}
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-disabled={disabled}
      onKeyDown={(e: KeyboardEvent<HTMLLabelElement>) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          if (!disabled) input.current?.click();
        }
      }}
    >
      <Icon name={icon} />
      <span className="btn-label">{label}</span>
      <input
        ref={input}
        type="file"
        accept={accept}
        multiple={multiple}
        hidden
        disabled={disabled}
        id={inputId}
        onChange={(e) => {
          const files = Array.from(e.target.files || []);
          e.target.value = ""; // Let the same files be chosen again after a failed import.
          if (files.length) onFiles(files);
        }}
      />
    </label>
  );
}
