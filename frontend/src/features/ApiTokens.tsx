import { useEffect, useState } from "react";
import { api, send } from "../api";
import { formatDateTime, registerTranslations, useI18n } from "../i18n";
import type { Run } from "../types";
import { Badge, Button, Callout, Card, Checkbox, Field, FormGrid, Input, LoadingBlock, Select, useDialogs } from "../ui";
import type { Tone } from "../ui";

registerTranslations({
  "Jetons d’API": "API tokens",
  "Un jeton donne accès à l’API d’automatisation (/api/v1) au nom de votre compte, limité aux permissions choisies. Il ne permet pas de se connecter à l’interface.":
    "A token opens the automation API (/api/v1) on behalf of your account, limited to the permissions you choose. It cannot be used to sign in to the interface.",
  "Aucun jeton.": "No tokens.",
  "Chargement des jetons…": "Loading tokens…",
  "Créer un jeton": "Create a token",
  "Nom du jeton": "Token name",
  "Permissions": "Permissions",
  "Expiration": "Expiration",
  "Jamais": "Never",
  "{days} jours": "{days} days",
  "Créé le {date}": "Created on {date}",
  "Expire le {date}": "Expires on {date}",
  "Sans expiration": "No expiration",
  "Utilisé le {date}": "Used on {date}",
  "Jamais utilisé": "Never used",
  "Actif": "Active",
  "Expiré": "Expired",
  "Révoqué": "Revoked",
  "Révoquer": "Revoke",
  "Révoquer {name}": "Revoke {name}",
  "Révoquer ce jeton ?": "Revoke this token?",
  "Les clients qui l’utilisent recevront une erreur 401. Cette action est définitive.":
    "Clients using it will receive a 401 error. This cannot be undone.",
  "Choisissez au moins une permission.": "Choose at least one permission.",
  "Jeton créé : copiez-le maintenant": "Token created: copy it now",
  "Ce secret ne sera plus jamais affiché. Conservez-le dans le gestionnaire de secrets de votre client.":
    "This secret will never be shown again. Keep it in your client's secret manager.",
  "Secret du jeton": "Token secret",
  "Copier": "Copy",
  "Copié": "Copied",
  "Copie impossible : sélectionnez le texte et copiez-le.": "Copy failed: select the text and copy it.",
  "J’ai copié le jeton": "I have copied the token",
  "Lire les séries": "Read series",
  "Envoyer du contenu": "Send content",
  "Lancer le pipeline": "Start the pipeline",
  "Suivre les travaux": "Follow jobs",
  "Piloter les travaux (pause, reprise, annulation)": "Control jobs (pause, resume, cancel)",
  "Lire les résultats": "Read results",
  "Documentation de l’API": "API documentation",
  "Référence complète dans le dépôt : {path} (exemples curl, codes d’erreur, limites, idempotence).":
    "Full reference in the repository: {path} (curl examples, error codes, limits, idempotency).",
  "Envoyer un EPUB, des chapitres TXT ou une requête JSON ; le pipeline complet démarre seul":
    "Send an EPUB, TXT chapters or a JSON request; the whole pipeline starts on its own",
  "Suivre l’état, l’étape, la progression et le rapport (attente longue avec ?wait=)":
    "Follow status, stage, progress and report (long poll with ?wait=)",
  "Mettre en pause, reprendre ou annuler": "Pause, resume or cancel",
  "Récupérer le résultat (EPUB traduit, json, txt, txt-zip)": "Fetch the result (translated EPUB, json, txt, txt-zip)",
  "Signer les webhooks avec un secret propre à ce jeton": "Sign webhooks with a secret of this token",
  "Les requêtes qui nomment un callback_url préviennent votre serveur à leur fin, signées en HMAC-SHA256 (X-Libris-Signature). Le secret est affiché une seule fois, avec le jeton.":
    "Requests naming a callback_url notify your server when they end, signed with HMAC-SHA256 (X-Libris-Signature). The secret is shown once, with the token.",
  "Secret de signature des webhooks": "Webhook signing secret",
  "Copier le secret du jeton": "Copy the token secret",
  "Copier le secret des webhooks": "Copy the webhook secret",
  "Webhooks signés": "Signed webhooks",
  "Ces secrets ne seront plus jamais affichés. Conservez-les dans le gestionnaire de secrets de votre client.":
    "These secrets will never be shown again. Keep them in your client's secret manager.",
  "J’ai copié les secrets": "I have copied the secrets",
  "Lister et lire les séries": "List and read series",
  "Exemple": "Example",
});

interface ApiToken {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  created_at: number;
  expires_at: number | null;
  revoked_at: number | null;
  last_used_at: number | null;
  state: "active" | "expired" | "revoked";
  /** Whether webhooks of this token's requests are signed with its own secret (0.6). */
  webhook_secret?: boolean;
}

const SCOPES: [string, string][] = [
  ["series:read", "Lire les séries"],
  ["content:write", "Envoyer du contenu"],
  ["pipeline:start", "Lancer le pipeline"],
  ["jobs:read", "Suivre les travaux"],
  ["jobs:control", "Piloter les travaux (pause, reprise, annulation)"],
  ["results:read", "Lire les résultats"],
];
const STATES: Record<ApiToken["state"], [string, Tone]> = {
  active: ["Actif", "success"],
  expired: ["Expiré", "warning"],
  revoked: ["Révoqué", "neutral"],
};
const ENDPOINTS: [string, string][] = [
  ["POST /api/v1/translation-requests", "Envoyer un EPUB, des chapitres TXT ou une requête JSON ; le pipeline complet démarre seul"],
  ["GET /api/v1/translation-requests/{id}?wait=60", "Suivre l’état, l’étape, la progression et le rapport (attente longue avec ?wait=)"],
  ["POST /api/v1/translation-requests/{id}/pause|resume|cancel", "Mettre en pause, reprendre ou annuler"],
  [
    "GET /api/v1/translation-requests/{id}/result?format=epub|json|txt|txt-zip",
    "Récupérer le résultat (EPUB traduit, json, txt, txt-zip)",
  ],
  ["GET /api/v1/series, /api/v1/series/{id}", "Lister et lire les séries"],
];
const EXAMPLE = `curl -X POST "$LIBRIS_URL/api/v1/translation-requests" \\
  -H "Authorization: Bearer $LIBRIS_TOKEN" \\
  -H "Idempotency-Key: volume-12-run-1" \\
  -F "file=@volume-12.epub;type=application/epub+zip" \\
  -F target_language=fr

curl "$LIBRIS_URL/api/v1/translation-requests/$ID/result?wait=60" \\
  -H "Authorization: Bearer $LIBRIS_TOKEN" -o volume-12.fr.epub`;

export function ApiTokens({ run }: { run: Run }) {
  const { t } = useI18n();
  const { confirm } = useDialogs();
  const [tokens, setTokens] = useState<ApiToken[] | null>(null);
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["jobs:read", "results:read"]);
  const [days, setDays] = useState("90");
  const [secret, setSecret] = useState("");
  const [signing, setSigning] = useState("");
  const [withWebhookSecret, setWithWebhookSecret] = useState(false);
  const [copied, setCopied] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    void run.background(async () => setTokens(await api("/tokens")));
  }, [run]);
  const revoke = async (token: ApiToken) => {
    const accepted = await confirm({
      title: t("Révoquer ce jeton ?"),
      message: (
        <>
          <strong>
            {token.name} · <code>{token.prefix}…</code>
          </strong>
          <br />
          {t("Les clients qui l’utilisent recevront une erreur 401. Cette action est définitive.")}
        </>
      ),
      confirmLabel: t("Révoquer"),
      tone: "danger",
    });
    if (!accepted) return;
    setBusy(true);
    await run(async () => {
      await api(`/tokens/${token.id}`, { method: "DELETE" });
      setTokens(await api("/tokens"));
    }).finally(() => setBusy(false));
  };
  return (
    <div className="stack">
      <Card
        title={t("Jetons d’API")}
        description={t(
          "Un jeton donne accès à l’API d’automatisation (/api/v1) au nom de votre compte, limité aux permissions choisies. Il ne permet pas de se connecter à l’interface.",
        )}
        padded={false}
      >
        {tokens === null ? (
          <div className="card-inset">
            <LoadingBlock label={t("Chargement des jetons…")} lines={2} />
          </div>
        ) : tokens.length === 0 ? (
          <p className="card-inset subtle">{t("Aucun jeton.")}</p>
        ) : (
          <ul className="token-list" aria-label={t("Jetons d’API")}>
            {tokens.map((token) => {
              const [label, tone] = STATES[token.state];
              return (
                <li key={token.id}>
                  <div className="grow token-main">
                    <strong>{token.name}</strong>
                    <code>{token.prefix}…</code>
                    <span className="token-scopes">
                      {token.scopes.map((scope) => (
                        <Badge key={scope}>{scope}</Badge>
                      ))}
                      {token.webhook_secret && <Badge tone="info">{t("Webhooks signés")}</Badge>}
                    </span>
                    <small className="subtle">
                      {[
                        t("Créé le {date}", { date: formatDateTime(token.created_at) }),
                        token.expires_at
                          ? t("Expire le {date}", { date: formatDateTime(token.expires_at) })
                          : t("Sans expiration"),
                        token.last_used_at
                          ? t("Utilisé le {date}", { date: formatDateTime(token.last_used_at) })
                          : t("Jamais utilisé"),
                      ].join(" · ")}
                    </small>
                  </div>
                  <Badge tone={tone} dot>
                    {t(label)}
                  </Badge>
                  {token.state !== "revoked" && (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      aria-label={t("Révoquer {name}", { name: token.name })}
                      onClick={() => void revoke(token)}
                    >
                      {t("Révoquer")}
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>
      {secret && (
        <Callout
          tone="warning"
          role="status"
          title={t("Jeton créé : copiez-le maintenant")}
          actions={
            <Button
              onClick={() => {
                setSecret("");
                setSigning("");
              }}
              variant="primary"
            >
              {signing ? t("J’ai copié les secrets") : t("J’ai copié le jeton")}
            </Button>
          }
        >
          <p>
            {signing
              ? t("Ces secrets ne seront plus jamais affichés. Conservez-les dans le gestionnaire de secrets de votre client.")
              : t("Ce secret ne sera plus jamais affiché. Conservez-le dans le gestionnaire de secrets de votre client.")}
          </p>
          {[
            [t("Secret du jeton"), secret, t("Copier le secret du jeton")],
            ...(signing ? [[t("Secret de signature des webhooks"), signing, t("Copier le secret des webhooks")]] : []),
          ].map(([label, value, copyLabel]) => (
            <div className="inline-form" key={label}>
              <Field label={label}>
                <Input readOnly value={value} spellCheck={false} onFocus={(event) => event.target.select()} />
              </Field>
              <Button
                aria-label={copyLabel}
                onClick={() => {
                  navigator.clipboard
                    .writeText(value)
                    .then(() => setCopied(t("Copié")))
                    .catch(() => setCopied(t("Copie impossible : sélectionnez le texte et copiez-le.")));
                }}
              >
                {t("Copier")}
              </Button>
            </div>
          ))}
          {copied && <p className="form-status">{copied}</p>}
        </Callout>
      )}
      <Card title={t("Créer un jeton")}>
        <form
          className="stack"
          onSubmit={(event) => {
            event.preventDefault();
            if (!scopes.length) {
              setError(t("Choisissez au moins une permission."));
              return;
            }
            setError("");
            setBusy(true);
            void run(async () => {
              const created = await send<Omit<ApiToken, "webhook_secret"> & { token: string; webhook_secret?: string | boolean }>(
                "/tokens",
                {
                  name: name.trim(),
                  scopes,
                  expires_in_days: days ? Number(days) : null,
                  // Only sent when asked: servers before 0.6 refuse unknown fields.
                  ...(withWebhookSecret ? { webhook_secret: true } : {}),
                },
              );
              setSecret(created.token);
              setSigning(typeof created.webhook_secret === "string" ? created.webhook_secret : "");
              setWithWebhookSecret(false);
              setCopied("");
              setName("");
              setTokens(await api("/tokens"));
            }).finally(() => setBusy(false));
          }}
        >
          <FormGrid>
            <Field label={t("Nom du jeton")}>
              <Input required maxLength={100} value={name} onChange={(event) => setName(event.target.value)} />
            </Field>
            <Field label={t("Expiration")}>
              <Select value={days} onChange={(event) => setDays(event.target.value)}>
                {["30", "90", "365"].map((value) => (
                  <option key={value} value={value}>
                    {t("{days} jours", { days: value })}
                  </option>
                ))}
                <option value="">{t("Jamais")}</option>
              </Select>
            </Field>
          </FormGrid>
          <fieldset className="token-scope-picker">
            <legend>{t("Permissions")}</legend>
            {SCOPES.map(([scope, label]) => (
              <Checkbox
                key={scope}
                label={t(label)}
                description={scope}
                checked={scopes.includes(scope)}
                onChange={(event) =>
                  setScopes((current) =>
                    event.target.checked ? [...current, scope] : current.filter((item) => item !== scope),
                  )
                }
              />
            ))}
          </fieldset>
          <Checkbox
            label={t("Signer les webhooks avec un secret propre à ce jeton")}
            description={t(
              "Les requêtes qui nomment un callback_url préviennent votre serveur à leur fin, signées en HMAC-SHA256 (X-Libris-Signature). Le secret est affiché une seule fois, avec le jeton.",
            )}
            checked={withWebhookSecret}
            onChange={(event) => setWithWebhookSecret(event.target.checked)}
          />
          {error && (
            <p role="alert" className="form-status tone-text-danger">
              {error}
            </p>
          )}
          <div>
            <Button type="submit" variant="primary" icon="plus" disabled={busy}>
              {t("Créer un jeton")}
            </Button>
          </div>
        </form>
      </Card>
      <Card
        title={t("Documentation de l’API")}
        description={t("Référence complète dans le dépôt : {path} (exemples curl, codes d’erreur, limites, idempotence).", {
          path: "docs/api.md",
        })}
      >
        <dl className="endpoint-list">
          {ENDPOINTS.map(([route, label]) => (
            <div key={route}>
              <dt>
                <code>{route}</code>
              </dt>
              <dd className="subtle">{t(label)}</dd>
            </div>
          ))}
        </dl>
        <p className="field-label">{t("Exemple")}</p>
        <pre className="code-sample">{EXAMPLE}</pre>
      </Card>
    </div>
  );
}
