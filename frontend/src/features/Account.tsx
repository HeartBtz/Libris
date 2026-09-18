import { useEffect, useState } from "react";
import { api, send } from "../api";
import { formatDateTime, registerTranslations, useI18n } from "../i18n";
import type { Run, User } from "../types";
import { Button, Card, Field, Icon, Input, LoadingBlock, Page, PageHeader } from "../ui";

registerTranslations({
  "Mon compte": "My account",
  "Nom d’utilisateur": "Username",
  "Changer le nom d’utilisateur": "Change username",
  "Nom d’utilisateur enregistré.": "Username saved.",
  "Mot de passe actuel": "Current password",
  "Nouveau mot de passe": "New password",
  "Confirmer le mot de passe": "Confirm password",
  "Les mots de passe ne correspondent pas.": "Passwords do not match.",
  "Changer le mot de passe": "Change password",
  "Sessions actives": "Active sessions",
  "Session actuelle": "Current session",
  "Autre session": "Other session",
  "Expire le {date}": "Expires on {date}",
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
  "Administrateur": "Administrator",
  "Utilisateur": "User",
  "Chargement des sessions…": "Loading sessions…",
});

interface Session {
  id: string;
  current: boolean;
  expires_at: number;
}

export function Account({
  user,
  run,
  onUserChange,
  onLogout,
}: {
  user: User;
  run: Run;
  onUserChange: (user: User) => void;
  onLogout: () => void;
}) {
  const { t } = useI18n();
  const [sessions, setSessions] = useState<Session[] | null>(null);
  const [username, setUsername] = useState(user.username);
  const [usernameSaved, setUsernameSaved] = useState(false);
  const [password, setPassword] = useState("");
  const [next, setNext] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    void run.background(async () => setSessions(await api("/auth/sessions")));
  }, [run]);
  return (
    <Page width="narrow">
      <PageHeader
        title={t("Mon compte")}
        description={`${user.username} · ${user.admin ? t("Administrateur") : t("Utilisateur")}`}
      />
      <div className="stack">
        <Card title={t("Changer le nom d’utilisateur")}>
          <form
            className="inline-form"
            onSubmit={(event) => {
              event.preventDefault();
              setBusy(true);
              setUsernameSaved(false);
              void run(async () => {
                const updated = await send<User>("/auth/username", { username }, "PUT");
                onUserChange(updated);
                setUsernameSaved(true);
              }).finally(() => setBusy(false));
            }}
          >
            <Field label={t("Nom d’utilisateur")}>
              <Input
                autoComplete="username"
                required
                minLength={2}
                maxLength={80}
                pattern="[\w.@-]+"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
              />
            </Field>
            <Button type="submit" disabled={busy || username === user.username}>
              {t("Changer le nom d’utilisateur")}
            </Button>
          </form>
          {usernameSaved && (
            <p role="status" className="form-status tone-text-success">
              {t("Nom d’utilisateur enregistré.")}
            </p>
          )}
        </Card>
        <Card
          title={t("Changer le mot de passe")}
          description={t("12 caractères minimum. Toutes les sessions seront déconnectées après modification.")}
        >
          <form
            className="stack"
            onSubmit={(event) => {
              event.preventDefault();
              setError("");
              if (next !== confirmation) {
                setError(t("Les mots de passe ne correspondent pas."));
                return;
              }
              setBusy(true);
              void run(async () => {
                await send("/auth/password", { current_password: password, new_password: next }, "PUT");
                onLogout();
              }).finally(() => setBusy(false));
            }}
          >
            <Field label={t("Mot de passe actuel")}>
              <Input
                type="password"
                autoComplete="current-password"
                required
                maxLength={200}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </Field>
            <div className="form-grid form-grid-2">
              <Field label={t("Nouveau mot de passe")}>
                <Input
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={12}
                  maxLength={200}
                  value={next}
                  onChange={(e) => setNext(e.target.value)}
                />
              </Field>
              <Field label={t("Confirmer le mot de passe")} error={error || undefined}>
                <Input
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={12}
                  maxLength={200}
                  value={confirmation}
                  onChange={(e) => setConfirmation(e.target.value)}
                />
              </Field>
            </div>
            <div>
              <Button type="submit" variant="primary" disabled={busy}>
                {t("Changer le mot de passe")}
              </Button>
            </div>
          </form>
        </Card>
        <Card title={t("Sessions actives")} padded={false}>
          {sessions === null ? (
            <div className="card-inset">
              <LoadingBlock label={t("Chargement des sessions…")} lines={2} />
            </div>
          ) : (
            <ul className="session-list">
              {sessions.map((session) => (
                <li key={session.id}>
                  <Icon name="monitor" />
                  <div className="grow">
                    <strong>{t(session.current ? "Session actuelle" : "Autre session")}</strong>
                    <p className="subtle">{t("Expire le {date}", { date: formatDateTime(session.expires_at) })}</p>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => {
                      setBusy(true);
                      void run(async () => {
                        await api(`/auth/sessions/${session.id}`, { method: "DELETE" });
                        if (session.current) onLogout();
                        else setSessions(await api("/auth/sessions"));
                      }).finally(() => setBusy(false));
                    }}
                  >
                    {t("Révoquer")}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </Page>
  );
}

export function RecoverySettings({ run }: { run: Run }) {
  const { t } = useI18n();
  const [delay, setDelay] = useState(60);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    void run.background(async () => {
      const value = await api<{ retry_seconds: number }>("/settings/recovery");
      setDelay(value.retry_seconds);
      setReady(true);
    });
  }, [run]);
  return (
    <Card
      className="settings-card"
      title={t("Reprise automatique")}
      description={t(
        "Reprise depuis le checkpoint après une panne réseau, un timeout ou une erreur temporaire. Les pauses manuelles et erreurs d’authentification restent à traiter.",
      )}
    >
      <form
        className="inline-form"
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
        <Field label={t("Délai de reprise (secondes)")}>
          <Input
            type="number"
            min={5}
            max={3600}
            required
            disabled={!ready || busy}
            value={delay}
            onChange={(e) => setDelay(Number(e.target.value))}
          />
        </Field>
        <Button type="submit" variant="primary" disabled={!ready || busy}>
          {t("Enregistrer le délai")}
        </Button>
      </form>
      {saved && (
        <p role="status" className="form-status tone-text-success">
          {t("Délai enregistré. La capacité et le délai demandé par le provider restent prioritaires.")}
        </p>
      )}
    </Card>
  );
}
