import { useLayoutEffect, useRef, useState } from "react";
import type { MouseEvent, ReactNode, TextareaHTMLAttributes } from "react";
import { registerTranslations, useI18n } from "../i18n";
import { cx } from "../ui";

registerTranslations({
  "Mise en forme du livre (italique, gras, lien…) conservée à l’export": "Book formatting (italic, bold, link…) kept on export",
  "Élément du livre (image, saut de ligne…) conservé à l’export": "Book element (image, line break…) kept on export",
  "Élément du livre conservé tel quel (image, note, saut de ligne…)": "Book element kept as is (image, note, line break…)",
  "Début d’une mise en forme du livre (italique, gras, lien…)": "Start of book formatting (italic, bold, link…)",
  "Fin d’une mise en forme du livre (italique, gras, lien…)": "End of book formatting (italic, bold, link…)",
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

function describe(code: string) {
  const [, closing, type, number] = /^⟦(\/?)([tx])(\d+)⟧$/.exec(code) || [];
  return { closing: closing === "/", atom: type === "x", number: Number(number) };
}

/**
 * Plain textarea (robust editing, exact text sent to the API) over a mirror that paints the
 * formatting codes as discreet marks. The mirror only draws: the caret and selection come from
 * the textarea, and each code keeps the width of its characters so both layers stay aligned.
 * The source units only name their block element, not the inline ones, so marks are told apart
 * by pair (a colour per code number) rather than by element type.
 */
export function MarkerTextarea({ value, className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement> & { value: string }) {
  const { t } = useI18n();
  const area = useRef<HTMLTextAreaElement>(null);
  const mirror = useRef<HTMLDivElement>(null);
  const [hint, setHint] = useState("");
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
  const label = (code: string) => {
    const { closing, atom } = describe(code);
    return atom
      ? t("Élément du livre conservé tel quel (image, note, saut de ligne…)")
      : closing
        ? t("Fin d’une mise en forme du livre (italique, gras, lien…)")
        : t("Début d’une mise en forme du livre (italique, gras, lien…)");
  };
  // The mirror ignores the pointer: find the mark under it by geometry to name it on hover.
  const onMouseMove = (event: MouseEvent<HTMLTextAreaElement>) => {
    const marks = mirror.current?.querySelectorAll<HTMLElement>("mark") || [];
    let found = "";
    for (const mark of Array.from(marks)) {
      for (const rect of Array.from(mark.getClientRects()))
        if (event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom)
          found = mark.dataset.label || "";
    }
    if (found !== hint) setHint(found);
  };
  return (
    <div className={cx("marker-field", className)}>
      <div ref={mirror} className="marker-mirror" aria-hidden="true">
        {value.split(MARKER).map((part, index) => {
          if (!/^⟦\/?[tx]\d+⟧$/.test(part)) return <span key={index}>{part}</span>;
          const { closing, atom, number } = describe(part);
          return (
            <mark
              key={index}
              data-label={label(part)}
              className={cx("marker", atom ? "marker-atom" : closing ? "marker-close" : "marker-open", `marker-hue-${number % 4}`)}
            >
              {part}
            </mark>
          );
        })}
        {"\u200b"}
      </div>
      <textarea
        ref={area}
        className="marker-input"
        value={value}
        rows={1}
        spellCheck
        title={hint || undefined}
        onMouseMove={onMouseMove}
        onMouseLeave={() => setHint("")}
        {...rest}
      />
    </div>
  );
}
