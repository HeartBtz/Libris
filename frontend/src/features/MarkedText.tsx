import { useLayoutEffect, useRef } from "react";
import type { ReactNode, TextareaHTMLAttributes } from "react";
import { registerTranslations, useI18n } from "../i18n";
import { cx } from "../ui";

registerTranslations({
  "Mise en forme du livre (italique, gras, lien…) conservée à l’export": "Book formatting (italic, bold, link…) kept on export",
  "Élément du livre (image, saut de ligne…) conservé à l’export": "Book element (image, line break…) kept on export",
});

// ⟦tN⟧…⟦/tN⟧ wrap an inline element of the book; ⟦xN⟧ stands for an atomic one (image, break…).
export const MARKER = /(⟦\/?[tx]\d+⟧)/g;

export function markers(text: string): string[] {
  return text.match(MARKER) || [];
}

interface Node {
  children: (Node | string)[];
  marker?: string;
}

function parse(text: string): Node {
  const root: Node = { children: [] };
  const stack: Node[] = [root];
  for (const part of text.split(MARKER)) {
    if (!part) continue;
    const top = stack[stack.length - 1];
    const open = /^⟦t(\d+)⟧$/.exec(part);
    const close = /^⟦\/t(\d+)⟧$/.exec(part);
    if (open) {
      const node: Node = { children: [], marker: open[1] };
      top.children.push(node);
      stack.push(node);
    } else if (close && stack.length > 1 && top.marker === close[1]) {
      stack.pop();
    } else if (/^⟦x\d+⟧$/.test(part)) {
      top.children.push({ children: [], marker: `x${part.slice(2, -1)}` });
    } else {
      top.children.push(part);
    }
  }
  return root;
}

/** Source text with the book's inline formatting shown as formatting, never as raw codes. */
export function MarkedText({ text }: { text: string }) {
  const { t } = useI18n();
  const render = (node: Node | string, key: number): ReactNode => {
    if (typeof node === "string") return node;
    if (node.marker?.startsWith("x"))
      return (
        <span key={key} className="inline-atom" title={t("Élément du livre (image, saut de ligne…) conservé à l’export")}>
          <span className="sr-only">{t("Élément du livre (image, saut de ligne…) conservé à l’export")}</span>
        </span>
      );
    return (
      <span key={key} className="inline-format" title={t("Mise en forme du livre (italique, gras, lien…) conservée à l’export")}>
        {node.children.map(render)}
      </span>
    );
  };
  return <>{parse(text).children.map(render)}</>;
}

/**
 * Plain textarea (robust editing, exact text sent to the API) over a mirror that paints the
 * formatting codes as discreet chips. The mirror only draws backgrounds: the visible glyphs
 * always come from the textarea itself, so the caret and selection stay native.
 */
export function MarkerTextarea({ value, className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement> & { value: string }) {
  const area = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const element = area.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight + 2}px`;
  });
  useLayoutEffect(() => {
    const element = area.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      element.style.height = "auto";
      element.style.height = `${element.scrollHeight + 2}px`;
    });
    observer.observe(element.parentElement || element);
    return () => observer.disconnect();
  }, []);
  return (
    <div className={cx("marker-field", className)}>
      <div className="marker-mirror" aria-hidden="true">
        {value.split(MARKER).map((part, index) =>
          /^⟦\/?[tx]\d+⟧$/.test(part) ? (
            <mark key={index} className={part.startsWith("⟦x") ? "marker marker-atom" : "marker"}>
              {part}
            </mark>
          ) : (
            <span key={index}>{part}</span>
          ),
        )}
        {"\u200b"}
      </div>
      <textarea ref={area} className="marker-input" value={value} rows={2} spellCheck {...rest} />
    </div>
  );
}
