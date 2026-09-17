import { useEffect, useState } from "react";
import { api, download, send } from "../api";
import { registerTranslations, useI18n } from "../i18n";
import type { Provider, Run, User } from "../types";
import { CodexConnection } from "./CodexConnection";
import { RecoverySettings } from "./Account";

const translations: Record<string, string> = {
  "Modèle local": "Local model", "Administration": "Administration", "Paramètres": "Settings", "← Bibliothèque": "← Library", "Mémoire · OpenViking": "Memory · OpenViking", "Utilisateurs": "Users",
  "{message} {count} résultat(s). Le test ne modifie pas la configuration.": "{message} {count} result(s). The test does not change the configuration.", "Configuration enregistrée. Prise en compte par le worker aux prochaines recherches.": "Configuration saved. It will be used by the worker for subsequent searches.", "Recherche web · SearXNG": "Web search · SearXNG", "Recherche terminologique facultative pendant la revue finale. Les termes recherchés sont transmis à votre instance et à ses moteurs amont.": "Optional terminology search during final review. Search terms are sent to your instance and its upstream engines.", "URL de l’instance SearXNG": "SearXNG instance URL", "Activer la recherche pendant la revue finale": "Enable search during final review", "Le format JSON doit être autorisé dans search.formats sur SearXNG. Deux recherches maximum par passage. Aucune recherche lorsque cette option est désactivée.": "JSON format must be enabled in SearXNG search.formats. A maximum of two searches per segment. No search is made when this option is disabled.", "Enregistrer": "Save", "Tester la connexion": "Test connection",
  "Provider enregistré.": "Provider saved.", "Nouveau provider": "New provider", "Connexion au modèle": "Model connection", "Les clés sont chiffrées côté serveur et ne sont jamais renvoyées au navigateur. L’URL doit être accessible depuis le conteneur.": "Keys are encrypted server-side and are never returned to the browser. The URL must be reachable from the container.", "Connexion / protocole": "Connection / protocol", "Codex / OpenAI · clé API (Responses)": "Codex / OpenAI · API key (Responses)", "Nom": "Name", "Clé API": "API key", "Enregistrée — laisser vide pour conserver": "Saved — leave empty to keep", "Facultative": "Optional", "Requise": "Required", "Anthropic · Claude (clé API)": "Anthropic · Claude (API key)", "OpenAI · Chat Completions (clé API)": "OpenAI · Chat Completions (API key)", "L’inférence utilise l’API Anthropic, facturée à l’usage. Température et Top P ne sont pas envoyés : les modèles Claude actuels les refusent.": "Inference uses the Anthropic API, billed per use. Temperature and Top P are not sent: current Claude models reject them.", "Modèle": "Model", "Fenêtre de contexte": "Context window", "Tokens de sortie maximum": "Maximum output tokens", "Température": "Temperature", "Timeout (secondes)": "Timeout (seconds)", "Livres simultanés": "Concurrent books", "Coût / million tokens entrée": "Cost / million input tokens", "Coût / million tokens sortie": "Cost / million output tokens", "La limite de livres simultanés s’applique à ce provider, analyses, traductions et relectures confondues. Chaque provider dispose de sa propre capacité indépendante.": "The concurrent-book limit applies to this provider across analysis, translation, and review. Each provider has its own independent capacity.", "L’inférence utilise OpenAI. Température et Top P ne sont pas envoyés pour ce transport.": "Inference uses OpenAI. Temperature and Top P are not sent for this transport.", "La limite de sortie est une réservation du budget de l’application ; Codex ne fournit pas de plafond de génération équivalent à max_output_tokens.": "The output limit reserves application budget; Codex does not provide a generation ceiling equivalent to max_output_tokens.", "Une clé API est facturée séparément de l’abonnement ChatGPT.": "An API key is billed separately from the ChatGPT subscription.", "Capacités déclarées": "Declared capabilities", "Paramètre limite de sortie": "Output limit parameter", "Non envoyé": "Not sent", "Tester / détecter les modèles": "Test / detect models",
  "Chargement…": "Loading…", "Mémoire narrative · OpenViking": "Narrative memory · OpenViking", "Hybrid conserve la continuité SQL lorsque OpenViking est absent ou indisponible. Les ressources sont séparées par propriétaire et projet, avec contrôle temporel des événements.": "Hybrid preserves SQL continuity when OpenViking is absent or unavailable. Resources are separated by owner and project, with temporal control of events.", "URL OpenViking": "OpenViking URL", "Racine dédiée viking://": "Dedicated viking:// root", "Budget contexte": "Context budget", "Budget retrieval": "Retrieval budget", "Score minimal": "Minimum score", "Authentification": "Authentication", "API key — recommandé": "API key — recommended", "Recherche sémantique (find)": "Semantic search (find)", "Recherche approfondie (search)": "Deep search (search)", "Les modèles VLM et embeddings se configurent sur le serveur OpenViking. L’application utilise find/search limités au projet, puis lit les souvenirs pertinents en L2. L’assemblage global non borné est évité.": "VLM and embedding models are configured on the OpenViking server. The application uses project-limited find/search, then reads relevant memories in L2. Unbounded global assembly is avoided.",
  "Version {version}": "Version {version}", "initiale": "initial", "Contenu du prompt": "Prompt content", "Nouvelle version enregistrée.": "New version saved.", "Créer une version": "Create version", "Exporter les prompts": "Export prompts", "Utilisateurs de Libris": "Libris users", "Utilisateur": "User", "Rôle": "Role", "Administrateur": "Administrator", "Créer un compte": "Create an account", "Mot de passe initial": "Initial password", "Créer le compte": "Create account", "Enregistrée": "Saved",
};
registerTranslations(translations);
registerTranslations({ "Codex · compte ChatGPT": "Codex · ChatGPT account" });
registerTranslations({
  "Gérer": "Manage", "Actif": "Active", "Désactivé": "Disabled",
  "Compte actif": "Active account", "Enregistrer le compte": "Save account",
  "Réinitialiser le mot de passe": "Reset password",
  "Réinitialisation effectuée. Les sessions ont été révoquées.": "Password reset. Sessions have been revoked.",
  "Compte enregistré. Les sessions ont été révoquées.": "Account saved. Sessions have been revoked.",
  "La désactivation conserve les livres. Les changements de rôle révoquent les sessions du compte.": "Deactivation preserves books. Role changes revoke the account's sessions.",
});
registerTranslations({
  "Niveau de raisonnement": "Reasoning level",
  "Non pris en charge": "Not supported",
  "Automatique (modèle)": "Automatic (model default)",
  "Minimal": "Minimal",
  "Faible": "Low",
  "Moyen": "Medium",
  "Élevé": "High",
  "Très élevé": "Extra high",
  "Désactivé envoie explicitement « none ». Automatique laisse le modèle choisir. Non pris en charge n’envoie aucun paramètre de raisonnement.":
    "Disabled explicitly sends ‘none’. Automatic lets the model decide. Not supported sends no reasoning parameter.",
});

const initial: Provider = {
  id: "",
  kind: "openai",
  name: "Modèle local",
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
    <main className="settings">
      <div className="page-heading">
        <div>
          <p className="eyebrow">{t("Administration")}</p>
          <h1>{t("Paramètres")}</h1>
        </div>
        <a href="#library">{t("← Bibliothèque")}</a>
      </div>
      <div className="tabs">
        {[
          ["providers", "Providers LLM"],
          ["memory", t("Mémoire · OpenViking")],
          ["search", "SearXNG"],
          ["prompts", "Prompts"],
          ["users", t("Utilisateurs")],
          ["recovery", t("Reprise automatique")],
        ].map(([id, label]) => (
          <button
            key={id}
            className={tab === id ? "active" : ""}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>
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
    </main>
  );
}

function SearchSettings({ run }: { run: Run }) {
  const { t } = useI18n();
  const [value, setValue] = useState({ base_url: "", enabled: false });
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  useEffect(() => {
    void run(async () => {
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
          const response = await send<{ message: string; results: number }>(
            "/settings/searxng/test",
            value,
          );
          setResult(
            t("{message} {count} résultat(s). Le test ne modifie pas la configuration.").replace("{message}", response.message).replace("{count}", String(response.results)),
          );
        } else {
          setValue(await send("/settings/searxng", value, "PUT"));
          setResult(
            t("Configuration enregistrée. Prise en compte par le worker aux prochaines recherches."),
          );
        }
      } finally {
        setBusy(false);
      }
    });
  }
  return (
    <section>
      <h2>{t("Recherche web · SearXNG")}</h2>
      <p className="muted">{t("Recherche terminologique facultative pendant la revue finale. Les termes recherchés sont transmis à votre instance et à ses moteurs amont.")}</p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void action(false);
        }}
      >
        <label>
          {t("URL de l’instance SearXNG")}
          <input
            type="url"
            placeholder="https://search.example.com"
            value={value.base_url}
            disabled={!ready || busy}
            onChange={(event) => {
              setValue({ ...value, base_url: event.target.value });
              setResult("");
            }}
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={value.enabled}
            disabled={!ready || busy}
            onChange={(event) => {
              setValue({ ...value, enabled: event.target.checked });
              setResult("");
            }}
          />{" "}
           {t("Activer la recherche pendant la revue finale")}
        </label>
        <p className="muted">{t("Le format JSON doit être autorisé dans search.formats sur SearXNG. Deux recherches maximum par passage. Aucune recherche lorsque cette option est désactivée.")}</p>
        <div className="actions">
          <button className="primary" disabled={!ready || busy}>
            {t("Enregistrer")}
          </button>
          <button
            type="button"
            disabled={!ready || busy || !value.base_url}
            onClick={() => void action(true)}
          >
            {t("Tester la connexion")}
          </button>
        </div>
        {result && (
          <p role="status" className="notice">
            {result}
          </p>
        )}
      </form>
    </section>
  );
}

function ProviderSettings({ run }: { run: Run }) {
  const { t } = useI18n();
  const [providers, setProviders] = useState<Provider[]>([]);
  const [value, setValue] = useState(initial);
  const [key, setKey] = useState("");
  const [result, setResult] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const native = value.kind === "anthropic" || value.kind === "openai_direct";
  useEffect(() => {
    void run(async () => setProviders(await api("/providers")));
  }, [run]);
  function field<K extends keyof Provider>(name: K, next: Provider[K]) {
    setValue({ ...value, [name]: next });
  }
  async function save() {
    const { id, has_api_key: _hidden, created_at: _created, ...body } = value;
    const saved = await send<Provider>(
      `/providers${id ? "/" + id : ""}`,
      {
        ...body,
        model: body.model || (body.kind === "codex_chatgpt" ? "codex" : ""),
        api_key: key || null,
      },
      id ? "PUT" : "POST",
    );
    setValue(saved);
    setKey("");
    setProviders(await api("/providers"));
    setResult(t("Provider enregistré."));
  }
  return (
    <div className="settings-grid">
      <aside className="list-nav">
        {providers.map((p) => (
          <button
            key={p.id}
            className={value.id === p.id ? "active" : ""}
            onClick={() => {
              setValue(p);
              setKey("");
              setResult("");
            }}
          >
            {p.name}
            <small>{p.model}</small>
          </button>
        ))}
        <button
          onClick={() => {
            setValue({ ...initial });
            setKey("");
            setModels([]);
            setResult("");
          }}
        >
          + {t("Nouveau provider")}
        </button>
      </aside>
      <section>
        <h2>{t("Connexion au modèle")}</h2>
        <p className="muted">{t("Les clés sont chiffrées côté serveur et ne sont jamais renvoyées au navigateur. L’URL doit être accessible depuis le conteneur.")}</p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void run(save);
          }}
        >
          <div className="form-grid">
            <label>
               {t("Connexion / protocole")}
              <select
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
                          : kind === "openai_responses" ||
                              kind === "openai_direct"
                            ? "https://api.openai.com/v1"
                            : "http://localhost:8000/v1",
                    model: "",
                    timeout: kind === "openai" ? 180 : 600,
                    capabilities: {
                      ...value.capabilities,
                      supports_json_schema:
                        kind !== "openai" && kind !== "anthropic",
                      supports_reasoning:
                        kind === "openai_responses" || kind === "codex_chatgpt",
                    },
                  });
                }}
              >
                <option value="openai">
                  OpenAI-compatible · Chat Completions
                </option>
                <option value="openai_responses">
                  {t("Codex / OpenAI · clé API (Responses)")}
                </option>
                <option value="codex_chatgpt">{t("Codex · compte ChatGPT")}</option>
                <option value="anthropic">
                  {t("Anthropic · Claude (clé API)")}
                </option>
                <option value="openai_direct">
                  {t("OpenAI · Chat Completions (clé API)")}
                </option>
              </select>
            </label>
            <label>
               {t("Nom")}
              <input
                value={value.name}
                onChange={(e) => field("name", e.target.value)}
                required
              />
            </label>
            {value.kind !== "codex_chatgpt" && (
              <label>
                Base URL
                <input
                  value={value.base_url}
                  onChange={(e) => field("base_url", e.target.value)}
                  required
                  placeholder="http://serveur:8000/v1"
                />
              </label>
            )}
            {value.kind !== "codex_chatgpt" && (
              <label>
                {t("Clé API")}
                <input
                  type="password"
                  autoComplete="new-password"
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                  required={native && !value.has_api_key}
                  placeholder={
                    value.has_api_key
                      ? t("Enregistrée — laisser vide pour conserver")
                      : native
                        ? t("Requise")
                        : t("Facultative")
                  }
                />
              </label>
            )}
            <label>
              {t("Modèle")}
              <input
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
            </label>
            <label>
              {t("Niveau de raisonnement")}
              <select
                value={
                  value.capabilities.supports_reasoning
                    ? value.capabilities.reasoning_effort || "auto"
                    : "unsupported"
                }
                onChange={(event) => {
                  const effort = event.target.value;
                  field("capabilities", {
                    ...value.capabilities,
                    supports_reasoning: effort !== "unsupported",
                    reasoning_effort:
                      effort === "unsupported" || effort === "auto" ? "" : effort,
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
              </select>
              <small className="muted">
                {t("Désactivé envoie explicitement « none ». Automatique laisse le modèle choisir. Non pris en charge n’envoie aucun paramètre de raisonnement.")}
              </small>
            </label>
            {(
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
            )
              .filter(
                (name) =>
                  value.kind === "openai" ||
                  value.kind === "openai_direct" ||
                  !["temperature", "top_p"].includes(name),
              )
              .map((name) => (
                <label key={name}>
                  {
                    {
                      context_window: t("Fenêtre de contexte"),
                       max_output_tokens: t("Tokens de sortie maximum"),
                      temperature: t("Température"),
                      top_p: "Top P",
                       timeout: t("Timeout (secondes)"),
                      max_concurrency: t("Livres simultanés"), input_cost: t("Coût / million tokens entrée"), output_cost: t("Coût / million tokens sortie"),
                    }[name]
                  }
                  <input
                    type="number"
                    min={name === "max_concurrency" ? "1" : "0"}
                    step={
                      [
                        "temperature",
                        "top_p",
                        "input_cost",
                        "output_cost",
                      ].includes(name)
                        ? ".01"
                        : "1"
                    }
                    value={value[name]}
                    onChange={(e) => field(name, Number(e.target.value))}
                  />
                </label>
              ))}
          </div>
          <p className="muted">{t("La limite de livres simultanés s’applique à ce provider, analyses, traductions et relectures confondues. Chaque provider dispose de sa propre capacité indépendante.")}</p>
          {value.kind === "codex_chatgpt" && (
            <CodexConnection
              key={value.id}
              providerId={value.id}
              run={run}
              onModels={(models) => {
                setModels(models);
                if (!value.model || value.model === "codex")
                  setValue({ ...value, model: models[0] || "" });
              }}
            />
          )}
          {value.kind === "anthropic" && (
            <p className="muted">
              {t("L’inférence utilise l’API Anthropic, facturée à l’usage. Température et Top P ne sont pas envoyés : les modèles Claude actuels les refusent.")}
            </p>
          )}
          {(value.kind === "openai_responses" ||
            value.kind === "codex_chatgpt") && (
            <p className="muted">
              {t("L’inférence utilise OpenAI. Température et Top P ne sont pas envoyés pour ce transport.")}{" "}
              {value.kind === "codex_chatgpt"
                ? t("La limite de sortie est une réservation du budget de l’application ; Codex ne fournit pas de plafond de génération équivalent à max_output_tokens.")
                : t("Une clé API est facturée séparément de l’abonnement ChatGPT.")}
            </p>
          )}
          <details open>
            <summary>{t("Capacités déclarées")}</summary>
            <div className="checks">
              {(
                [
                  "supports_json_schema",
                  "supports_json_object",
                  "supports_tool_calls",
                ] as const
              ).map((cap) => (
                <label key={cap}>
                  <input
                    type="checkbox"
                    checked={Boolean(value.capabilities[cap])}
                    onChange={(e) =>
                      field("capabilities", {
                        ...value.capabilities,
                        [cap]: e.target.checked,
                      })
                    }
                  />
                  {cap}
                </label>
              ))}
            </div>
            <div className="form-grid">
              <label>
                {t("Paramètre limite de sortie")}
                <select
                  value={
                    value.capabilities.max_tokens_parameter || "max_tokens"
                  }
                  onChange={(e) =>
                    field("capabilities", {
                      ...value.capabilities,
                      max_tokens_parameter: e.target.value,
                    })
                  }
                >
                  <option>max_tokens</option>
                  <option>max_completion_tokens</option>
                </select>
              </label>
            </div>
          </details>
          <div className="actions">
            <button className="primary">{t("Enregistrer")}</button>
            <button
              type="button"
              disabled={!value.id}
              onClick={() =>
                void run(async () => {
                  const r = await send<{
                    ok: boolean;
                    models: string[];
                    message: string;
                  }>(`/providers/${value.id}/test`);
                  setResult(r.message);
                  setModels(r.models);
                })
              }
            >
              {t("Tester / détecter les modèles")}
            </button>
          </div>
          {result && (
            <p className="notice" role="status">
              {result}
            </p>
          )}
        </form>
      </section>
    </div>
  );
}

function MemorySettings({ run }: { run: Run }) {
  const { t } = useI18n();
  const [value, setValue] = useState<MemoryConfig | null>(null);
  const [key, setKey] = useState("");
  const [result, setResult] = useState<unknown>(null);
  useEffect(() => {
    void run(async () => setValue(await api("/settings/memory")));
  }, [run]);
  if (!value) return <p>{t("Chargement…")}</p>;
  return (
    <section className="narrow">
      <h2>{t("Mémoire narrative · OpenViking")}</h2>
      <p className="muted">{t("Hybrid conserve la continuité SQL lorsque OpenViking est absent ou indisponible. Les ressources sont séparées par propriétaire et projet, avec contrôle temporel des événements.")}</p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void run(async () => {
            const { has_api_key: _hidden, ...body } = value;
            await send(
              "/settings/memory",
              { ...body, api_key: key || null },
              "PUT",
            );
            setKey("");
            setResult({ message: t("Configuration enregistrée.") });
          });
        }}
      >
        <div className="form-grid">
          <label>
            {t("URL OpenViking")}
            <input
              value={value.base_url}
              onChange={(e) => setValue({ ...value, base_url: e.target.value })}
              placeholder="http://openviking:1933"
            />
          </label>
          <label>
            {t("Racine dédiée viking://")}
            <input
              value={value.root_uri}
              onChange={(e) => setValue({ ...value, root_uri: e.target.value })}
            />
          </label>
          <label>
            {t("Clé API")}
            <input
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder={value.has_api_key ? t("Enregistrée") : t("Facultative")}
            />
          </label>
          <label>
            {t("Budget contexte")}
            <input
              type="number"
              value={value.context_budget}
              onChange={(e) =>
                setValue({ ...value, context_budget: +e.target.value })
              }
            />
          </label>
          <label>
            {t("Budget retrieval")}
            <input
              type="number"
              value={value.retrieval_budget}
              onChange={(e) =>
                setValue({ ...value, retrieval_budget: +e.target.value })
              }
            />
          </label>
          <label>
            {t("Score minimal")}
            <input
              type="number"
              min="0"
              max="1"
              step=".01"
              value={value.min_score}
              onChange={(e) =>
                setValue({ ...value, min_score: +e.target.value })
              }
            />
          </label>
          <label>
            {t("Timeout (secondes)")}
            <input
              type="number"
              value={value.timeout}
              onChange={(e) => setValue({ ...value, timeout: +e.target.value })}
            />
          </label>
          <label>
            {t("Authentification")}
            <select
              value={value.auth_mode}
              onChange={(e) =>
                setValue({ ...value, auth_mode: e.target.value })
              }
            >
              <option value="api_key">{t("API key — recommandé")}</option>
              <option value="trusted">Trusted gateway</option>
            </select>
          </label>
          {value.auth_mode === "trusted" && (
            <>
              <label>
                Account
                <input
                  value={value.account}
                  onChange={(e) =>
                    setValue({ ...value, account: e.target.value })
                  }
                />
              </label>
              <label>
                User
                <input
                  value={value.user}
                  onChange={(e) => setValue({ ...value, user: e.target.value })}
                />
              </label>
            </>
          )}
        </div>
        <div className="checks">
          {(["enable_search", "enable_deep_search"] as const).map((k) => (
            <label key={k}>
              <input
                type="checkbox"
                checked={value[k]}
                onChange={(e) => setValue({ ...value, [k]: e.target.checked })}
              />
              {k === "enable_search"
                ? t("Recherche sémantique (find)")
                : t("Recherche approfondie (search)")}
            </label>
          ))}
        </div>
        <div className="actions">
          <button className="primary">{t("Enregistrer")}</button>
          <button
            type="button"
            onClick={() =>
              void run(async () =>
                setResult(await send("/settings/memory/test")),
              )
            }
          >
            {t("Tester la connexion")}
          </button>
        </div>
      </form>
      {result != null && <pre>{JSON.stringify(result, null, 2)}</pre>}
      <p className="muted">{t("Les modèles VLM et embeddings se configurent sur le serveur OpenViking. L’application utilise find/search limités au projet, puis lit les souvenirs pertinents en L2. L’assemblage global non borné est évité.")}</p>
    </section>
  );
}

function Prompts({ run }: { run: Run }) {
  const { t } = useI18n();
  const [values, setValues] = useState<Prompt[]>([]);
  const [selected, setSelected] = useState(0);
  const [saved, setSaved] = useState("");
  useEffect(() => {
    void run(async () => setValues(await api("/prompts")));
  }, [run]);
  const current = values[selected];
  return (
    <div className="settings-grid">
      <aside className="list-nav">
        {values.map((v, i) => (
          <button
            key={v.name}
            className={i === selected ? "active" : ""}
            onClick={() => setSelected(i)}
          >
            {v.name}
            <small>{t("Version {version}").replace("{version}", String(v.version || t("initiale")))}</small>
          </button>
        ))}
      </aside>
      {current && (
        <section>
          <h2>{current.name}</h2>
          <textarea
            className="prompt-editor"
            aria-label={t("Contenu du prompt")}
            value={current.content}
            onChange={(e) =>
              setValues(
                values.map((v, i) =>
                  i === selected ? { ...v, content: e.target.value } : v,
                ),
              )
            }
          />
          <div className="actions">
            <button
              className="primary"
              onClick={() =>
                void run(async () => {
                  await send(
                    `/prompts/${current.name}`,
                    { content: current.content },
                    "PUT",
                  );
                  setValues(await api("/prompts"));
                  setSaved(t("Nouvelle version enregistrée."));
                })
              }
            >
              {t("Créer une version")}
            </button>
            <button onClick={() => download("prompts.json", values)}>
              {t("Exporter les prompts")}
            </button>
          </div>
          <p role="status">{saved}</p>
        </section>
      )}
    </div>
  );
}

function Users({ run }: { run: Run }) {
  const { t } = useI18n();
  const [users, setUsers] = useState<User[]>([]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [selected, setSelected] = useState<User | null>(null);
  const [reset, setReset] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    void run(async () => setUsers(await api("/users")));
  }, [run]);
  return (
    <section className="narrow">
      <h2>{t("Utilisateurs de Libris")}</h2>
      <table className="accounts-table">
        <thead>
          <tr>
            <th>{t("Utilisateur")}</th>
            <th>{t("Rôle")}</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id}>
              <td>{u.username}</td>
              <td>{u.admin ? t("Administrateur") : t("Utilisateur")} · {t(u.active === false ? "Désactivé" : "Actif")}
                <button disabled={busy} onClick={() => { setSelected(u); setReset(""); setNotice(""); }}>{t("Gérer")} {u.username}</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {selected && <section className="account-editor" aria-label={selected.username}>
        <h3>{selected.username}</h3>
        <p>{t("La désactivation conserve les livres. Les changements de rôle révoquent les sessions du compte.")}</p>
        <form onSubmit={e => { e.preventDefault(); setBusy(true); void run(async () => {
          await send(`/users/${selected.id}`, { admin: selected.admin, active: selected.active !== false }, "PUT");
          setUsers(await api("/users")); setNotice(t("Compte enregistré. Les sessions ont été révoquées."));
        }).finally(() => setBusy(false)); }}>
          <label><input type="checkbox" checked={selected.admin} onChange={e => setSelected({ ...selected, admin: e.target.checked })} />{t("Administrateur")}</label>
          <label><input type="checkbox" checked={selected.active !== false} onChange={e => setSelected({ ...selected, active: e.target.checked })} />{t("Compte actif")}</label>
          <button disabled={busy}>{t("Enregistrer le compte")}</button>
        </form>
        <form onSubmit={e => { e.preventDefault(); setBusy(true); void run(async () => {
          await send(`/users/${selected.id}/password`, { password: reset }, "PUT"); setReset("");
          setNotice(t("Réinitialisation effectuée. Les sessions ont été révoquées."));
        }).finally(() => setBusy(false)); }}>
          <label>{t("Nouveau mot de passe")}<input type="password" autoComplete="new-password" required minLength={12} maxLength={200} value={reset} onChange={e => setReset(e.target.value)} /></label>
          <button disabled={busy}>{t("Réinitialiser le mot de passe")}</button>
        </form>
      </section>}
      {notice && <p role="status">{notice}</p>}
      <h3>{t("Créer un compte")}</h3>
      <form
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
        <div className="form-grid">
          <label>
            {t("Utilisateur")}
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
          </label>
          <label>
            {t("Mot de passe initial")}
            <input
              type="password"
              minLength={12}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
        </div>
        <button className="primary">{t("Créer le compte")}</button>
      </form>
    </section>
  );
}
