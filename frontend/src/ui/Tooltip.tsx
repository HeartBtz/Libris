import type { ReactNode } from "react";

/** Visual hint only: the wrapped control must already carry its accessible name. */
export function Tooltip({
  content,
  placement = "top",
  children,
}: {
  content: ReactNode;
  placement?: "top" | "bottom" | "right";
  children: ReactNode;
}) {
  return (
    <span className="tooltip-host">
      {children}
      <span className={`tooltip tooltip-${placement}`} aria-hidden="true">
        {content}
      </span>
    </span>
  );
}
