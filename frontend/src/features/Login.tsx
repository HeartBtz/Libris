import { useState } from "react";
import type { ReactNode } from "react";
import { send } from "../api";
import { locales, useI18n } from "../i18n";
import type { Locale } from "../i18n";
import type { ThemePreference } from "../theme";
import type { Run, User } from "../types";
import { Button, Field, IconButton, Input, Select } from "../ui";

export function Login({
  run,
  onLogin,
  error,
  theme,
  setTheme,
}: {
  run: Run;
  onLogin: (user: User) => void;
  error: ReactNode;
  theme: ThemePreference;
  setTheme: (theme: ThemePreference) => void;
}) {
  const { t, locale, setLocale } = useI18n();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const dark =
    theme === "dark" || (theme === "system" && !!window.matchMedia?.("(prefers-color-scheme: dark)").matches);
  return (
    <main className="login-page">
      <div className="login-tools">
        <label className="login-language">
          <span className="sr-only">{t("app.language")}</span>
          <Select value={locale} onChange={(event) => setLocale(event.target.value as Locale)}>
            {locales.map((value) => (
              <option key={value} value={value}>
                {t(value === "fr" ? "language.french" : "language.english")}
              </option>
            ))}
          </Select>
        </label>
        <IconButton
          icon={dark ? "sun" : "moon"}
          label={t("app.theme")}
          variant="secondary"
          onClick={() => setTheme(dark ? "light" : "dark")}
        />
      </div>
      <div className="login-panel">
        <div className="login-brand">
          <img src="/assets/libris-icon.png" alt="" />
          <span>Libris</span>
        </div>
        <div className="login-card">
          <p className="login-eyebrow">{t("login.eyebrow")}</p>
          <h1>{t("login.title")}</h1>
          <p className="muted">{t("login.description")}</p>
          {error}
          <form
            className="stack"
            autoComplete="off"
            onSubmit={(e) => {
              e.preventDefault();
              setBusy(true);
              void run(async () => {
                onLogin(await send<User>("/auth/login", { username, password }));
              }).finally(() => setBusy(false));
            }}
          >
            <Field label={t("login.username")}>
              <Input
                autoComplete="off"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
              />
            </Field>
            <Field label={t("login.password")}>
              <Input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                minLength={12}
                required
              />
            </Field>
            <Button type="submit" variant="primary" size="lg" loading={busy}>
              {busy ? t("login.submitting") : t("login.submit")}
            </Button>
          </form>
        </div>
        <p className="login-footnote">{t("login.firstAccess")}</p>
      </div>
    </main>
  );
}
