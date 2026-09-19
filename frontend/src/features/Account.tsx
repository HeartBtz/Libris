import { useEffect, useState } from "react";
import { api, send } from "../api";
import { formatDateTime, registerTranslations, useI18n } from "../i18n";
import type { Run, User } from "../types";
import { Badge, Button, Card, Field, Icon, Input, LoadingBlock, Page, PageHeader, useDialogs } from "../ui";
import { ApiTokens } from "./ApiTokens";

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
  "Délai enregistré ici": "Delay saved here",
  "Délai de l’environnement": "Environment delay",
  "Valeur de l’environnement : {value} s": "Environment value: {value} s",
  "Revenir au délai de l’environnement": "Go back to the environment delay",
  "Revenir au délai de l’environnement ?": "Go back to the environment delay?",
  "Le délai enregistré ici est oublié ; PROVIDER_RECOVERY_BASE_SECONDS s’applique de nouveau aux prochaines reprises.":
    "The delay saved here is forgotten; PROVIDER_RECOVERY_BASE_SECONDS applies again to the next retries.",
  Revenir: "Go back",
  "Délai de l’environnement rétabli.": "Environment delay restored.",
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
        <ApiTokens run={run} />
      </div>
    </Page>
  );
}

type RecoveryView = { retry_seconds: number; default_seconds: number; saved: boolean };

export function RecoverySettings({ run }: { run: Run }) {
  const { t } = useI18n();
  const { confirm } = useDialogs();
  const [view, setView] = useState<RecoveryView | null>(null);
  const [delay, setDelay] = useState(60);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const load = (next: RecoveryView) => {
    setView(next);
    setDelay(next.retry_seconds);
  };
  useEffect(() => {
    void run.background(async () => {
      load(await api<RecoveryView>("/settings/recovery"));
    });
  }, [run]);
  async function reset() {
    const accepted = await confirm({
      title: t("Revenir au délai de l’environnement ?"),
      message: t("Le délai enregistré ici est oublié ; PROVIDER_RECOVERY_BASE_SECONDS s’applique de nouveau aux prochaines reprises."),
      confirmLabel: t("Revenir"),
    });
    if (!accepted) return;
    setBusy(true);
    setStatus("");
    await run(async () => {
      load(await api<RecoveryView>("/settings/recovery", { method: "DELETE" }));
      setStatus(t("Délai de l’environnement rétabli."));
    }).finally(() => setBusy(false));
  }
  const ready = view !== null;
  return (
    <Card
      className="settings-card"
      title={t("Reprise automatique")}
      description={t(
        "Reprise depuis le checkpoint après une panne réseau, un timeout ou une erreur temporaire. Les pauses manuelles et erreurs d’authentification restent à traiter.",
      )}
      actions={
        view && (
          <Badge tone={view.saved ? "accent" : "neutral"}>
            {view.saved ? t("Délai enregistré ici") : t("Délai de l’environnement")}
          </Badge>
        )
      }
    >
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          setStatus("");
          void run(async () => {
            load(await send<RecoveryView>("/settings/recovery", { retry_seconds: delay }, "PUT"));
            setStatus(t("Délai enregistré. La capacité et le délai demandé par le provider restent prioritaires."));
          }).finally(() => setBusy(false));
        }}
      >
        <Field
          label={t("Délai de reprise (secondes)")}
          hint={view && t("Valeur de l’environnement : {value} s", { value: String(view.default_seconds) })}
        >
          <Input
            type="number"
            min={5}
            max={3600}
            required
            disabled={!ready || busy}
            value={delay}
            onChange={(e) => {
              setStatus("");
              setDelay(Number(e.target.value));
            }}
          />
        </Field>
        <div className="form-actions">
          <Button type="submit" variant="primary" disabled={!ready || busy}>
            {t("Enregistrer le délai")}
          </Button>
          <Button disabled={!ready || busy || !view?.saved} onClick={() => void reset()}>
            {t("Revenir au délai de l’environnement")}
          </Button>
        </div>
        {status && (
          <p role="status" className="form-status tone-text-success">
            {status}
          </p>
        )}
      </form>
    </Card>
  );
}
