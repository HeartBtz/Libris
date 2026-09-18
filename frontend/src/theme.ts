import { useEffect, useState } from "react";

export type ThemePreference = "light" | "dark" | "system";

const query = "(prefers-color-scheme: dark)";

function stored(): ThemePreference {
  const value = localStorage.getItem("theme");
  return value === "light" || value === "dark" ? value : "system";
}

function resolve(preference: ThemePreference): "light" | "dark" {
  if (preference !== "system") return preference;
  return window.matchMedia?.(query).matches ? "dark" : "light";
}

function apply(preference: ThemePreference) {
  const theme = resolve(preference);
  document.documentElement.dataset.theme = theme;
  const background = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
  document
    .querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')
    .forEach((meta) => meta.setAttribute("content", background));
}

/** Applied before the first render so the page never flashes the other theme. */
export function applyStoredTheme() {
  apply(stored());
}

export function useTheme() {
  const [preference, setPreference] = useState<ThemePreference>(stored);
  useEffect(() => {
    apply(preference);
    if (preference === "system") localStorage.removeItem("theme");
    else localStorage.setItem("theme", preference);
    if (preference !== "system" || !window.matchMedia) return;
    const media = window.matchMedia(query);
    const follow = () => apply("system");
    media.addEventListener("change", follow);
    return () => media.removeEventListener("change", follow);
  }, [preference]);
  return [preference, setPreference] as const;
}
