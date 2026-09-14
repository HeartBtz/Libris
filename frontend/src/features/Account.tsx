import { useEffect, useState } from "react";
import { api, send } from "../api";
import { registerTranslations, useI18n } from "../i18n";
import type { Run, User } from "../types";

registerTranslations({
  "Mon compte": "My account",
  "Mot de passe actuel": "Current password",
  "Nouveau mot de passe": "New password",
  "Confirmer le mot de passe": "Confirm password",
  "Les mots de passe ne correspondent pas.": "Passwords do not match.",
  "Changer le mot de passe": "Change password",
  "Sessions actives": "Active sessions",
  "Session actuelle": "Current session",
  "Autre session": "Other session",
  "Expire le": "Expires on",
  Révoquer: "Revoke",
  "Reprise automatique": "Automatic recovery",
  "Délai de reprise (secondes)": "Retry delay (seconds)",
  "Enregistrer le délai": "Save retry delay",
  "12 caractères minimum. Toutes les sessions seront déconnectées après modification.":
    "At least 12 characters. All sessions will be signed out after this change.",
  "Reprise depuis le checkpoint après une panne réseau, un timeout ou une erreur temporaire. Les pauses manuelles et erreurs d’authentification restent à traiter.":
    "Resume from checkpoint after network failures, timeouts or temporary errors. Manual pauses and authentication errors still need attention.",
  "Délai enregistré. La capacité et le délai demandé par le provider restent prioritaires.":
    "Delay saved. Capacity and the provider's requested delay still take priority.",
});
interface Session {
  id: string;
  current: boolean;
  expires_at: number;
}

export function Account({
  user,
  run,
  onLogout,
}: {
  user: User;
  run: Run;
  onLogout: () => void;
}) {
  const { t, locale } = useI18n();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [password, setPassword] = useState("");
  const [next, setNext] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    void run(async () => setSessions(await api("/auth/sessions")));
  }, [run]);
  return (
    <main className="settings account-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">{user.username}</p>
          <h1>{t("Mon compte")}</h1>
        </div>
        <a href="#library">{t("← Bibliothèque")}</a>
      </div>
      <section className="narrow">
        <h2>{t("Changer le mot de passe")}</h2>
        <p>
          {t(
            "12 caractères minimum. Toutes les sessions seront déconnectées après modification.",
          )}
        </p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            setError("");
            if (next !== confirmation) {
              setError(t("Les mots de passe ne correspondent pas."));
              return;
            }
            setBusy(true);
            void run(async () => {
              await send(
                "/auth/password",
                { current_password: password, new_password: next },
                "PUT",
              );
              onLogout();
            }).finally(() => setBusy(false));
          }}
        >
          <label>
            {t("Mot de passe actuel")}
            <input
              type="password"
              autoComplete="current-password"
              required
              maxLength={200}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <label>
            {t("Nouveau mot de passe")}
            <input
              type="password"
              autoComplete="new-password"
              required
              minLength={12}
              maxLength={200}
              value={next}
              onChange={(e) => setNext(e.target.value)}
            />
          </label>
          <label>
            {t("Confirmer le mot de passe")}
            <input
              type="password"
              autoComplete="new-password"
              required
              minLength={12}
              maxLength={200}
              value={confirmation}
              onChange={(e) => setConfirmation(e.target.value)}
            />
          </label>
          {error && <p role="alert">{error}</p>}
          <button className="primary" disabled={busy}>
            {t("Changer le mot de passe")}
          </button>
        </form>
      </section>
      <section>
        <h2>{t("Sessions actives")}</h2>
        <ul className="account-list">
          {sessions.map((session) => (
            <li key={session.id}>
              <div>
                <strong>
                  {t(session.current ? "Session actuelle" : "Autre session")}
                </strong>
                <p className="muted">
                  {t("Expire le")}{" "}
                  {new Date(session.expires_at * 1000).toLocaleString(locale)}
                </p>
              </div>
              <button
                disabled={busy}
                onClick={() => {
                  setBusy(true);
                  void run(async () => {
                    await api(`/auth/sessions/${session.id}`, {
                      method: "DELETE",
                    });
                    if (session.current) onLogout();
                    else setSessions(await api("/auth/sessions"));
                  }).finally(() => setBusy(false));
                }}
              >
                {t("Révoquer")}
              </button>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}

export function RecoverySettings({ run }: { run: Run }) {
  const { t } = useI18n();
  const [delay, setDelay] = useState(60);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    void run(async () => {
      const value = await api<{ retry_seconds: number }>("/settings/recovery");
      setDelay(value.retry_seconds);
      setReady(true);
    });
  }, [run]);
  return (
    <section className="narrow">
      <h2>{t("Reprise automatique")}</h2>
      <p>
        {t(
          "Reprise depuis le checkpoint après une panne réseau, un timeout ou une erreur temporaire. Les pauses manuelles et erreurs d’authentification restent à traiter.",
        )}
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          setSaved(false);
          void run(async () => {
            await send("/settings/recovery", { retry_seconds: delay }, "PUT");
            setSaved(true);
          }).finally(() => setBusy(false));
        }}
      >
        <label>
          {t("Délai de reprise (secondes)")}
          <input
            type="number"
            min={5}
            max={3600}
            required
            disabled={!ready || busy}
            value={delay}
            onChange={(e) => setDelay(Number(e.target.value))}
          />
        </label>
        <button disabled={!ready || busy}>{t("Enregistrer le délai")}</button>
        {saved && (
          <p role="status">
            {t(
              "Délai enregistré. La capacité et le délai demandé par le provider restent prioritaires.",
            )}
          </p>
        )}
      </form>
    </section>
  );
}
