import { useEffect, useState } from "react";
import { api, download, send } from "../api";
import { registerTranslations, useI18n } from "../i18n";
import type { Provider, Run, User } from "../types";
import {
  Badge,
  Button,
  Callout,
  Card,
  Checkbox,
  EmptyState,
  Field,
  FormGrid,
  Input,
  LoadingBlock,
  Page,
  PageHeader,
  Select,
  Switch,
  TabPanel,
  Tabs,
  TextArea,
  cx,
  useDialogs,
} from "../ui";
import { CodexConnection } from "./CodexConnection";
import { RecoverySettings } from "./Account";

registerTranslations({
  "Modèle local": "Local model",
  Administration: "Administration",
  Paramètres: "Settings",
  "Sections des paramètres": "Settings sections",
  "Providers LLM": "LLM providers",
  "Mémoire · OpenViking": "Memory · OpenViking",
  Utilisateurs: "Users",
  "Reprise automatique": "Automatic recovery",
  "{message} — {count} résultat. Le test ne modifie pas la configuration.":
    "{message} — {count} result. The test does not change the configuration.",
  "{message} — {count} résultats. Le test ne modifie pas la configuration.":
    "{message} — {count} results. The test does not change the configuration.",
  "Configuration enregistrée. Prise en compte par le worker aux prochaines recherches.":
    "Configuration saved. It will be used by the worker for subsequent searches.",
  "Recherche web · SearXNG": "Web search · SearXNG",
  "Recherche terminologique facultative pendant la revue finale. Les termes recherchés sont transmis à votre instance et à ses moteurs amont.":
    "Optional terminology search during final review. Search terms are sent to your instance and its upstream engines.",
  "URL de l’instance SearXNG": "SearXNG instance URL",
  "Activer la recherche pendant la revue finale": "Enable search during final review",
  "Le format JSON doit être autorisé dans search.formats sur SearXNG. Deux recherches maximum par passage. Aucune recherche lorsque cette option est désactivée.":
    "JSON format must be enabled in SearXNG search.formats. A maximum of two searches per passage. No search is made when this option is disabled.",
  Enregistrer: "Save",
  "Tester la connexion": "Test connection",
  "Provider enregistré.": "Provider saved.",
  "Nouveau provider": "New provider",
  Providers: "Providers",
  "Connexion au modèle": "Model connection",
  "Les clés sont chiffrées côté serveur et ne sont jamais renvoyées au navigateur. L’URL doit être accessible depuis le conteneur.":
    "Keys are encrypted server-side and are never returned to the browser. The URL must be reachable from the container.",
  "Connexion / protocole": "Connection / protocol",
  "Codex / OpenAI · clé API (Responses)": "Codex / OpenAI · API key (Responses)",
  "Codex · compte ChatGPT": "Codex · ChatGPT account",
  "OpenAI-compatible · Chat Completions": "OpenAI-compatible · Chat Completions",
  Nom: "Name",
  "Clé API": "API key",
  "Enregistrée — laisser vide pour conserver": "Saved — leave empty to keep",
  "Ressaisissez la clé API": "Enter the API key again",
  "La clé enregistrée n’est jamais envoyée vers une nouvelle adresse ou un autre type de connexion : saisissez-la de nouveau pour enregistrer ce changement.":
    "The saved key is never sent to a new address or another connection type: enter it again to save this change.",
  Facultative: "Optional",
  Requise: "Required",
  "Anthropic · Claude (clé API)": "Anthropic · Claude (API key)",
  "OpenAI · Chat Completions (clé API)": "OpenAI · Chat Completions (API key)",
  "L’inférence utilise l’API Anthropic, facturée à l’usage. Température et Top P ne sont pas envoyés : les modèles Claude actuels les refusent.":
    "Inference uses the Anthropic API, billed per use. Temperature and Top P are not sent: current Claude models reject them.",
  Modèle: "Model",
  "Modèle et limites": "Model and limits",
  "Coûts et capacité": "Costs and capacity",
  "Fenêtre de contexte": "Context window",
  "Tokens de sortie maximum": "Maximum output tokens",
  Température: "Temperature",
  "Timeout (secondes)": "Timeout (seconds)",
  "Livres simultanés": "Concurrent books",
  "Coût / million tokens entrée": "Cost / million input tokens",
  "Coût / million tokens sortie": "Cost / million output tokens",
  "La limite de livres simultanés s’applique à ce provider, analyses, traductions et relectures confondues. Chaque provider dispose de sa propre capacité indépendante.":
    "The concurrent-book limit applies to this provider across analysis, translation, and review. Each provider has its own independent capacity.",
  "L’inférence utilise OpenAI. Température et Top P ne sont pas envoyés pour ce transport.":
    "Inference uses OpenAI. Temperature and Top P are not sent for this transport.",
  "La limite de sortie est une réservation du budget de l’application ; Codex ne fournit pas de plafond de génération équivalent à max_output_tokens.":
    "The output limit reserves application budget; Codex does not provide a generation ceiling equivalent to max_output_tokens.",
  "Une clé API est facturée séparément de l’abonnement ChatGPT.": "An API key is billed separately from the ChatGPT subscription.",
  "Capacités déclarées": "Declared capabilities",
  "Paramètre limite de sortie": "Output limit parameter",
  "Tester / détecter les modèles": "Test / detect models",
  "Supprimer ce provider": "Delete this provider",
  "Supprimer le provider « {name} » ?": "Delete the provider “{name}”?",
  "Un provider encore utilisé par un livre ou par l’historique des requêtes ne peut pas être supprimé ; le serveur indiquera quoi changer.":
    "A provider still used by a book or by the request history cannot be deleted; the server will say what to change.",
  "Provider supprimé.": "Provider deleted.",
  Supprimer: "Delete",
  "Aucun provider pour l’instant.": "No provider yet.",
  "Chargement…": "Loading…",
  "Mémoire narrative · OpenViking": "Narrative memory · OpenViking",
  "Hybrid conserve la continuité SQL lorsque OpenViking est absent ou indisponible. Les ressources sont séparées par propriétaire et projet, avec contrôle temporel des événements.":
    "Hybrid preserves SQL continuity when OpenViking is absent or unavailable. Resources are separated by owner and project, with temporal control of events.",
  "URL OpenViking": "OpenViking URL",
  "Racine dédiée viking://": "Dedicated viking:// root",
  "Budget contexte": "Context budget",
  "Budget retrieval": "Retrieval budget",
  "Score minimal": "Minimum score",
  Authentification: "Authentication",
  "API key — recommandé": "API key — recommended",
  "Recherche sémantique (find)": "Semantic search (find)",
  "Recherche approfondie (search)": "Deep search (search)",
  "Les modèles VLM et embeddings se configurent sur le serveur OpenViking. L’application utilise find/search limités au projet, puis lit les souvenirs pertinents en L2. L’assemblage global non borné est évité.":
    "VLM and embedding models are configured on the OpenViking server. The application uses project-limited find/search, then reads relevant memories in L2. Unbounded global assembly is avoided.",
  Enregistrée: "Saved",
  "Configuration enregistrée.": "Configuration saved.",
  "Résultat du test": "Test result",
  "Version {version}": "Version {version}",
  initiale: "initial",
  "Contenu du prompt": "Prompt content",
  "Nouvelle version enregistrée.": "New version saved.",
  "Créer une version": "Create version",
  "Exporter les prompts": "Export prompts",
  "Utilisateurs de Libris": "Libris users",
  "Sélectionnez un compte pour le gérer.": "Select an account to manage it.",
  Utilisateur: "User",
  Rôle: "Role",
  État: "Status",
  Administrateur: "Administrator",
  "Créer un compte": "Create an account",
  "Mot de passe initial": "Initial password",
  "Créer le compte": "Create account",
  "Gérer {user}": "Manage {user}",
  Actif: "Active",
  Désactivé: "Disabled",
  "Compte actif": "Active account",
  "Enregistrer le compte": "Save account",
  "Nouveau mot de passe": "New password",
  "Réinitialiser le mot de passe": "Reset password",
  "Réinitialisation effectuée. Les sessions ont été révoquées.": "Password reset. Sessions have been revoked.",
  "Compte enregistré. Les sessions ont été révoquées.": "Account saved. Sessions have been revoked.",
  "La désactivation conserve les livres. Les changements de rôle révoquent les sessions du compte.":
    "Deactivation preserves books. Role changes revoke the account's sessions.",
  "Niveau de raisonnement": "Reasoning level",
  "Non pris en charge": "Not supported",
  "Automatique (modèle)": "Automatic (model default)",
  Minimal: "Minimal",
  Faible: "Low",
  Moyen: "Medium",
  Élevé: "High",
  "Très élevé": "Extra high",
  "Désactivé envoie explicitement « none ». Automatique laisse le modèle choisir. Non pris en charge n’envoie aucun paramètre de raisonnement.":
    "Disabled explicitly sends ‘none’. Automatic lets the model decide. Not supported sends no reasoning parameter.",
});

const initial: Provider = {
  id: "",
  kind: "openai",
  name: "",
  base_url: "http://localhost:8000/v1",
  model: "",
  context_window: 32768,
  max_output_tokens: 4096,
  temperature: 0.2,
  top_p: 0.9,
  timeout: 180,
  max_concurrency: 1,
  input_cost: 0,
  output_cost: 0,
  capabilities: {
    supports_json_schema: false,
    supports_json_object: true,
    supports_reasoning: false,
    supports_tool_calls: false,
    max_tokens_parameter: "max_tokens",
    reasoning_effort: "",
  },
};

interface MemoryConfig {
  base_url: string;
  root_uri: string;
  enable_search: boolean;
  enable_deep_search: boolean;
  context_budget: number;
  retrieval_budget: number;
  has_api_key?: boolean;
  min_score: number;
  timeout: number;
  auth_mode: string;
  account: string;
  user: string;
}
interface Prompt {
  name: string;
  content: string;
  version: number;
}

export function Settings({ run }: { run: Run }) {
  const { t } = useI18n();
  const [tab, setTab] = useState("providers");
  return (
    <Page>
      <PageHeader title={t("Paramètres")} description={t("Administration")} />
      <div className="stack">
        <Tabs
          idPrefix="settings"
          label={t("Sections des paramètres")}
          value={tab}
          onChange={setTab}
          items={[
            { id: "providers", label: t("Providers LLM") },
            { id: "memory", label: t("Mémoire · OpenViking") },
            { id: "search", label: "SearXNG" },
            { id: "prompts", label: "Prompts" },
            { id: "users", label: t("Utilisateurs") },
            { id: "recovery", label: t("Reprise automatique") },
          ]}
        />
        <TabPanel idPrefix="settings" value={tab}>
          {tab === "providers" ? (
            <ProviderSettings run={run} />
          ) : tab === "memory" ? (
            <MemorySettings run={run} />
          ) : tab === "prompts" ? (
            <Prompts run={run} />
          ) : tab === "search" ? (
            <SearchSettings run={run} />
          ) : tab === "recovery" ? (
            <RecoverySettings run={run} />
          ) : (
            <Users run={run} />
          )}
        </TabPanel>
      </div>
    </Page>
  );
}

function SearchSettings({ run }: { run: Run }) {
  const { t, tp } = useI18n();
  const [value, setValue] = useState({ base_url: "", enabled: false });
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  useEffect(() => {
    void run.background(async () => {
      setValue(await api("/settings/searxng"));
      setReady(true);
    });
  }, [run]);
  async function action(test: boolean) {
    setBusy(true);
    setResult("");
    await run(async () => {
      try {
        if (test) {
          const response = await send<{ message: string; results: number }>("/settings/searxng/test", value);
          setResult(
            tp(
              response.results,
              "{message} — {count} résultat. Le test ne modifie pas la configuration.",
              "{message} — {count} résultats. Le test ne modifie pas la configuration.",
              { message: response.message },
            ),
          );
        } else {
          setValue(await send("/settings/searxng", value, "PUT"));
          setResult(t("Configuration enregistrée. Prise en compte par le worker aux prochaines recherches."));
        }
      } finally {
        setBusy(false);
      }
    });
  }
  return (
    <Card
      className="settings-card"
      title={t("Recherche web · SearXNG")}
      description={t(
        "Recherche terminologique facultative pendant la revue finale. Les termes recherchés sont transmis à votre instance et à ses moteurs amont.",
      )}
    >
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          void action(false);
        }}
      >
        <Field label={t("URL de l’instance SearXNG")}>
          <Input
            type="url"
            placeholder="https://search.example.com"
            value={value.base_url}
            disabled={!ready || busy}
            onChange={(event) => {
              setValue({ ...value, base_url: event.target.value });
              setResult("");
            }}
          />
        </Field>
        <Switch
          label={t("Activer la recherche pendant la revue finale")}
          description={t(
            "Le format JSON doit être autorisé dans search.formats sur SearXNG. Deux recherches maximum par passage. Aucune recherche lorsque cette option est désactivée.",
          )}
          checked={value.enabled}
          disabled={!ready || busy}
          onChange={(event) => {
            setValue({ ...value, enabled: event.target.checked });
            setResult("");
          }}
        />
        <div className="form-actions">
          <Button type="submit" variant="primary" disabled={!ready || busy}>
            {t("Enregistrer")}
          </Button>
          <Button disabled={!ready || busy || !value.base_url} onClick={() => void action(true)}>
            {t("Tester la connexion")}
          </Button>
        </div>
        {result && (
          <Callout tone="info" role="status">
            {result}
          </Callout>
        )}
      </form>
    </Card>
  );
}

function ProviderSettings({ run }: { run: Run }) {
  const { t } = useI18n();
  const { confirm } = useDialogs();
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const blank = () => ({ ...initial, name: t("Modèle local") });
  const [value, setValue] = useState<Provider>(blank);
  const [original, setOriginal] = useState<Provider | null>(null);
  const [key, setKey] = useState("");
  const [result, setResult] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const native = value.kind === "anthropic" || value.kind === "openai_direct";
  // The server refuses to reuse a stored key for another address or connection type.
  const endpointChanged =
    !!value.id && !!original?.has_api_key && (value.base_url !== original.base_url || value.kind !== original.kind);
  useEffect(() => {
    void run.background(async () => setProviders(await api("/providers")));
  }, [run]);
  function field<K extends keyof Provider>(name: K, next: Provider[K]) {
    setValue({ ...value, [name]: next });
  }
  async function save() {
    const { id, has_api_key: _hidden, created_at: _created, ...body } = value;
    const saved = await send<Provider>(
      `/providers${id ? "/" + id : ""}`,
      { ...body, model: body.model || (body.kind === "codex_chatgpt" ? "codex" : ""), api_key: key || null },
      id ? "PUT" : "POST",
    );
    setValue(saved);
    setOriginal(saved);
    setKey("");
    setProviders(await api("/providers"));
    setResult(t("Provider enregistré."));
  }
  const numeric = (
    [
      "context_window",
      "max_output_tokens",
      "temperature",
      "top_p",
      "timeout",
      "max_concurrency",
      "input_cost",
      "output_cost",
    ] as const
  ).filter((name) => value.kind === "openai" || value.kind === "openai_direct" || !["temperature", "top_p"].includes(name));
  const numberLabels: Record<(typeof numeric)[number], string> = {
    context_window: t("Fenêtre de contexte"),
    max_output_tokens: t("Tokens de sortie maximum"),
    temperature: t("Température"),
    top_p: "Top P",
    timeout: t("Timeout (secondes)"),
    max_concurrency: t("Livres simultanés"),
    input_cost: t("Coût / million tokens entrée"),
    output_cost: t("Coût / million tokens sortie"),
  };
  const numberField = (name: (typeof numeric)[number]) => (
    <Field key={name} label={numberLabels[name]}>
      <Input
        type="number"
        min={name === "max_concurrency" ? "1" : "0"}
        step={["temperature", "top_p", "input_cost", "output_cost"].includes(name) ? ".01" : "1"}
        value={value[name]}
        onChange={(e) => field(name, Number(e.target.value))}
      />
    </Field>
  );
  return (
    <div className="master-detail">
      <Card padded={false} className="master-list">
        <div className="master-list-header">
          <strong>{t("Providers")}</strong>
          <Button
            size="sm"
            icon="plus"
            onClick={() => {
              setValue(blank());
              setOriginal(null);
              setKey("");
              setModels([]);
              setResult("");
            }}
          >
            {t("Nouveau provider")}
          </Button>
        </div>
        {providers === null ? (
          <div className="card-inset">
            <LoadingBlock label={t("Chargement…")} lines={2} />
          </div>
        ) : providers.length ? (
          <ul>
            {providers.map((p) => (
              <li key={p.id}>
                <button
                  type="button"
                  className={cx("master-item", value.id === p.id && "is-active")}
                  aria-current={value.id === p.id ? "true" : undefined}
                  onClick={() => {
                    setValue(p);
                    setOriginal(p);
                    setKey("");
                    setResult("");
                  }}
                >
                  <span className="master-item-title">{p.name}</span>
                  <small>{p.model}</small>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState compact icon="sparkles" title={t("Aucun provider pour l’instant.")} />
        )}
      </Card>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          void run(save);
        }}
      >
        <Card
          title={t("Connexion au modèle")}
          description={t(
            "Les clés sont chiffrées côté serveur et ne sont jamais renvoyées au navigateur. L’URL doit être accessible depuis le conteneur.",
          )}
        >
          <div className="stack">
            <FormGrid>
              <Field label={t("Connexion / protocole")}>
                <Select
                  value={value.kind}
                  onChange={(e) => {
                    const kind = e.target.value as Provider["kind"];
                    setKey("");
                    setModels([]);
                    setResult("");
                    setValue({
                      ...value,
                      kind,
                      base_url:
                        kind === "codex_chatgpt"
                          ? ""
                          : kind === "anthropic"
                            ? "https://api.anthropic.com"
                            : kind === "openai_responses" || kind === "openai_direct"
                              ? "https://api.openai.com/v1"
                              : "http://localhost:8000/v1",
                      model: "",
                      timeout: kind === "openai" ? 180 : 600,
                      capabilities: {
                        ...value.capabilities,
                        supports_json_schema: kind !== "openai" && kind !== "anthropic",
                        supports_reasoning: kind === "openai_responses" || kind === "codex_chatgpt",
                      },
                    });
                  }}
                >
                  <option value="openai">{t("OpenAI-compatible · Chat Completions")}</option>
                  <option value="openai_responses">{t("Codex / OpenAI · clé API (Responses)")}</option>
                  <option value="codex_chatgpt">{t("Codex · compte ChatGPT")}</option>
                  <option value="anthropic">{t("Anthropic · Claude (clé API)")}</option>
                  <option value="openai_direct">{t("OpenAI · Chat Completions (clé API)")}</option>
                </Select>
              </Field>
              <Field label={t("Nom")}>
                <Input value={value.name} onChange={(e) => field("name", e.target.value)} required />
              </Field>
              {value.kind !== "codex_chatgpt" && (
                <Field label="Base URL">
                  <Input
                    value={value.base_url}
                    onChange={(e) => field("base_url", e.target.value)}
                    required
                    placeholder="http://gpu-host:8000/v1"
                  />
                </Field>
              )}
              {value.kind !== "codex_chatgpt" && (
                <Field
                  label={t("Clé API")}
                  hint={
                    endpointChanged
                      ? t(
                          "La clé enregistrée n’est jamais envoyée vers une nouvelle adresse ou un autre type de connexion : saisissez-la de nouveau pour enregistrer ce changement.",
                        )
                      : undefined
                  }
                >
                  <Input
                    type="password"
                    autoComplete="new-password"
                    value={key}
                    onChange={(e) => setKey(e.target.value)}
                    required={(native && !value.has_api_key) || endpointChanged}
                    placeholder={
                      endpointChanged
                        ? t("Ressaisissez la clé API")
                        : value.has_api_key
                        ? t("Enregistrée — laisser vide pour conserver")
                        : native
                          ? t("Requise")
                          : t("Facultative")
                    }
                  />
                </Field>
              )}
            </FormGrid>
            {value.kind === "anthropic" && (
              <Callout tone="neutral">
                {t(
                  "L’inférence utilise l’API Anthropic, facturée à l’usage. Température et Top P ne sont pas envoyés : les modèles Claude actuels les refusent.",
                )}
              </Callout>
            )}
            {(value.kind === "openai_responses" || value.kind === "codex_chatgpt") && (
              <Callout tone="neutral">
                {t("L’inférence utilise OpenAI. Température et Top P ne sont pas envoyés pour ce transport.")}{" "}
                {value.kind === "codex_chatgpt"
                  ? t(
                      "La limite de sortie est une réservation du budget de l’application ; Codex ne fournit pas de plafond de génération équivalent à max_output_tokens.",
                    )
                  : t("Une clé API est facturée séparément de l’abonnement ChatGPT.")}
              </Callout>
            )}
            {value.kind === "codex_chatgpt" && (
              <CodexConnection
                key={value.id}
                providerId={value.id}
                run={run}
                onModels={(found) => {
                  setModels(found);
                  if (!value.model || value.model === "codex") setValue({ ...value, model: found[0] || "" });
                }}
              />
            )}
          </div>
        </Card>
        <Card title={t("Modèle et limites")}>
          <FormGrid columns={3}>
            <Field label={t("Modèle")}>
              <Input
                list="models"
                value={value.model}
                onChange={(e) => field("model", e.target.value)}
                required={value.kind !== "codex_chatgpt"}
              />
              <datalist id="models">
                {models.map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
            </Field>
            <Field
              label={t("Niveau de raisonnement")}
              hint={t(
                "Désactivé envoie explicitement « none ». Automatique laisse le modèle choisir. Non pris en charge n’envoie aucun paramètre de raisonnement.",
              )}
            >
              <Select
                value={
                  value.capabilities.supports_reasoning ? value.capabilities.reasoning_effort || "auto" : "unsupported"
                }
                onChange={(event) => {
                  const effort = event.target.value;
                  field("capabilities", {
                    ...value.capabilities,
                    supports_reasoning: effort !== "unsupported",
                    reasoning_effort: effort === "unsupported" || effort === "auto" ? "" : effort,
                  });
                }}
              >
                <option value="unsupported">{t("Non pris en charge")}</option>
                <option value="auto">{t("Automatique (modèle)")}</option>
                <option value="none">{t("Désactivé")}</option>
                <option value="minimal">{t("Minimal")}</option>
                <option value="low">{t("Faible")}</option>
                <option value="medium">{t("Moyen")}</option>
                <option value="high">{t("Élevé")}</option>
                <option value="xhigh">{t("Très élevé")}</option>
              </Select>
            </Field>
            {numeric.filter((name) => !["max_concurrency", "input_cost", "output_cost"].includes(name)).map(numberField)}
          </FormGrid>
        </Card>
        <Card
          title={t("Coûts et capacité")}
          description={t(
            "La limite de livres simultanés s’applique à ce provider, analyses, traductions et relectures confondues. Chaque provider dispose de sa propre capacité indépendante.",
          )}
        >
          <FormGrid columns={3}>
            {numeric.filter((name) => ["max_concurrency", "input_cost", "output_cost"].includes(name)).map(numberField)}
          </FormGrid>
        </Card>
        <details className="disclosure">
          <summary>{t("Capacités déclarées")}</summary>
          <div className="stack">
            <div className="checks">
              {(["supports_json_schema", "supports_json_object", "supports_tool_calls"] as const).map((cap) => (
                <Checkbox
                  key={cap}
                  label={<code>{cap}</code>}
                  checked={Boolean(value.capabilities[cap])}
                  onChange={(e) => field("capabilities", { ...value.capabilities, [cap]: e.target.checked })}
                />
              ))}
            </div>
            <Field label={t("Paramètre limite de sortie")} className="narrow-field">
              <Select
                value={value.capabilities.max_tokens_parameter || "max_tokens"}
                onChange={(e) => field("capabilities", { ...value.capabilities, max_tokens_parameter: e.target.value })}
              >
                <option>max_tokens</option>
                <option>max_completion_tokens</option>
              </Select>
            </Field>
          </div>
        </details>
        <div className="form-actions sticky-actions">
          <Button type="submit" variant="primary">
            {t("Enregistrer")}
          </Button>
          <Button
            disabled={!value.id}
            onClick={() =>
              void run(async () => {
                const r = await send<{ ok: boolean; models: string[]; message: string }>(`/providers/${value.id}/test`);
                setResult(r.message);
                setModels(r.models);
              })
            }
          >
            {t("Tester / détecter les modèles")}
          </Button>
          <span className="grow" />
          {value.id && (
            <Button
              variant="ghost"
              icon="trash"
              className="danger-text"
              onClick={() =>
                void (async () => {
                  const accepted = await confirm({
                    title: t("Supprimer le provider « {name} » ?", { name: value.name }),
                    message: t(
                      "Un provider encore utilisé par un livre ou par l’historique des requêtes ne peut pas être supprimé ; le serveur indiquera quoi changer.",
                    ),
                    confirmLabel: t("Supprimer"),
                    tone: "danger",
                  });
                  if (!accepted) return;
                  await run(async () => {
                    await api(`/providers/${value.id}`, { method: "DELETE" });
                    setValue(blank());
              setOriginal(null);
                    setProviders(await api("/providers"));
                    setResult(t("Provider supprimé."));
                  });
                })()
              }
            >
              {t("Supprimer ce provider")}
            </Button>
          )}
        </div>
        {result && (
          <Callout tone="info" role="status">
            {result}
          </Callout>
        )}
      </form>
    </div>
  );
}

function MemorySettings({ run }: { run: Run }) {
  const { t } = useI18n();
  const [value, setValue] = useState<MemoryConfig | null>(null);
  const [key, setKey] = useState("");
  const [result, setResult] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    void run.background(async () => setValue(await api("/settings/memory")));
  }, [run]);
  if (!value) return <LoadingBlock label={t("Chargement…")} />;
  const set = (change: Partial<MemoryConfig>) => {
    setSaved(false);
    setValue({ ...value, ...change });
  };
  return (
    <Card
      className="settings-card"
      title={t("Mémoire narrative · OpenViking")}
      description={t(
        "Hybrid conserve la continuité SQL lorsque OpenViking est absent ou indisponible. Les ressources sont séparées par propriétaire et projet, avec contrôle temporel des événements.",
      )}
    >
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          void run(async () => {
            const { has_api_key: _hidden, ...body } = value;
            await send("/settings/memory", { ...body, api_key: key || null }, "PUT");
            setKey("");
            setSaved(true);
          });
        }}
      >
        <FormGrid>
          <Field label={t("URL OpenViking")}>
            <Input value={value.base_url} onChange={(e) => set({ base_url: e.target.value })} placeholder="http://openviking:1933" />
          </Field>
          <Field label={t("Racine dédiée viking://")}>
            <Input value={value.root_uri} onChange={(e) => set({ root_uri: e.target.value })} />
          </Field>
          <Field label={t("Clé API")}>
            <Input
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder={value.has_api_key ? t("Enregistrée") : t("Facultative")}
            />
          </Field>
          <Field label={t("Authentification")}>
            <Select value={value.auth_mode} onChange={(e) => set({ auth_mode: e.target.value })}>
              <option value="api_key">{t("API key — recommandé")}</option>
              <option value="trusted">Trusted gateway</option>
            </Select>
          </Field>
          {value.auth_mode === "trusted" && (
            <>
              <Field label="Account">
                <Input value={value.account} onChange={(e) => set({ account: e.target.value })} />
              </Field>
              <Field label="User">
                <Input value={value.user} onChange={(e) => set({ user: e.target.value })} />
              </Field>
            </>
          )}
        </FormGrid>
        <FormGrid columns={3}>
          <Field label={t("Budget contexte")}>
            <Input type="number" value={value.context_budget} onChange={(e) => set({ context_budget: +e.target.value })} />
          </Field>
          <Field label={t("Budget retrieval")}>
            <Input type="number" value={value.retrieval_budget} onChange={(e) => set({ retrieval_budget: +e.target.value })} />
          </Field>
          <Field label={t("Score minimal")}>
            <Input
              type="number"
              min="0"
              max="1"
              step=".01"
              value={value.min_score}
              onChange={(e) => set({ min_score: +e.target.value })}
            />
          </Field>
          <Field label={t("Timeout (secondes)")}>
            <Input type="number" value={value.timeout} onChange={(e) => set({ timeout: +e.target.value })} />
          </Field>
        </FormGrid>
        <div className="checks">
          <Switch
            label={t("Recherche sémantique (find)")}
            checked={value.enable_search}
            onChange={(e) => set({ enable_search: e.target.checked })}
          />
          <Switch
            label={t("Recherche approfondie (search)")}
            checked={value.enable_deep_search}
            onChange={(e) => set({ enable_deep_search: e.target.checked })}
          />
        </div>
        <p className="subtle">
          {t(
            "Les modèles VLM et embeddings se configurent sur le serveur OpenViking. L’application utilise find/search limités au projet, puis lit les souvenirs pertinents en L2. L’assemblage global non borné est évité.",
          )}
        </p>
        <div className="form-actions">
          <Button type="submit" variant="primary">
            {t("Enregistrer")}
          </Button>
          <Button onClick={() => void run(async () => setResult(await send("/settings/memory/test")))}>
            {t("Tester la connexion")}
          </Button>
          {saved && (
            <span role="status" className="tone-text-success">
              {t("Configuration enregistrée.")}
            </span>
          )}
        </div>
        {result != null && (
          <details className="disclosure" open>
            <summary>{t("Résultat du test")}</summary>
            <pre>{JSON.stringify(result, null, 2)}</pre>
          </details>
        )}
      </form>
    </Card>
  );
}

function Prompts({ run }: { run: Run }) {
  const { t } = useI18n();
  const [values, setValues] = useState<Prompt[] | null>(null);
  const [selected, setSelected] = useState(0);
  const [saved, setSaved] = useState("");
  useEffect(() => {
    void run.background(async () => setValues(await api("/prompts")));
  }, [run]);
  if (!values) return <LoadingBlock label={t("Chargement…")} />;
  const current = values[selected];
  return (
    <div className="master-detail">
      <Card padded={false} className="master-list">
        <div className="master-list-header">
          <strong>Prompts</strong>
          <Button size="sm" variant="ghost" icon="download" onClick={() => download("prompts.json", values)}>
            {t("Exporter les prompts")}
          </Button>
        </div>
        <ul>
          {values.map((v, i) => (
            <li key={v.name}>
              <button
                type="button"
                className={cx("master-item", i === selected && "is-active")}
                aria-current={i === selected ? "true" : undefined}
                onClick={() => {
                  setSelected(i);
                  setSaved("");
                }}
              >
                <span className="master-item-title">{v.name}</span>
                <small>{t("Version {version}", { version: v.version || t("initiale") })}</small>
              </button>
            </li>
          ))}
        </ul>
      </Card>
      {current && (
        <Card title={<code>{current.name}</code>}>
          <div className="stack">
            <TextArea
              className="code-editor"
              rows={22}
              aria-label={t("Contenu du prompt")}
              value={current.content}
              onChange={(e) =>
                setValues(values.map((v, i) => (i === selected ? { ...v, content: e.target.value } : v)))
              }
            />
            <div className="form-actions">
              <Button
                variant="primary"
                onClick={() =>
                  void run(async () => {
                    await send(`/prompts/${current.name}`, { content: current.content }, "PUT");
                    setValues(await api("/prompts"));
                    setSaved(t("Nouvelle version enregistrée."));
                  })
                }
              >
                {t("Créer une version")}
              </Button>
              {saved && (
                <span role="status" className="tone-text-success">
                  {saved}
                </span>
              )}
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}

function Users({ run }: { run: Run }) {
  const { t } = useI18n();
  const [users, setUsers] = useState<User[] | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [selected, setSelected] = useState<User | null>(null);
  const [reset, setReset] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    void run.background(async () => setUsers(await api("/users")));
  }, [run]);
  return (
    <div className="master-detail users-layout">
      <div className="stack">
        <Card title={t("Utilisateurs de Libris")} padded={false}>
          {users === null ? (
            <div className="card-inset">
              <LoadingBlock label={t("Chargement…")} lines={2} />
            </div>
          ) : (
            <ul className="user-list">
              {users.map((u) => (
                <li key={u.id} className={cx(selected?.id === u.id && "is-active")}>
                  <span className="avatar" aria-hidden="true">
                    {u.username.slice(0, 1).toUpperCase()}
                  </span>
                  <span className="grow">
                    <strong>{u.username}</strong>
                    <small className="subtle">{u.admin ? t("Administrateur") : t("Utilisateur")}</small>
                  </span>
                  <Badge tone={u.active === false ? "neutral" : "success"} dot>
                    {t(u.active === false ? "Désactivé" : "Actif")}
                  </Badge>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => {
                      setSelected(u);
                      setReset("");
                      setNotice("");
                    }}
                  >
                    {t("Gérer {user}", { user: u.username })}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title={t("Créer un compte")}>
          <form
            className="stack"
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                await send("/users", { username, password });
                setUsername("");
                setPassword("");
                setUsers(await api("/users"));
              });
            }}
          >
            <FormGrid>
              <Field label={t("Utilisateur")}>
                <Input value={username} onChange={(e) => setUsername(e.target.value)} required />
              </Field>
              <Field label={t("Mot de passe initial")}>
                <Input type="password" minLength={12} value={password} onChange={(e) => setPassword(e.target.value)} required />
              </Field>
            </FormGrid>
            <div>
              <Button type="submit" variant="primary" icon="plus">
                {t("Créer le compte")}
              </Button>
            </div>
          </form>
        </Card>
      </div>
      {selected ? (
        <Card
          title={selected.username}
          aria-label={selected.username}
          description={t("La désactivation conserve les livres. Les changements de rôle révoquent les sessions du compte.")}
        >
          <div className="stack">
            <form
              className="stack"
              onSubmit={(e) => {
                e.preventDefault();
                setBusy(true);
                void run(async () => {
                  await send(`/users/${selected.id}`, { admin: selected.admin, active: selected.active !== false }, "PUT");
                  setUsers(await api("/users"));
                  setNotice(t("Compte enregistré. Les sessions ont été révoquées."));
                }).finally(() => setBusy(false));
              }}
            >
              <Checkbox
                label={t("Administrateur")}
                checked={selected.admin}
                onChange={(e) => setSelected({ ...selected, admin: e.target.checked })}
              />
              <Checkbox
                label={t("Compte actif")}
                checked={selected.active !== false}
                onChange={(e) => setSelected({ ...selected, active: e.target.checked })}
              />
              <div>
                <Button type="submit" disabled={busy}>
                  {t("Enregistrer le compte")}
                </Button>
              </div>
            </form>
            <hr className="divider" />
            <form
              className="stack"
              onSubmit={(e) => {
                e.preventDefault();
                setBusy(true);
                void run(async () => {
                  await send(`/users/${selected.id}/password`, { password: reset }, "PUT");
                  setReset("");
                  setNotice(t("Réinitialisation effectuée. Les sessions ont été révoquées."));
                }).finally(() => setBusy(false));
              }}
            >
              <Field label={t("Nouveau mot de passe")}>
                <Input
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={12}
                  maxLength={200}
                  value={reset}
                  onChange={(e) => setReset(e.target.value)}
                />
              </Field>
              <div>
                <Button type="submit" disabled={busy}>
                  {t("Réinitialiser le mot de passe")}
                </Button>
              </div>
            </form>
            {notice && (
              <Callout tone="success" role="status">
                {notice}
              </Callout>
            )}
          </div>
        </Card>
      ) : (
        <Card className="placeholder-card">
          <EmptyState compact icon="users" title={t("Sélectionnez un compte pour le gérer.")} />
        </Card>
      )}
    </div>
  );
}

