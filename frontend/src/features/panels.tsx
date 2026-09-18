import { useCallback, useEffect, useState } from "react";
import { api, date, download, downloadGet, labels, send } from "../api";
import { formatNumber, formatPercent, registerTranslations, useI18n } from "../i18n";
import type { Issue, LLMRequest, Project, ProviderSummary, Run, Term, User } from "../types";
import {
  Badge,
  Button,
  Callout,
  Card,
  Dialog,
  EmptyState,
  Field,
  FileButton,
  FormGrid,
  IconButton,
  Input,
  LoadingBlock,
  Menu,
  SearchInput,
  Select,
  Stat,
  StatusPill,
  Switch,
  Table,
  TextArea,
  useDialogs,
  useToast,
} from "../ui";
import { RequestDetails } from "./Editor";
import { MemoryPanel } from "./MemoryPanel";
import { duration, projectProgress } from "./progress";

registerTranslations({
  "{words} mots · {sections} sections · {images} images · {size} Mo":
    "{words} words · {sections} sections · {images} images · {size} MB",
  Livre: "Book",
  "Titre à l’export": "Export title",
  Auteur: "Author",
  Série: "Series",
  "Numéro du volume": "Volume number",
  Langues: "Languages",
  "Langue source": "Source language",
  "Langue cible (code BCP 47)": "Target language (BCP 47 code)",
  "Choisir…": "Choose…",
  Qualité: "Quality",
  Rapide: "Fast",
  "Normal · vérification": "Normal · verification",
  "Haute qualité · critique & révision": "High quality · critique & revision",
  "Maximum · polissage": "Maximum · polishing",
  "Moteur de contexte": "Context engine",
  "Interne — recommandé": "Internal — recommended",
  "Mémoire de traduction": "Translation memory",
  "Réutiliser les passages identiques déjà traduits (vos livres, même paire de langues)":
    "Reuse identical passages already translated (your books, same language pair)",
  "{count} passage repris de la mémoire de traduction": "{count} passage reused from the translation memory",
  "{count} passages repris de la mémoire de traduction": "{count} passages reused from the translation memory",
  "Stratégie du livre": "Book strategy",
  "Conservés tels quels à l’import": "Kept as in the original at import",
  "Ces éléments ne sont pas envoyés au modèle et restent identiques dans l’EPUB exporté.":
    "These elements are not sent to the model and stay unchanged in the exported EPUB.",
  "{kind} · {count}": "{kind} · {count}",
  "Instructions globales": "Global instructions",
  "Conserver les suffixes -san, -chan, -sama. Tutoyer entre Alice et Bob…":
    "Preserve -san, -chan, and -sama suffixes. Use informal address between Alice and Bob…",
  "Enregistrer les réglages": "Save settings",
  "Configuration enregistrée.": "Configuration saved.",
  "Rapport de validation à l’import": "Import validation report",
  Partage: "Sharing",
  "Les lecteurs consultent le livre ; les éditeurs peuvent aussi corriger et lancer des travaux.":
    "Readers can view the book; editors can also correct it and start jobs.",
  "Accès accordé à {user}.": "Access granted to {user}.",
  "Utilisateur à inviter": "User to invite",
  "Nom d’utilisateur existant": "Existing username",
  "Rôle sur le projet": "Project role",
  Lecteur: "Reader",
  Éditeur: "Editor",
  Partager: "Share",
  "Aucun membre : ce livre n’est visible que par vous.": "No members: only you can see this book.",
  "Révoquer l’accès de {user}": "Revoke access for {user}",
  "Révoquer l’accès de {user} ?": "Revoke access for {user}?",
  "{user} ne verra plus ce livre.": "{user} will no longer see this book.",
  Révoquer: "Revoke",
  Membres: "Members",
  Rôle: "Role",
  "Zone de danger": "Danger zone",
  "Supprimer ce projet": "Delete this project",
  "Supprimer ce projet ?": "Delete this project?",
  "Le projet local, ses traductions et ses travaux seront supprimés. La mémoire OpenViking distante reste séparée. Exportez le projet pour en garder une copie.":
    "The local project, its translations and its jobs will be deleted. Remote OpenViking memory remains separate. Export the project to keep a copy.",
  "Supprimer définitivement": "Delete permanently",
  "Seul le propriétaire du livre gère le partage et la suppression.": "Only the book's owner manages sharing and deletion.",
  "Choix terminologiques": "Terminology choices",
  "{accepted} acceptés · {proposals} propositions · {locked} verrouillés":
    "{accepted} accepted · {proposals} proposals · {locked} locked",
  "Terme enregistré. Les passages concernés sont marqués à réévaluer.":
    "Term saved. Affected passages are marked for reevaluation.",
  "Importer un glossaire": "Import a glossary",
  Exporter: "Export",
  "Exporter le glossaire": "Export the glossary",
  "{imported} termes importés, {skipped} ignorés (déjà présents).": "{imported} terms imported, {skipped} skipped (already present).",
  "Rechercher dans le glossaire": "Search glossary",
  "Rechercher un terme…": "Search for a term…",
  Source: "Source",
  Traduction: "Translation",
  Catégorie: "Category",
  Verrouillé: "Locked",
  Accepté: "Accepted",
  "Traduction de {term}": "Translation of {term}",
  "Catégorie de {term}": "Category of {term}",
  "Verrouiller {term}": "Lock {term}",
  "Accepter {term}": "Accept {term}",
  "Enregistrer {term}": "Save {term}",
  "Supprimer {term}": "Delete {term}",
  "Supprimer le terme « {term} » ?": "Delete the term “{term}”?",
  "Les passages concernés seront réévalués sans ce choix.": "Affected passages will be reevaluated without this choice.",
  Supprimer: "Delete",
  "Ajouter un choix humain": "Add a human choice",
  "Nouvelle expression source": "New source expression",
  "Expression source": "Source expression",
  "Nouvelle traduction": "New translation",
  "Ajouter et verrouiller": "Add and lock",
  "Aucun terme pour l’instant.": "No terms yet.",
  "L’analyse propose des termes ; vous pouvez aussi en ajouter ou importer un glossaire.":
    "Analysis suggests terms; you can also add some or import a glossary.",
  "Aucun terme ne correspond.": "No term matches.",
  "Chargement du glossaire…": "Loading glossary…",
  "Validée par un humain": "Validated by a human",
  "Analyse IA — à examiner": "AI analysis — review required",
  "Exporter JSON": "Export JSON",
  "Résumé éditorial": "Editorial summary",
  "Lancez l’analyse pour construire la mémoire du livre.": "Run analysis to build the book memory.",
  Personnages: "Characters",
  Validé: "Validated",
  "À relire": "To review",
  "Modifier la fiche": "Edit profile",
  "Modifier {name}": "Edit {name}",
  "Fiche personnage": "Character profile",
  "Enregistrer et valider": "Save and validate",
  "Nom canonique": "Canonical name",
  "Alias (séparés par des virgules)": "Aliases (comma-separated)",
  Genre: "Gender",
  Pronoms: "Pronouns",
  Description: "Description",
  "Relations (une par ligne)": "Relationships (one per line)",
  "Façon de parler": "Speech style",
  "Registre (tutoiement, vouvoiement…)": "Register (formal, informal…)",
  "Notes de traduction (une par ligne)": "Translation notes (one per line)",
  "Aucun personnage identifié pour l’instant.": "No character identified yet.",
  "Éditer la Book Bible structurée (JSON)": "Edit the structured Book Bible (JSON)",
  "Enregistrer et valider la Book Bible": "Save and validate Book Bible",
  "Le JSON de la Book Bible est invalide : {error}": "The Book Bible JSON is invalid: {error}",
  Ton: "Tone",
  "Style narratif": "Narrative style",
  "Point de vue": "Point of view",
  Temps: "Tense",
  "Consignes de traduction": "Translation guidelines",
  Relations: "Relationships",
  Lieux: "Locations",
  "Titres honorifiques": "Honorifics",
  "Jeux de mots connus": "Known wordplay",
  "Genre littéraire": "Genre",
  "Relecture ciblée": "Targeted review",
  "Les signaux automatiques orientent la relecture ; ils ne mesurent pas seuls la qualité littéraire.":
    "Automated signals guide review; they do not alone measure literary quality.",
  "Contrôle global de cohérence": "Global consistency check",
  "Contrôle de cohérence mis en file.": "Consistency check queued.",
  Traité: "Resolved",
  "Passage {id}": "Passage {id}",
  "Marquer comme traité": "Mark as resolved",
  "Aucun problème enregistré par les contrôles exécutés.": "No issue recorded by completed checks.",
  Observabilité: "Observability",
  "Estimations du travail actif": "Active work estimates",
  "Temps restant estimé": "Estimated remaining time",
  "Coût tarifaire consommé": "Consumed list-price cost",
  "Coût restant estimé": "Estimated remaining cost",
  "Confiance de l’estimation": "Estimate confidence",
  "Estimations fondées sur les requêtes réussies de l’étape active et les tarifs actuellement configurés. Elles sont indisponibles tant que l’échantillon est insuffisant.":
    "Estimates are based on successful requests from the active stage and currently configured rates. They are unavailable while the sample is insufficient.",
  "Tokens d’entrée perdus": "Wasted input tokens",
  "{share} des tokens d’entrée, perdus en réponses invalides ou en erreurs":
    "{share} of input tokens, lost to invalid responses or errors",
  "Mesures du livre": "Book metrics",
  Heure: "Time",
  Opération: "Operation",
  Modèle: "Model",
  État: "Status",
  Durée: "Duration",
  "Entrée / sortie": "Input / output",
  "Débit moyen*": "Average throughput*",
  Essai: "Attempt",
  Cache: "Cache",
  "* Tokens de sortie / durée totale de requête, préremplissage inclus. Les comptes absents du provider sont enregistrés à zéro, pas inventés.":
    "* Output tokens / total request duration, including prefill. Counts missing from the provider are recorded as zero, not invented.",
  "Requêtes précédentes": "Previous requests",
  "Requêtes suivantes": "Next requests",
  "Détail de la requête": "Request details",
  "Aucune requête enregistrée pour ce livre.": "No request recorded for this book.",
  "Requêtes au modèle": "Model requests",
  "Chargement des requêtes…": "Loading requests…",
  "{value} t/s": "{value} t/s",
});

const bibleLabels: Record<string, string> = {
  genre: "Genre littéraire",
  tone: "Ton",
  narrative_style: "Style narratif",
  narrative_point_of_view: "Point de vue",
  tense: "Temps",
  translation_guidelines: "Consignes de traduction",
  relationships: "Relations",
  locations: "Lieux",
  honorifics: "Titres honorifiques",
  known_wordplay: "Jeux de mots connus",
};

const metricLabels: Record<string, string> = {
  requests: "Requêtes",
  prompt_tokens: "Tokens d’entrée",
  completion_tokens: "Tokens de sortie",
};
registerTranslations({ Requêtes: "Requests", "Tokens d’entrée": "Input tokens", "Tokens de sortie": "Output tokens" });

interface Member {
  user_id: string;
  username: string;
  role: "reader" | "editor";
}

export function ProjectSettings({
  project,
  user,
  run,
  refresh,
}: {
  project: Project;
  user: User;
  run: Run;
  refresh: () => void;
}) {
  const { t, tp } = useI18n();
  const { confirm } = useDialogs();
  const [value, setValue] = useState(project);
  const [providers, setProviders] = useState<ProviderSummary[]>([]);
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState(false);
  const owner = project.owner_id === user.id;
  // The setting only exists on servers that support it: never send an unknown field.
  const supportsMemory = typeof project.translation_memory === "boolean";
  useEffect(() => {
    void run.background(async () => setProviders(await api("/providers")));
  }, [run]);
  const field = <K extends keyof Project>(name: K, next: Project[K]) => {
    setSaved("");
    setValue((current) => ({ ...current, [name]: next }));
  };
  return (
    <div className="settings-layout">
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          void run(async () => {
            const {
              title,
              author,
              series_name,
              volume_number,
              source_language,
              target_language,
              provider_id,
              quality,
              context_backend,
              instructions,
              translation_memory,
            } = value;
            await send(
              `/projects/${project.id}`,
              {
                title,
                author,
                series_name,
                volume_number,
                source_language,
                target_language,
                provider_id,
                quality,
                context_backend,
                instructions,
                ...(supportsMemory ? { translation_memory } : {}),
              },
              "PUT",
            );
            refresh();
            setSaved(t("Configuration enregistrée."));
          }).finally(() => setBusy(false));
        }}
      >
        <Card
          title={t("Livre")}
          description={t("{words} mots · {sections} sections · {images} images · {size} Mo", {
            words: project.book_info.words,
            sections: project.stats.chapters,
            images: project.book_info.images,
            size: formatNumber(project.book_info.size / 1024 ** 2, { maximumFractionDigits: 1 }),
          })}
        >
          <FormGrid>
            <Field label={t("Titre à l’export")}>
              <Input value={value.title} onChange={(e) => field("title", e.target.value)} />
            </Field>
            <Field label={t("Auteur")}>
              <Input value={value.author} onChange={(e) => field("author", e.target.value)} />
            </Field>
            <Field label={t("Série")}>
              <Input value={value.series_name} onChange={(e) => field("series_name", e.target.value)} />
            </Field>
            <Field label={t("Numéro du volume")}>
              <Input
                type="number"
                min="1"
                max="10000"
                value={value.volume_number ?? ""}
                onChange={(e) => field("volume_number", e.target.value ? Number(e.target.value) : null)}
                placeholder="1"
              />
            </Field>
          </FormGrid>
        </Card>
        <Card title={t("Langues")}>
          <FormGrid>
            <Field label={t("Langue source")}>
              <Input value={value.source_language} onChange={(e) => field("source_language", e.target.value)} />
            </Field>
            <Field label={t("Langue cible (code BCP 47)")}>
              <Input
                value={value.target_language}
                onChange={(e) => field("target_language", e.target.value)}
                placeholder="fr"
              />
            </Field>
          </FormGrid>
        </Card>
        <Card title={t("Stratégie du livre")}>
          <div className="stack">
            <FormGrid columns={3}>
              <Field label="Provider">
                <Select value={value.provider_id || ""} onChange={(e) => field("provider_id", e.target.value || null)}>
                  <option value="">{t("Choisir…")}</option>
                  {providers.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} · {p.model}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label={t("Qualité")}>
                <Select value={value.quality} onChange={(e) => field("quality", e.target.value)}>
                  <option value="fast">{t("Rapide")}</option>
                  <option value="normal">{t("Normal · vérification")}</option>
                  <option value="high">{t("Haute qualité · critique & révision")}</option>
                  <option value="maximum">{t("Maximum · polissage")}</option>
                </Select>
              </Field>
              <Field label={t("Moteur de contexte")}>
                <Select value={value.context_backend} onChange={(e) => field("context_backend", e.target.value)}>
                  <option value="internal">{t("Interne — recommandé")}</option>
                  <option value="hybrid">Hybrid</option>
                  <option value="openviking">OpenViking</option>
                </Select>
              </Field>
            </FormGrid>
            {supportsMemory && (
              <Switch
                label={t("Mémoire de traduction")}
                description={
                  <>
                    {t("Réutiliser les passages identiques déjà traduits (vos livres, même paire de langues)")}
                    {!!project.stats.translation_memory_reused && (
                      <>
                        {" · "}
                        {tp(
                          project.stats.translation_memory_reused,
                          "{count} passage repris de la mémoire de traduction",
                          "{count} passages repris de la mémoire de traduction",
                        )}
                      </>
                    )}
                  </>
                }
                checked={!!value.translation_memory}
                onChange={(e) => field("translation_memory", e.target.checked)}
              />
            )}
            <Field label={t("Instructions globales")}>
              <TextArea
                rows={5}
                value={value.instructions}
                onChange={(e) => field("instructions", e.target.value)}
                placeholder={t("Conserver les suffixes -san, -chan, -sama. Tutoyer entre Alice et Bob…")}
              />
            </Field>
          </div>
        </Card>
        <div className="form-actions sticky-actions">
          <Button type="submit" variant="primary" loading={busy}>
            {t("Enregistrer les réglages")}
          </Button>
          {saved && (
            <span role="status" className="tone-text-success">
              {saved}
            </span>
          )}
        </div>
      </form>
      <aside className="stack">
        {owner ? (
          <Members project={project} run={run} />
        ) : (
          <Callout tone="neutral">{t("Seul le propriétaire du livre gère le partage et la suppression.")}</Callout>
        )}
        {!!project.book_info.untranslated && !!Object.keys(project.book_info.untranslated).length && (
          <Card
            title={t("Conservés tels quels à l’import")}
            description={t("Ces éléments ne sont pas envoyés au modèle et restent identiques dans l’EPUB exporté.")}
          >
            <ul className="untranslated-list">
              {Object.entries(project.book_info.untranslated).map(([kind, entry]) => (
                <li key={kind}>
                  <Badge>{t("{kind} · {count}", { kind, count: entry.count })}</Badge>
                  <span className="subtle">{entry.resources.slice(0, 4).join(", ")}{entry.resources.length > 4 ? "…" : ""}</span>
                </li>
              ))}
            </ul>
          </Card>
        )}
        <details className="disclosure">
          <summary>{t("Rapport de validation à l’import")}</summary>
          <pre>{JSON.stringify(project.book_info.validation, null, 2)}</pre>
        </details>
        {owner && (
          <Card title={t("Zone de danger")} className="danger-zone">
            <Button
              variant="danger"
              icon="trash"
              onClick={() =>
                void (async () => {
                  const accepted = await confirm({
                    title: t("Supprimer ce projet ?"),
                    message: t(
                      "Le projet local, ses traductions et ses travaux seront supprimés. La mémoire OpenViking distante reste séparée. Exportez le projet pour en garder une copie.",
                    ),
                    confirmLabel: t("Supprimer définitivement"),
                    tone: "danger",
                  });
                  if (!accepted) return;
                  await run(async () => {
                    await api(`/projects/${project.id}?stop_jobs=true`, { method: "DELETE" });
                    location.hash = "library";
                  });
                })()
              }
            >
              {t("Supprimer ce projet")}
            </Button>
          </Card>
        )}
      </aside>
    </div>
  );
}

function Members({ project, run }: { project: Project; run: Run }) {
  const { t } = useI18n();
  const { confirm } = useDialogs();
  const [members, setMembers] = useState<Member[] | null>(null);
  const [username, setUsername] = useState("");
  const [role, setRole] = useState("reader");
  const [notice, setNotice] = useState("");
  const load = useCallback(async () => setMembers(await api<Member[]>(`/projects/${project.id}/members`)), [project.id]);
  useEffect(() => {
    void run.background(load);
  }, [run, load]);
  return (
    <Card
      title={t("Partage")}
      description={t("Les lecteurs consultent le livre ; les éditeurs peuvent aussi corriger et lancer des travaux.")}
    >
      <div className="stack">
        {members === null ? (
          <LoadingBlock label={t("Membres")} lines={2} />
        ) : members.length ? (
          <ul className="member-list" aria-label={t("Membres")}>
            {members.map((member) => (
              <li key={member.user_id}>
                <span className="avatar" aria-hidden="true">
                  {member.username.slice(0, 1).toUpperCase()}
                </span>
                <span className="grow">{member.username}</span>
                <Badge tone={member.role === "editor" ? "accent" : "neutral"}>
                  {member.role === "editor" ? t("Éditeur") : t("Lecteur")}
                </Badge>
                <IconButton
                  icon="x"
                  size="sm"
                  label={t("Révoquer l’accès de {user}", { user: member.username })}
                  onClick={() =>
                    void (async () => {
                      const accepted = await confirm({
                        title: t("Révoquer l’accès de {user} ?", { user: member.username }),
                        message: t("{user} ne verra plus ce livre.", { user: member.username }),
                        confirmLabel: t("Révoquer"),
                        tone: "danger",
                      });
                      if (!accepted) return;
                      await run(async () => {
                        await api(`/projects/${project.id}/members/${member.user_id}`, { method: "DELETE" });
                        await load();
                      });
                    })()
                  }
                />
              </li>
            ))}
          </ul>
        ) : (
          <p className="subtle">{t("Aucun membre : ce livre n’est visible que par vous.")}</p>
        )}
        <form
          className="share-form"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await send(`/projects/${project.id}/members`, { username, role });
              setNotice(t("Accès accordé à {user}.", { user: username }));
              setUsername("");
              await load();
            });
          }}
        >
          <Field label={t("Utilisateur à inviter")}>
            <Input
              placeholder={t("Nom d’utilisateur existant")}
              value={username}
              onChange={(e) => {
                setUsername(e.target.value);
                setNotice("");
              }}
              required
            />
          </Field>
          <Field label={t("Rôle sur le projet")}>
            <Select value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="reader">{t("Lecteur")}</option>
              <option value="editor">{t("Éditeur")}</option>
            </Select>
          </Field>
          <Button type="submit" icon="users">
            {t("Partager")}
          </Button>
        </form>
        {notice && (
          <p role="status" className="tone-text-success">
            {notice}
          </p>
        )}
      </div>
    </Card>
  );
}

export function Glossary({ project, run, tick }: { project: Project; run: Run; tick: number }) {
  const { t } = useI18n();
  const { confirm } = useDialogs();
  const [terms, setTerms] = useState<Term[] | null>(null);
  const [search, setSearch] = useState("");
  const [source, setSource] = useState("");
  const [translation, setTranslation] = useState("");
  const [message, setMessage] = useState("");
  const load = useCallback(async () => setTerms(await api(`/projects/${project.id}/glossary`)), [project.id]);
  useEffect(() => {
    void run.background(load);
  }, [load, tick, run]);
  async function save(term: Term) {
    const { source, translation, category, description, locked, accepted } = term;
    await send(
      `/projects/${project.id}/glossary/${term.id}`,
      { source, translation, category, description, locked, accepted },
      "PUT",
    );
    setMessage(t("Terme enregistré. Les passages concernés sont marqués à réévaluer."));
    await load();
  }
  const list = terms || [];
  const update = (id: string, change: Partial<Term>) =>
    setTerms((all) => (all || []).map((v) => (v.id === id ? { ...v, ...change } : v)));
  const filtered = list.filter((term) =>
    `${term.source} ${term.translation}`.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <section className="stack">
      <div className="panel-header">
        <div>
          <h2>{t("Choix terminologiques")}</h2>
          <p className="muted">
            {t("{accepted} acceptés · {proposals} propositions · {locked} verrouillés", {
              accepted: list.filter((term) => term.accepted).length,
              proposals: list.filter((term) => !term.accepted).length,
              locked: list.filter((term) => term.locked).length,
            })}
          </p>
        </div>
        <div className="panel-actions">
          <FileButton
            label={t("Importer un glossaire")}
            accept=".json,.csv,.tbx,.xml,.tsv,.txt"
            onFiles={([file]) =>
              void run(async () => {
                const data = new FormData();
                data.append("file", file);
                const result = await api<{ imported: number; skipped: number }>(
                  `/projects/${project.id}/glossary/import`,
                  { method: "POST", body: data },
                );
                setMessage(t("{imported} termes importés, {skipped} ignorés (déjà présents).", result));
                await load();
              })
            }
          />
          <Menu
            label={t("Exporter le glossaire")}
            trigger={(props) => (
              <Button {...props} icon="download" iconAfter="chevronDown">
                {t("Exporter")}
              </Button>
            )}
            items={(["json", "csv", "tbx"] as const).map((format) => ({
              label: format.toUpperCase(),
              icon: "file" as const,
              onSelect: () =>
                void run(() => downloadGet(`/projects/${project.id}/glossary/export/${format}`, `glossary.${format}`)),
            }))}
          />
        </div>
      </div>
      {message && (
        <Callout tone="success" role="status">
          {message}
        </Callout>
      )}
      <Card padded={false}>
        <div className="card-toolbar">
          <label className="grow glossary-search">
            <span className="sr-only">{t("Rechercher dans le glossaire")}</span>
            <SearchInput placeholder={t("Rechercher un terme…")} value={search} onChange={(e) => setSearch(e.target.value)} />
          </label>
        </div>
        {terms === null ? (
          <div className="card-inset">
            <LoadingBlock label={t("Chargement du glossaire…")} />
          </div>
        ) : !list.length ? (
          <EmptyState
            compact
            icon="book"
            title={t("Aucun terme pour l’instant.")}
            description={t("L’analyse propose des termes ; vous pouvez aussi en ajouter ou importer un glossaire.")}
          />
        ) : !filtered.length ? (
          <EmptyState compact icon="search" title={t("Aucun terme ne correspond.")} />
        ) : (
          <Table className="glossary-table">
            <thead>
              <tr>
                <th>{t("Source")}</th>
                <th>{t("Traduction")}</th>
                <th>{t("Catégorie")}</th>
                <th className="cell-tight">{t("Verrouillé")}</th>
                <th className="cell-tight">{t("Accepté")}</th>
                <th className="cell-tight" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((term) => (
                <tr key={term.id} className={!term.accepted ? "is-proposal" : undefined}>
                  <td className="glossary-source">
                    {term.source}
                    {!term.accepted && <Badge tone="warning">{t("À relire")}</Badge>}
                  </td>
                  <td>
                    <Input
                      aria-label={t("Traduction de {term}", { term: term.source })}
                      value={term.translation}
                      onChange={(e) => update(term.id, { translation: e.target.value })}
                    />
                  </td>
                  <td>
                    <Input
                      aria-label={t("Catégorie de {term}", { term: term.source })}
                      value={term.category}
                      onChange={(e) => update(term.id, { category: e.target.value })}
                    />
                  </td>
                  <td className="cell-tight center">
                    <input
                      aria-label={t("Verrouiller {term}", { term: term.source })}
                      type="checkbox"
                      checked={term.locked}
                      onChange={(e) => update(term.id, { locked: e.target.checked })}
                    />
                  </td>
                  <td className="cell-tight center">
                    <input
                      aria-label={t("Accepter {term}", { term: term.source })}
                      type="checkbox"
                      checked={term.accepted}
                      onChange={(e) => update(term.id, { accepted: e.target.checked })}
                    />
                  </td>
                  <td className="cell-tight">
                    <div className="row nowrap">
                      <IconButton
                        icon="check"
                        size="sm"
                        label={t("Enregistrer {term}", { term: term.source })}
                        onClick={() => void run(() => save(term))}
                      />
                      <IconButton
                        icon="trash"
                        size="sm"
                        label={t("Supprimer {term}", { term: term.source })}
                        onClick={() =>
                          void (async () => {
                            const accepted = await confirm({
                              title: t("Supprimer le terme « {term} » ?", { term: term.source }),
                              message: t("Les passages concernés seront réévalués sans ce choix."),
                              confirmLabel: t("Supprimer"),
                              tone: "danger",
                            });
                            if (!accepted) return;
                            await run(async () => {
                              await api(`/projects/${project.id}/glossary/${term.id}`, { method: "DELETE" });
                              await load();
                            });
                          })()
                        }
                      />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      <Card title={t("Ajouter un choix humain")}>
        <form
          className="inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await send(`/projects/${project.id}/glossary`, { source, translation, accepted: true, locked: true });
              setSource("");
              setTranslation("");
              await load();
            });
          }}
        >
          <Field label={t("Nouvelle expression source")}>
            <Input placeholder={t("Expression source")} value={source} onChange={(e) => setSource(e.target.value)} required />
          </Field>
          <Field label={t("Nouvelle traduction")}>
            <Input
              placeholder={t("Traduction")}
              value={translation}
              onChange={(e) => setTranslation(e.target.value)}
              required
            />
          </Field>
          <Button type="submit" variant="primary" icon="lock">
            {t("Ajouter et verrouiller")}
          </Button>
        </form>
      </Card>
    </section>
  );
}

interface CharacterData {
  canonical_name?: string;
  aliases?: string[];
  proposed_aliases?: string[];
  gender?: string;
  pronouns?: string;
  role?: string;
  description?: string;
  relationships?: string[];
  speech_style?: string;
  formal_or_informal?: string;
  translation_notes?: string[];
}

interface BibleData {
  bible: Record<string, unknown>;
  validated: boolean;
  entities: { id: string; name: string; data: CharacterData; validated: boolean }[];
}

export function Bible({
  project,
  run,
  refresh,
  tick,
}: {
  project: Project;
  run: Run;
  refresh: () => void;
  tick: number;
}) {
  const { t } = useI18n();
  const [text, setText] = useState(JSON.stringify(project.bible || {}, null, 2));
  const [dirty, setDirty] = useState(false);
  const [jsonError, setJsonError] = useState("");
  const [data, setData] = useState<BibleData | null>(null);
  const [editing, setEditing] = useState<BibleData["entities"][number] | null>(null);
  useEffect(() => {
    void run.background(async () => {
      setData(await api(`/projects/${project.id}/bible`));
    });
    if (!dirty) setText(JSON.stringify(project.bible || {}, null, 2));
  }, [project.id, project.updated_at, run, tick]);
  const bible = data?.bible || {};
  const fields = Object.keys(bibleLabels).filter((key) => (Array.isArray(bible[key]) ? (bible[key] as unknown[]).length : bible[key]));
  return (
    <div className="bible-layout">
      <div className="stack">
        <div className="panel-header">
          <div>
            <h2>Book Bible</h2>
            <p>
              {data && (
                <Badge tone={data.validated ? "success" : "warning"} dot>
                  {data.validated ? t("Validée par un humain") : t("Analyse IA — à examiner")}
                </Badge>
              )}
            </p>
          </div>
          <div className="panel-actions">
            <Button icon="download" onClick={() => download("book-bible.json", project.bible)}>
              {t("Exporter JSON")}
            </Button>
          </div>
        </div>
        {data === null ? (
          <LoadingBlock label="Book Bible" lines={5} />
        ) : (
          <>
            <Card title={t("Résumé éditorial")}>
              <p className="book-text bible-summary">
                {String(bible.summary || t("Lancez l’analyse pour construire la mémoire du livre."))}
              </p>
              {!!fields.length && (
                <dl className="definition-grid">
                  {fields.map((key) => (
                    <div key={key}>
                      <dt>{t(bibleLabels[key])}</dt>
                      <dd>{Array.isArray(bible[key]) ? (bible[key] as string[]).join(" · ") : String(bible[key])}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </Card>
            <Card title={t("Personnages")} padded={false}>
              {data.entities.length ? (
                <ul className="character-list">
                  {data.entities.map((entity) => (
                    <li key={entity.id}>
                      <span className="avatar" aria-hidden="true">
                        {entity.name.slice(0, 1).toUpperCase()}
                      </span>
                      <div className="grow">
                        <div className="row">
                          <strong>{entity.name}</strong>
                          <Badge tone={entity.validated ? "success" : "neutral"}>
                            {entity.validated ? t("Validé") : t("À relire")}
                          </Badge>
                        </div>
                        <p className="subtle">
                          {[entity.data.role, entity.data.description].filter(Boolean).join(" — ")}
                        </p>
                        {!!entity.data.aliases?.length && <p className="subtle">{entity.data.aliases.join(" · ")}</p>}
                      </div>
                      <Button
                        size="sm"
                        variant="ghost"
                        icon="edit"
                        aria-label={t("Modifier {name}", { name: entity.name })}
                        onClick={() => setEditing(entity)}
                      >
                        {t("Modifier la fiche")}
                      </Button>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState compact icon="users" title={t("Aucun personnage identifié pour l’instant.")} />
              )}
            </Card>
          </>
        )}
        <details className="disclosure">
          <summary>{t("Éditer la Book Bible structurée (JSON)")}</summary>
          <div className="stack">
            <TextArea
              className="code-editor"
              aria-label="Book Bible JSON"
              rows={16}
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                setDirty(true);
                setJsonError("");
              }}
            />
            {jsonError && <Callout tone="danger">{jsonError}</Callout>}
            <div>
              <Button
                variant="primary"
                onClick={() => {
                  let parsed: unknown;
                  try {
                    parsed = JSON.parse(text);
                  } catch (error) {
                    setJsonError(t("Le JSON de la Book Bible est invalide : {error}", { error: String(error) }));
                    return;
                  }
                  void run(async () => {
                    await send(`/projects/${project.id}/bible`, parsed, "PUT");
                    setData(await api(`/projects/${project.id}/bible`));
                    setDirty(false);
                    refresh();
                  });
                }}
              >
                {t("Enregistrer et valider la Book Bible")}
              </Button>
            </div>
          </div>
        </details>
      </div>
      <aside>
        <MemoryPanel pid={project.id} run={run} refreshKey={tick} />
      </aside>
      {editing && (
        <CharacterForm
          entity={editing}
          close={() => setEditing(null)}
          save={(profile) =>
            run(async () => {
              await send(`/projects/${project.id}/characters/${editing.id}`, profile, "PUT");
              setEditing(null);
              setData(await api(`/projects/${project.id}/bible`));
              refresh();
            })
          }
        />
      )}
    </div>
  );
}

function CharacterForm({
  entity,
  close,
  save,
}: {
  entity: BibleData["entities"][number];
  close: () => void;
  save: (profile: Required<CharacterData>) => Promise<void>;
}) {
  const { t } = useI18n();
  const data = entity.data;
  const [form, setForm] = useState({
    canonical_name: data.canonical_name || entity.name,
    aliases: (data.aliases || []).join(", "),
    gender: data.gender || "",
    pronouns: data.pronouns || "",
    role: data.role || "",
    description: data.description || "",
    relationships: (data.relationships || []).join("\n"),
    speech_style: data.speech_style || "",
    formal_or_informal: data.formal_or_informal || "",
    translation_notes: (data.translation_notes || []).join("\n"),
  });
  const [busy, setBusy] = useState(false);
  const set = (name: keyof typeof form) => (event: { target: { value: string } }) =>
    setForm((current) => ({ ...current, [name]: event.target.value }));
  const list = (value: string, separator: RegExp) =>
    value
      .split(separator)
      .map((item) => item.trim())
      .filter(Boolean);
  return (
    <Dialog
      open
      onClose={close}
      size="lg"
      title={t("Fiche personnage")}
      description={entity.name}
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            {t("Annuler")}
          </Button>
          <Button type="submit" form="character-form" variant="primary" loading={busy}>
            {t("Enregistrer et valider")}
          </Button>
        </>
      }
    >
      <form
        id="character-form"
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          setBusy(true);
          void save({
            canonical_name: form.canonical_name,
            aliases: list(form.aliases, /,/),
            proposed_aliases: data.proposed_aliases || [],
            gender: form.gender,
            pronouns: form.pronouns,
            role: form.role,
            description: form.description,
            relationships: list(form.relationships, /\n/),
            speech_style: form.speech_style,
            formal_or_informal: form.formal_or_informal,
            translation_notes: list(form.translation_notes, /\n/),
          }).finally(() => setBusy(false));
        }}
      >
        <FormGrid>
          <Field label={t("Nom canonique")}>
            <Input data-autofocus required value={form.canonical_name} onChange={set("canonical_name")} />
          </Field>
          <Field label={t("Alias (séparés par des virgules)")}>
            <Input value={form.aliases} onChange={set("aliases")} />
          </Field>
          <Field label={t("Genre")}>
            <Input value={form.gender} onChange={set("gender")} />
          </Field>
          <Field label={t("Pronoms")}>
            <Input value={form.pronouns} onChange={set("pronouns")} />
          </Field>
          <Field label={t("Rôle")}>
            <Input value={form.role} onChange={set("role")} />
          </Field>
          <Field label={t("Registre (tutoiement, vouvoiement…)")}>
            <Input value={form.formal_or_informal} onChange={set("formal_or_informal")} />
          </Field>
          <Field label={t("Description")} className="span-all">
            <TextArea rows={3} value={form.description} onChange={set("description")} />
          </Field>
          <Field label={t("Façon de parler")} className="span-all">
            <Input value={form.speech_style} onChange={set("speech_style")} />
          </Field>
          <Field label={t("Relations (une par ligne)")}>
            <TextArea rows={3} value={form.relationships} onChange={set("relationships")} />
          </Field>
          <Field label={t("Notes de traduction (une par ligne)")}>
            <TextArea rows={3} value={form.translation_notes} onChange={set("translation_notes")} />
          </Field>
        </FormGrid>
      </form>
    </Dialog>
  );
}

export function Quality({ project, run, tick }: { project: Project; run: Run; tick: number }) {
  const { t } = useI18n();
  const toast = useToast();
  const [issues, setIssues] = useState<Issue[] | null>(null);
  useEffect(() => {
    void run.background(async () => setIssues(await api(`/projects/${project.id}/issues`)));
  }, [project.id, tick, run]);
  return (
    <section className="stack">
      <div className="panel-header">
        <div>
          <h2>{t("Relecture ciblée")}</h2>
          <p className="muted">
            {t("Les signaux automatiques orientent la relecture ; ils ne mesurent pas seuls la qualité littéraire.")}
          </p>
        </div>
        <div className="panel-actions">
          <Button
            icon="sparkles"
            onClick={() =>
              void run(async () => {
                await send(`/projects/${project.id}/jobs`, { operation: "consistency" });
                toast(t("Contrôle de cohérence mis en file."));
              })
            }
          >
            {t("Contrôle global de cohérence")}
          </Button>
        </div>
      </div>
      {issues === null ? (
        <LoadingBlock label={t("Relecture ciblée")} />
      ) : issues.length ? (
        <Card padded={false}>
          <ul className="issue-list">
            {issues.map((issue) => (
              <li key={issue.id} className={issue.resolved ? "is-resolved" : undefined}>
                {issue.resolved ? (
                  <Badge tone="success">{t("Traité")}</Badge>
                ) : (
                  <StatusPill status={issue.severity} label={issue.severity} />
                )}
                <div className="grow">
                  <strong>{issue.code}</strong>
                  <p>{issue.message}</p>
                  {issue.segment_id && <small className="subtle">{t("Passage {id}", { id: issue.segment_id })}</small>}
                </div>
                {!issue.resolved && (
                  <Button
                    size="sm"
                    icon="check"
                    onClick={() =>
                      void run(async () => {
                        await send(`/issues/${issue.id}/resolve`);
                        setIssues(await api(`/projects/${project.id}/issues`));
                      })
                    }
                  >
                    {t("Marquer comme traité")}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </Card>
      ) : (
        <Card>
          <EmptyState icon="check" title={t("Aucun problème enregistré par les contrôles exécutés.")} />
        </Card>
      )}
    </section>
  );
}

export function Observability({ project, run, tick }: { project: Project; run: Run; tick: number }) {
  const { t } = useI18n();
  const [requests, setRequests] = useState<LLMRequest[] | null>(null);
  const [metrics, setMetrics] = useState<Record<string, number>>({});
  const [selected, setSelected] = useState<LLMRequest | null>(null);
  const [offset, setOffset] = useState(0);
  const progress = projectProgress(project);
  useEffect(() => {
    void run.background(async () => {
      const [r, m] = await Promise.all([
        api<LLMRequest[]>(`/projects/${project.id}/requests?offset=${offset}`),
        api<Record<string, number>>(`/projects/${project.id}/metrics`),
      ]);
      setRequests(r);
      setMetrics(m);
    });
  }, [project.id, tick, run, offset]);
  const { wasted_input_tokens: wasted, wasted_share: wastedShare, ...otherMetrics } = metrics;
  return (
    <section className="stack">
      <div className="panel-header">
        <div>
          <h2>{t("Observabilité")}</h2>
          <p className="muted">
            {t(
              "Estimations fondées sur les requêtes réussies de l’étape active et les tarifs actuellement configurés. Elles sont indisponibles tant que l’échantillon est insuffisant.",
            )}
          </p>
        </div>
      </div>
      <div className="stat-grid" role="group" aria-label={t("Estimations du travail actif")}>
        <Stat label={t("Temps restant estimé")} value={duration(progress.estimate.remaining_seconds)} />
        <Stat
          label={t("Coût tarifaire consommé")}
          value={formatNumber(progress.estimate.spent_cost, { maximumFractionDigits: 2 })}
        />
        <Stat
          label={t("Coût restant estimé")}
          value={
            progress.estimate.remaining_cost === null
              ? "—"
              : formatNumber(progress.estimate.remaining_cost, { maximumFractionDigits: 2 })
          }
        />
        <Stat label={t("Confiance de l’estimation")} value={progress.estimate.confidence} />
      </div>
      <div className="stat-grid" role="group" aria-label={t("Mesures du livre")}>
        {Object.entries(otherMetrics).map(([key, value]) => (
          <Stat key={key} label={metricLabels[key] ? t(metricLabels[key]) : key} value={formatNumber(value)} />
        ))}
        {wasted !== undefined && (
          <Stat
            label={t("Tokens d’entrée perdus")}
            value={formatNumber(wasted)}
            tone={wastedShare && wastedShare > 0.1 ? "warning" : undefined}
            hint={
              wastedShare !== undefined
                ? t("{share} des tokens d’entrée, perdus en réponses invalides ou en erreurs", {
                    share: formatPercent(wastedShare * 100),
                  })
                : undefined
            }
          />
        )}
      </div>
      <Card title={t("Requêtes au modèle")} padded={false}>
        {requests === null ? (
          <div className="card-inset">
            <LoadingBlock label={t("Chargement des requêtes…")} />
          </div>
        ) : requests.length ? (
          <Table>
            <thead>
              <tr>
                <th>{t("Heure")}</th>
                <th>{t("Opération")}</th>
                <th>{t("Modèle")}</th>
                <th>{t("État")}</th>
                <th className="num">{t("Durée")}</th>
                <th className="num">{t("Entrée / sortie")}</th>
                <th className="num">{t("Débit moyen*")}</th>
                <th className="num">{t("Essai")}</th>
              </tr>
            </thead>
            <tbody>
              {requests.map((r) => (
                <tr key={r.id}>
                  <td className="muted tabular nowrap">{date(r.created_at)}</td>
                  <td>
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => void run(async () => setSelected(await api(`/requests/${r.id}`)))}
                    >
                      {r.operation}
                    </button>{" "}
                    {r.cached && <Badge>{t("Cache")}</Badge>}
                  </td>
                  <td className="muted">{r.model}</td>
                  <td>
                    <StatusPill status={r.status} label={labels[r.status]} />
                  </td>
                  <td className="num">{formatNumber(r.duration, { maximumFractionDigits: 1 })} s</td>
                  <td className="num">
                    {formatNumber(r.prompt_tokens)} / {formatNumber(r.completion_tokens)}
                  </td>
                  <td className="num">
                    {r.duration > 0
                      ? t("{value} t/s", {
                          value: formatNumber(r.completion_tokens / r.duration, { maximumFractionDigits: 1 }),
                        })
                      : "—"}
                  </td>
                  <td className="num">{r.attempt}/5</td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <EmptyState compact icon="chart" title={t("Aucune requête enregistrée pour ce livre.")} />
        )}
        <div className="card-footer">
          <span className="subtle">
            {t(
              "* Tokens de sortie / durée totale de requête, préremplissage inclus. Les comptes absents du provider sont enregistrés à zéro, pas inventés.",
            )}
          </span>
          <span className="grow" />
          <Button size="sm" variant="ghost" icon="chevronLeft" disabled={!offset} onClick={() => setOffset((v) => Math.max(0, v - 100))}>
            {t("Requêtes précédentes")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            iconAfter="chevronRight"
            disabled={(requests?.length || 0) < 100}
            onClick={() => setOffset((v) => v + 100)}
          >
            {t("Requêtes suivantes")}
          </Button>
        </div>
      </Card>
      {selected && (
        <Dialog
          open
          variant="sheet"
          size="lg"
          onClose={() => setSelected(null)}
          ariaLabel={t("Détail de la requête")}
          title={selected.operation}
        >
          <RequestDetails request={selected} />
        </Dialog>
      )}
    </section>
  );
}

