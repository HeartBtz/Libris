import { useEffect, useState } from "react";
import { api, send } from "../api";
import { registerTranslations, useI18n } from "../i18n";
import type { ProviderSummary, Run } from "../types";
import { Badge, Button, Callout, Card, Checkbox, Field, FormGrid, Input, LoadingBlock, Switch, TextArea, useDialogs } from "../ui";
import { ProviderChain } from "./ProviderChain";

registerTranslations({
  "Pilote automatique de l’installation": "Installation autopilot",
  "Valeurs appliquées aux livres qui n’ont pas fait leur propre choix dans leurs réglages. Sans valeur enregistrée ici, les variables d’environnement AUTOPILOT_* s’appliquent.":
    "Values applied to the books that have not made their own choice in their settings. Without a value saved here, the AUTOPILOT_* environment variables apply.",
  "Valeurs enregistrées ici": "Values saved here",
  "Valeurs de l’environnement": "Environment values",
  "Lancer les livres en pilote automatique": "Run books on autopilot",
  "Un livre lancé va de sa source au résultat sans intervention. Désactivé, les décisions attendent une personne.":
    "A launched book goes from its source to the result with no intervention. Off, decisions wait for a person.",
  "Tours de convergence au plus": "Convergence rounds at most",
  "Reprise, revue finale et arbitrage de l’IA ; les points restants sont ensuite réglés automatiquement.":
    "Recovery, final review and AI arbitration; the remaining points are then settled automatically.",
  "Attentes d’une panne au plus": "Waits for an outage at most",
  "Attente d’une panne (minutes)": "Waiting for an outage (minutes)",
  "Au-delà, le fournisseur de secours suivant prend le relais.": "Beyond that, the next fallback provider takes over.",
  "Essayés dans cet ordre, après ceux du livre, quand son fournisseur est en panne ou refuse ses identifiants.":
    "Tried in this order, after the book's own, when its provider is down or refuses its credentials.",
  "Aucun fournisseur de secours : un livre dont le fournisseur ne répond plus échoue avec la raison.":
    "No fallback provider: a book whose provider no longer answers fails with the reason.",
  "Introuvables parmi les providers (ignorés) : {names}": "Not found among the providers (skipped): {names}",
  "Seuils des décisions automatiques": "Thresholds of the automatic decisions",
  "Confiance minimale d’un terme de glossaire": "Minimum confidence of a glossary term",
  "Confiance minimale d’un lien d’identité de série": "Minimum confidence of a series identity link",
  "Couverture minimale d’une mise à jour de la Book Bible": "Minimum coverage of a Book Bible update",
  "Couverture minimale d’un contexte de chapitre périmé": "Minimum coverage of an outdated chapter context",
  "Entre 0 et 1. En dessous, la proposition est refusée ou laissée telle quelle, et la décision est consignée.":
    "Between 0 and 1. Below it, the proposal is rejected or left as it is, and the decision is logged.",
  "Valeur par défaut : {value}": "Default value: {value}",
  "Enregistrer le pilote automatique": "Save the autopilot",
  "Revenir aux valeurs de l’environnement": "Go back to the environment values",
  "Revenir aux valeurs de l’environnement ?": "Go back to the environment values?",
  "Les valeurs enregistrées ici sont oubliées ; les variables d’environnement s’appliquent de nouveau aux prochaines décisions.":
    "The values saved here are forgotten; the environment variables apply again to the next decisions.",
  "Revenir": "Go back",
  "Réglages enregistrés. Ils s’appliquent aux prochaines décisions, sans redémarrage.":
    "Settings saved. They apply to the next decisions, without a restart.",
  "Valeurs de l’environnement rétablies.": "Environment values restored.",
  "Webhooks des requêtes d’API": "Webhooks of API requests",
  "Une requête qui nomme un callback_url prévient ce serveur à sa fin. Sans valeur enregistrée ici, les variables API_WEBHOOK_* s’appliquent.":
    "A request naming a callback_url notifies that server when it ends. Without a value saved here, the API_WEBHOOK_* variables apply.",
  "Hôtes autorisés": "Allowed hosts",
  "Un par ligne ; *.example.org autorise ses sous-domaines. Vide : webhooks refusés.":
    "One per line; *.example.org allows its subdomains. Empty: webhooks refused.",
  "Réseaux privés autorisés": "Allowed private networks",
  "Notation CIDR, un par ligne (10.0.0.0/8). Les adresses privées sont refusées sinon.":
    "CIDR notation, one per line (10.0.0.0/8). Private addresses are refused otherwise.",
  "Tentatives au plus": "Attempts at most",
  "Délai d’un appel (secondes)": "Timeout of a call (seconds)",
  "Secret de signature global": "Global signing secret",
  "Enregistré ici": "Saved here",
  "Défini par API_WEBHOOK_SECRET": "Set by API_WEBHOOK_SECRET",
  Aucun: "None",
  "Seuls les jetons qui ont leur propre secret peuvent recevoir des webhooks.":
    "Only the tokens with their own secret can receive webhooks.",
  "Nouveau secret global": "New global secret",
  "32 caractères au moins. Vide : le secret actuel est conservé. Il n’est jamais réaffiché : copiez-le avant d’enregistrer.":
    "32 characters at least. Empty: the current secret is kept. It is never shown again: copy it before saving.",
  Générer: "Generate",
  "Oublier le secret enregistré ici": "Forget the secret saved here",
  "Le secret d’un jeton, quand il en a un, signe toujours ses propres requêtes.":
    "A token's own secret, when it has one, always signs its own requests.",
  "Enregistrer les webhooks": "Save the webhooks",
  "Ce serveur ne permet pas encore de régler le pilote automatique depuis l’interface.":
    "This server does not let the interface set the autopilot yet.",
});

interface AutopilotValues {
  enabled: boolean;
  max_rounds: number;
  fallback_providers: string[];
  outage_max_retries: number;
  outage_max_wait_seconds: number;
  glossary_min_confidence: number;
  identity_min_confidence: number;
  bible_min_coverage: number;
  stale_min_coverage: number;
}
/** `GET /api/settings/autopilot`. */
export interface AutopilotAdmin {
  values: AutopilotValues;
  defaults: AutopilotValues;
  saved: boolean;
  fallback_provider_ids: string[];
}
interface WebhookValues {
  hosts: string[];
  private_networks: string[];
  max_attempts: number;
  timeout_seconds: number;
}
/** `GET /api/settings/webhooks`: the secret itself is never sent back. */
export interface WebhookAdmin {
  values: WebhookValues;
  defaults: WebhookValues;
  saved: boolean;
  secret: { configured: boolean; source: "saved" | "environment" | "none" };
}

const THRESHOLDS: [keyof AutopilotValues, string][] = [
  ["glossary_min_confidence", "Confiance minimale d’un terme de glossaire"],
  ["identity_min_confidence", "Confiance minimale d’un lien d’identité de série"],
  ["bible_min_coverage", "Couverture minimale d’une mise à jour de la Book Bible"],
  ["stale_min_coverage", "Couverture minimale d’un contexte de chapitre périmé"],
];

const lines = (value: string) =>
  value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);

const isSettings = <T,>(value: unknown): value is T =>
  !!value && typeof value === "object" && !Array.isArray(value) && "values" in value && "defaults" in value;

function SourceBadge({ saved }: { saved: boolean }) {
  const { t } = useI18n();
  return <Badge tone={saved ? "accent" : "neutral"}>{saved ? t("Valeurs enregistrées ici") : t("Valeurs de l’environnement")}</Badge>;
}

/** The installation's autopilot: default switch, round and outage limits, fallback chain, thresholds. */
export function AutopilotSettings({ run }: { run: Run }) {
  const { t } = useI18n();
  const { confirm } = useDialogs();
  const [view, setView] = useState<AutopilotAdmin | null>(null);
  const [draft, setDraft] = useState<AutopilotValues | null>(null);
  const [chain, setChain] = useState<string[]>([]);
  const [providers, setProviders] = useState<ProviderSummary[]>([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [unsupported, setUnsupported] = useState(false);
  const load = (next: AutopilotAdmin) => {
    setView(next);
    setDraft(next.values);
    setChain(next.fallback_provider_ids);
  };
  useEffect(() => {
    void run.background(async () => {
      const [value, list] = await Promise.all([
        api<unknown>("/settings/autopilot").catch(() => null),
        api<ProviderSummary[]>("/providers"),
      ]);
      setProviders(list);
      if (isSettings<AutopilotAdmin>(value)) load(value);
      else setUnsupported(true);
    });
  }, [run]);
  if (unsupported)
    return <Callout tone="info">{t("Ce serveur ne permet pas encore de régler le pilote automatique depuis l’interface.")}</Callout>;
  if (!view || !draft) return <LoadingBlock label={t("Chargement…")} />;
  const set = (change: Partial<AutopilotValues>) => {
    setStatus("");
    setDraft({ ...draft, ...change });
  };
  const numeric = (name: keyof AutopilotValues, value: string) => set({ [name]: value === "" ? 0 : Number(value) });
  // Names of the environment variable that match no provider: shown, since the chain skips them.
  const missing = draft.fallback_providers.filter(
    (token) => !providers.some((provider) => provider.id === token || provider.name === token),
  );
  async function save() {
    setBusy(true);
    await run(async () => {
      load(await send<AutopilotAdmin>("/settings/autopilot", { ...draft, fallback_providers: chain }, "PUT"));
      setStatus(t("Réglages enregistrés. Ils s’appliquent aux prochaines décisions, sans redémarrage."));
    }).finally(() => setBusy(false));
  }
  async function reset() {
    const accepted = await confirm({
      title: t("Revenir aux valeurs de l’environnement ?"),
      message: t(
        "Les valeurs enregistrées ici sont oubliées ; les variables d’environnement s’appliquent de nouveau aux prochaines décisions.",
      ),
      confirmLabel: t("Revenir"),
    });
    if (!accepted) return;
    setBusy(true);
    await run(async () => {
      load(await api<AutopilotAdmin>("/settings/autopilot", { method: "DELETE" }));
      setStatus(t("Valeurs de l’environnement rétablies."));
    }).finally(() => setBusy(false));
  }
  return (
    <Card
      className="settings-card"
      title={t("Pilote automatique de l’installation")}
      description={t(
        "Valeurs appliquées aux livres qui n’ont pas fait leur propre choix dans leurs réglages. Sans valeur enregistrée ici, les variables d’environnement AUTOPILOT_* s’appliquent.",
      )}
      actions={<SourceBadge saved={view.saved} />}
    >
      <form
        className="stack"
        aria-label={t("Pilote automatique de l’installation")}
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <Switch
          label={t("Lancer les livres en pilote automatique")}
          description={t(
            "Un livre lancé va de sa source au résultat sans intervention. Désactivé, les décisions attendent une personne.",
          )}
          checked={draft.enabled}
          disabled={busy}
          onChange={(event) => set({ enabled: event.target.checked })}
        />
        <FormGrid columns={3}>
          <Field
            label={t("Tours de convergence au plus")}
            hint={t("Reprise, revue finale et arbitrage de l’IA ; les points restants sont ensuite réglés automatiquement.")}
          >
            <Input
              type="number"
              min={1}
              max={10}
              required
              inputMode="numeric"
              disabled={busy}
              value={draft.max_rounds}
              onChange={(event) => numeric("max_rounds", event.target.value)}
            />
          </Field>
          <Field label={t("Attentes d’une panne au plus")} hint={t("Au-delà, le fournisseur de secours suivant prend le relais.")}>
            <Input
              type="number"
              min={1}
              max={100}
              required
              inputMode="numeric"
              disabled={busy}
              value={draft.outage_max_retries}
              onChange={(event) => numeric("outage_max_retries", event.target.value)}
            />
          </Field>
          <Field label={t("Attente d’une panne (minutes)")}>
            <Input
              type="number"
              min={0}
              max={7 * 24 * 60}
              required
              inputMode="numeric"
              disabled={busy}
              value={Math.round(draft.outage_max_wait_seconds / 60)}
              onChange={(event) => set({ outage_max_wait_seconds: Number(event.target.value || 0) * 60 })}
            />
          </Field>
        </FormGrid>
        <ProviderChain
          value={chain}
          onChange={(next) => {
            setStatus("");
            setChain(next);
          }}
          providers={providers}
          max={20}
          disabled={busy}
          hint={t("Essayés dans cet ordre, après ceux du livre, quand son fournisseur est en panne ou refuse ses identifiants.")}
          empty={t("Aucun fournisseur de secours : un livre dont le fournisseur ne répond plus échoue avec la raison.")}
        />
        {!view.saved && missing.length > 0 && (
          <Callout tone="warning">{t("Introuvables parmi les providers (ignorés) : {names}", { names: missing.join(", ") })}</Callout>
        )}
        <details className="disclosure">
          <summary>{t("Seuils des décisions automatiques")}</summary>
          <p className="field-hint">
            {t("Entre 0 et 1. En dessous, la proposition est refusée ou laissée telle quelle, et la décision est consignée.")}
          </p>
          <FormGrid columns={2}>
            {THRESHOLDS.map(([name, label]) => (
              <Field key={name} label={t(label)} hint={t("Valeur par défaut : {value}", { value: String(view.defaults[name]) })}>
                <Input
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  required
                  inputMode="decimal"
                  disabled={busy}
                  value={draft[name] as number}
                  onChange={(event) => numeric(name, event.target.value)}
                />
              </Field>
            ))}
          </FormGrid>
        </details>
        <div className="form-actions">
          <Button type="submit" variant="primary" loading={busy}>
            {t("Enregistrer le pilote automatique")}
          </Button>
          <Button disabled={busy || !view.saved} onClick={() => void reset()}>
            {t("Revenir aux valeurs de l’environnement")}
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

function randomSecret() {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** Hosts and networks a callback_url may name, attempts, timeout and the global signing secret. */
export function WebhookSettings({ run }: { run: Run }) {
  const { t } = useI18n();
  const { confirm } = useDialogs();
  const [view, setView] = useState<WebhookAdmin | null>(null);
  const [hosts, setHosts] = useState("");
  const [networks, setNetworks] = useState("");
  const [attempts, setAttempts] = useState(6);
  const [timeout, setTimeoutSeconds] = useState(10);
  const [secret, setSecret] = useState("");
  const [clearSecret, setClearSecret] = useState(false);
  const [unsupported, setUnsupported] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const load = (next: WebhookAdmin) => {
    setView(next);
    setHosts(next.values.hosts.join("\n"));
    setNetworks(next.values.private_networks.join("\n"));
    setAttempts(next.values.max_attempts);
    setTimeoutSeconds(next.values.timeout_seconds);
    setSecret("");
    setClearSecret(false);
  };
  useEffect(() => {
    void run.background(async () => {
      // Servers before 0.6 have no such settings: the card is left out rather than broken.
      const value = await api<unknown>("/settings/webhooks").catch(() => null);
      if (isSettings<WebhookAdmin>(value)) load(value);
      else setUnsupported(true);
    });
  }, [run]);
  if (unsupported) return null;
  if (!view) return <LoadingBlock label={t("Chargement…")} />;
  const touched = () => setStatus("");
  async function save() {
    setBusy(true);
    await run(async () => {
      load(
        await send<WebhookAdmin>(
          "/settings/webhooks",
          {
            hosts: lines(hosts),
            private_networks: lines(networks),
            max_attempts: attempts,
            timeout_seconds: timeout,
            ...(secret ? { secret } : {}),
            clear_secret: clearSecret,
          },
          "PUT",
        ),
      );
      setStatus(t("Réglages enregistrés. Ils s’appliquent aux prochaines décisions, sans redémarrage."));
    }).finally(() => setBusy(false));
  }
  async function reset() {
    const accepted = await confirm({
      title: t("Revenir aux valeurs de l’environnement ?"),
      message: t(
        "Les valeurs enregistrées ici sont oubliées ; les variables d’environnement s’appliquent de nouveau aux prochaines décisions.",
      ),
      confirmLabel: t("Revenir"),
    });
    if (!accepted) return;
    setBusy(true);
    await run(async () => {
      load(await api<WebhookAdmin>("/settings/webhooks", { method: "DELETE" }));
      setStatus(t("Valeurs de l’environnement rétablies."));
    }).finally(() => setBusy(false));
  }
  const source = view.secret.source;
  return (
    <Card
      className="settings-card"
      title={t("Webhooks des requêtes d’API")}
      description={t(
        "Une requête qui nomme un callback_url prévient ce serveur à sa fin. Sans valeur enregistrée ici, les variables API_WEBHOOK_* s’appliquent.",
      )}
      actions={<SourceBadge saved={view.saved || source === "saved"} />}
    >
      <form
        className="stack"
        aria-label={t("Webhooks des requêtes d’API")}
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <FormGrid columns={2}>
          <Field label={t("Hôtes autorisés")} hint={t("Un par ligne ; *.example.org autorise ses sous-domaines. Vide : webhooks refusés.")}>
            <TextArea
              rows={4}
              spellCheck={false}
              placeholder={"hooks.example.org\n*.example.net"}
              disabled={busy}
              value={hosts}
              onChange={(event) => {
                touched();
                setHosts(event.target.value);
              }}
            />
          </Field>
          <Field
            label={t("Réseaux privés autorisés")}
            hint={t("Notation CIDR, un par ligne (10.0.0.0/8). Les adresses privées sont refusées sinon.")}
          >
            <TextArea
              rows={4}
              spellCheck={false}
              placeholder="10.0.0.0/8"
              disabled={busy}
              value={networks}
              onChange={(event) => {
                touched();
                setNetworks(event.target.value);
              }}
            />
          </Field>
          <Field label={t("Tentatives au plus")}>
            <Input
              type="number"
              min={1}
              max={20}
              required
              inputMode="numeric"
              disabled={busy}
              value={attempts}
              onChange={(event) => {
                touched();
                setAttempts(Number(event.target.value));
              }}
            />
          </Field>
          <Field label={t("Délai d’un appel (secondes)")}>
            <Input
              type="number"
              min={1}
              max={60}
              required
              inputMode="numeric"
              disabled={busy}
              value={timeout}
              onChange={(event) => {
                touched();
                setTimeoutSeconds(Number(event.target.value));
              }}
            />
          </Field>
        </FormGrid>
        <fieldset className="stack webhook-secret">
          <legend className="field-label">{t("Secret de signature global")}</legend>
          <p className="row">
            <Badge tone={source === "none" ? "warning" : "success"}>
              {source === "saved" ? t("Enregistré ici") : source === "environment" ? t("Défini par API_WEBHOOK_SECRET") : t("Aucun")}
            </Badge>
            {source === "none" && (
              <span className="field-hint">{t("Seuls les jetons qui ont leur propre secret peuvent recevoir des webhooks.")}</span>
            )}
          </p>
          <div className="inline-form">
            <Field
              label={t("Nouveau secret global")}
              hint={t(
                "32 caractères au moins. Vide : le secret actuel est conservé. Il n’est jamais réaffiché : copiez-le avant d’enregistrer.",
              )}
            >
              <Input
                autoComplete="off"
                spellCheck={false}
                minLength={32}
                maxLength={512}
                disabled={busy || clearSecret}
                value={secret}
                onChange={(event) => {
                  touched();
                  setSecret(event.target.value);
                }}
              />
            </Field>
            <Button
              disabled={busy || clearSecret}
              onClick={() => {
                touched();
                setSecret(randomSecret());
              }}
            >
              {t("Générer")}
            </Button>
          </div>
          {source === "saved" && (
            <Checkbox
              label={t("Oublier le secret enregistré ici")}
              checked={clearSecret}
              disabled={busy}
              onChange={(event) => {
                touched();
                setClearSecret(event.target.checked);
                if (event.target.checked) setSecret("");
              }}
            />
          )}
          <p className="field-hint">{t("Le secret d’un jeton, quand il en a un, signe toujours ses propres requêtes.")}</p>
        </fieldset>
        <div className="form-actions">
          <Button type="submit" variant="primary" loading={busy}>
            {t("Enregistrer les webhooks")}
          </Button>
          <Button disabled={busy || !(view.saved || source === "saved")} onClick={() => void reset()}>
            {t("Revenir aux valeurs de l’environnement")}
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
