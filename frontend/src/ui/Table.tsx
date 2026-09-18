import type { ReactNode, TableHTMLAttributes } from "react";
import { cx } from "./Button";

/** Dense data table; the wrapper scrolls horizontally instead of widening the page. */
export function Table({
  children,
  className,
  label,
  ...rest
}: TableHTMLAttributes<HTMLTableElement> & { children: ReactNode; label?: string }) {
  return (
    <div className="table-scroll" role="region" aria-label={label} tabIndex={label ? 0 : undefined}>
      <table className={cx("table", className)} {...rest}>
        {children}
      </table>
    </div>
  );
}
