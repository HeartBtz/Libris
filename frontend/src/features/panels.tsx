import { useEffect, useState } from "react";
import { api, date, download, labels, number, send } from "../api";
import { registerTranslations, useI18n } from "../i18n";
import type { Issue, LLMRequest, Project, Provider, Run, Term } from "../types";
import { RequestDetails } from "./Editor";
import { MemoryPanel } from "./MemoryPanel";
import { duration, projectProgress } from "./progress";

const translations: Record<string, string> = {
  "Stratégie du livre": "Book strategy",
  "Choix terminologiques": "Terminology choices",
  "Observabilité": "Observability",
  "mots · {sections} sections / documents · {images} images · {size} Mo": "words · {sections} sections / documents · {images} images · {size} MB", "Titre à l’export": "Export title", "Auteur": "Author", "Série": "Series", "Numéro du volume": "Volume number", "Langue source": "Source language", "Langue cible (code BCP 47)": "Target language (BCP 47 code)", "Choisir…": "Choose…", "Qualité": "Quality", "Rapide": "Fast", "Normal · vérification": "Normal · verification", "Haute qualité · critique & révision": "High quality · critique & revision", "Maximum · polishing": "Maximum · polishing", "Moteur de contexte": "Context engine", "Internal — recommandé": "Internal — recommended", "Instructions globales": "Global instructions", "Conserver les suffixes -san, -chan, -sama. Tutoyer entre Alice et Bob…": "Preserve -san, -chan, and -sama suffixes. Use informal address between Alice and Bob…", "Enregistrer": "Save", "Rapport de validation à l’import": "Import validation report", "Partager ce projet": "Share this project", "Accès accordé.": "Access granted.", "Utilisateur à inviter": "User to invite", "Utilisateur existant": "Existing user", "Rôle sur le projet": "Project role", "Lecteur": "Reader", "Éditeur": "Editor", "Partager": "Share", "Supprimer le projet local, ses traductions et arrêter ses travaux ? La mémoire OpenViking distante reste séparée. Exportez le projet pour conserver une copie.": "Delete the local project, its translations, and stop its work? Remote OpenViking memory remains separate. Export the project to retain a copy.", "Supprimer ce projet": "Delete this project",
  "Terme enregistré. Les passages concernés sont marqués à réévaluer.": "Term saved. Affected segments are marked for reevaluation.", "{accepted} acceptés · {proposals} propositions · {locked} verrouillés": "{accepted} accepted · {proposals} proposals · {locked} locked", "Importer": "Import", "Rechercher dans le glossaire": "Search glossary", "Rechercher un terme…": "Search for a term…", "Source": "Source", "Traduction": "Translation", "Catégorie": "Category", "Verrouillé": "Locked", "Accepté": "Accepted", "Traduction de {term}": "Translation of {term}", "Catégorie de {term}": "Category of {term}", "Verrouiller {term}": "Lock {term}", "Accepter {term}": "Accept {term}", "Supprimer": "Delete", "Ajouter un choix humain": "Add a human choice", "Nouvelle expression source": "New source expression", "Expression source": "Source expression", "Nouvelle traduction": "New translation", "Ajouter et verrouiller": "Add and lock",
  "Validée par un humain": "Validated by a human", "Analyse IA — à examiner": "AI analysis — review required", "Exporter JSON": "Export JSON", "Résumé éditorial": "Editorial summary", "Lancez l’analyse pour construire la mémoire du livre.": "Run analysis to build the book memory.", "Personnages": "Characters", "Validé": "Validated", "Fiche personnage (JSON)": "Character profile (JSON)", "Modifier / valider": "Edit / validate", "Éditer la Book Bible structurée": "Edit structured Book Bible", "Enregistrer et valider la Book Bible": "Save and validate Book Bible", "Relecture ciblée": "Targeted review", "Les signaux automatiques orientent la relecture ; ils ne mesurent pas seuls la qualité littéraire.": "Automated signals guide review; they do not alone measure literary quality.", "Contrôle global de cohérence": "Global consistency check", "Traité": "Resolved", "Passage {id}": "Segment {id}", "Marquer comme traité": "Mark as resolved", "Aucun problème enregistré par les contrôles exécutés.": "No issue recorded by completed checks.", "Estimations du travail actif": "Active work estimates", "temps restant estimé": "estimated remaining time", "coût tarifaire consommé": "consumed list-price cost", "coût restant estimé": "estimated remaining cost", "confiance de l’estimation": "estimate confidence", "Estimations fondées sur les requêtes réussies de l’étape active et les tarifs actuellement configurés. Elles sont indisponibles tant que l’échantillon est insuffisant.": "Estimates are based on successful requests from the active stage and currently configured rates. They are unavailable while the sample is insufficient.", "Opération": "Operation", "Modèle": "Model", "État": "Status", "Durée": "Duration", "Entrée / sortie": "Input / output", "Débit moyen*": "Average throughput*", "Essai": "Attempt", "Tokens de sortie / durée totale de requête, préremplissage inclus. Les comptes absents du provider sont enregistrés à zéro, pas inventés.": "Output tokens / total request duration, including prefill. Accounts absent from the provider are recorded as zero, not invented.", "Précédentes": "Previous", "Suivantes": "Next", "Détail de la requête": "Request details", "Fermer": "Close",
};
registerTranslations(translations);
registerTranslations({
  "Configuration enregistrée.": "Configuration saved.",
  Cache: "Cache",
  Heure: "Time",
});

export function ProjectSettings({
  project,
  run,
  refresh,
}: {
  project: Project;
  run: Run;
  refresh: () => void;
}) {
  const { t } = useI18n();
  const [value, setValue] = useState(project);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [saved, setSaved] = useState("");
  const [username, setUsername] = useState("");
  const [role, setRole] = useState("reader");
  useEffect(() => {
    void run(async () => setProviders(await api("/providers")));
  }, [run]);
  return (
    <section className="narrow">
      <h2>{t("Stratégie du livre")}</h2>
      <p className="muted">
        {t("mots · {sections} sections / documents · {images} images · {size} Mo").replace("words", number(project.book_info.words)).replace("{sections}", String(project.stats.chapters)).replace("{images}", String(project.book_info.images)).replace("{size}", (project.book_info.size / 1024 ** 2).toFixed(1))}
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
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
              },
              "PUT",
            );
            refresh();
            setSaved(t("Configuration enregistrée."));
          });
        }}
      >
        <div className="form-grid">
          <label>
            {t("Titre à l’export")}
            <input
              value={value.title}
              onChange={(e) => setValue({ ...value, title: e.target.value })}
            />
          </label>
          <label>
            {t("Auteur")}
            <input
              value={value.author}
              onChange={(e) => setValue({ ...value, author: e.target.value })}
            />
          </label>
          <label>
            {t("Série")}
            <input
              value={value.series_name}
              onChange={(e) =>
                setValue({ ...value, series_name: e.target.value })
              }
              placeholder="Ex. Mushoku Tensei"
            />
          </label>
          <label>
            {t("Numéro du volume")}
            <input
              type="number"
              min="1"
              max="10000"
              value={value.volume_number ?? ""}
              onChange={(e) =>
                setValue({
                  ...value,
                  volume_number: e.target.value ? Number(e.target.value) : null,
                })
              }
              placeholder="1"
            />
          </label>
          <label>
            {t("Langue source")}
            <input
              value={value.source_language}
              onChange={(e) =>
                setValue({ ...value, source_language: e.target.value })
              }
            />
          </label>
          <label>
            {t("Langue cible (code BCP 47)")}
            <input
              value={value.target_language}
              onChange={(e) =>
                setValue({ ...value, target_language: e.target.value })
              }
              placeholder="fr"
            />
          </label>
          <label>
            Provider
            <select
              value={value.provider_id || ""}
              onChange={(e) =>
                setValue({ ...value, provider_id: e.target.value || null })
              }
            >
              <option value="">{t("Choisir…")}</option>
              {providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} · {p.model}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("Qualité")}
            <select
              value={value.quality}
              onChange={(e) => setValue({ ...value, quality: e.target.value })}
            >
              <option value="fast">{t("Rapide")}</option>
              <option value="normal">{t("Normal · vérification")}</option>
              <option value="high">{t("Haute qualité · critique & révision")}</option>
              <option value="maximum">Maximum · polishing</option>
            </select>
          </label>
          <label>
            {t("Moteur de contexte")}
            <select
              value={value.context_backend}
              onChange={(e) =>
                setValue({ ...value, context_backend: e.target.value })
              }
            >
              <option value="internal">{t("Internal — recommandé")}</option>
              <option value="hybrid">Hybrid</option>
              <option value="openviking">OpenViking</option>
            </select>
          </label>
        </div>
        <label>
          {t("Instructions globales")}
          <textarea
            rows={6}
            value={value.instructions}
            onChange={(e) =>
              setValue({ ...value, instructions: e.target.value })
            }
            placeholder={t("Conserver les suffixes -san, -chan, -sama. Tutoyer entre Alice et Bob…")}
          />
        </label>
        <button className="primary">{t("Enregistrer")}</button>
        <p role="status">{saved}</p>
      </form>
      <details>
        <summary>{t("Rapport de validation à l’import")}</summary>
        <pre>{JSON.stringify(project.book_info.validation, null, 2)}</pre>
      </details>
      <h3>{t("Partager ce projet")}</h3>
      <form
        className="actions"
        onSubmit={(e) => {
          e.preventDefault();
          void run(async () => {
            await send(`/projects/${project.id}/members`, { username, role });
            setSaved(t("Accès accordé."));
          });
        }}
      >
        <input
          aria-label={t("Utilisateur à inviter")}
          placeholder={t("Utilisateur existant")}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          required
        />
        <select
          aria-label={t("Rôle sur le projet")}
          value={role}
          onChange={(e) => setRole(e.target.value)}
        >
          <option value="reader">{t("Lecteur")}</option>
          <option value="editor">{t("Éditeur")}</option>
        </select>
        <button>{t("Partager")}</button>
      </form>
      <hr />
      <button
        className="danger"
        onClick={() => {
          if (
            confirm(
              "Supprimer le projet local, ses traductions et arrêter ses travaux ? La mémoire OpenViking distante reste séparée. Exportez le projet pour conserver une copie.",
            )
          )
            void run(async () => {
              await api(`/projects/${project.id}?stop_jobs=true`, {
                method: "DELETE",
              });
              location.hash = "library";
            });
        }}
      >
        Supprimer ce projet
      </button>
    </section>
  );
}

export function Glossary({
  project,
  run,
  tick,
}: {
  project: Project;
  run: Run;
  tick: number;
}) {
  const { t } = useI18n();
  const [terms, setTerms] = useState<Term[]>([]);
  const [search, setSearch] = useState("");
  const [source, setSource] = useState("");
  const [translation, setTranslation] = useState("");
  const [message, setMessage] = useState("");
  async function load() {
    setTerms(await api(`/projects/${project.id}/glossary`));
  }
  useEffect(() => {
    void run(async () =>
      setTerms(await api(`/projects/${project.id}/glossary`)),
    );
  }, [project.id, tick, run]);
  async function save(term: Term) {
    const { source, translation, category, description, locked, accepted } =
      term;
    await send(
      `/projects/${project.id}/glossary/${term.id}`,
      { source, translation, category, description, locked, accepted },
      "PUT",
    );
    setMessage(
      t("Terme enregistré. Les passages concernés sont marqués à réévaluer."),
    );
    await load();
  }
  const filtered = terms.filter((t) =>
    `${t.source} ${t.translation}`.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <section>
      <div className="page-heading">
        <div>
          <h2>{t("Choix terminologiques")}</h2>
          <p className="muted">
            {t("{accepted} acceptés · {proposals} propositions · {locked} verrouillés")
              .replace("{accepted}", String(terms.filter((t) => t.accepted).length))
              .replace("{proposals}", String(terms.filter((t) => !t.accepted).length))
              .replace("{locked}", String(terms.filter((t) => t.locked).length))}
          </p>
        </div>
        <div className="actions">
          <a
            className="button"
            href={`/api/projects/${project.id}/glossary/export/json`}
          >
            JSON ↓
          </a>
          <a
            className="button"
            href={`/api/projects/${project.id}/glossary/export/csv`}
          >
            CSV ↓
          </a>
          <label className="button">
            {t("Importer")}
            <input
              hidden
              type="file"
              accept=".json,.csv"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file)
                  void run(async () => {
                    const data = new FormData();
                    data.append("file", file);
                    const r = await api(
                      `/projects/${project.id}/glossary/import`,
                      { method: "POST", body: data },
                    );
                    setMessage(JSON.stringify(r));
                    await load();
                  });
              }}
            />
          </label>
        </div>
      </div>
      <div className="actions">
        <input
          className="search"
          aria-label={t("Rechercher dans le glossaire")}
          placeholder={t("Rechercher un terme…")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t("Source")}</th>
              <th>{t("Traduction")}</th>
              <th>{t("Catégorie")}</th>
              <th>{t("Verrouillé")}</th>
              <th>{t("Accepté")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {filtered.map((term) => (
              <tr key={term.id} className={!term.accepted ? "proposal" : ""}>
                <td>{term.source}</td>
                <td>
                  <input
                    aria-label={t("Traduction de {term}").replace("{term}", term.source)}
                    value={term.translation}
                    onChange={(e) =>
                      setTerms(
                        terms.map((v) =>
                          v.id === term.id
                            ? { ...v, translation: e.target.value }
                            : v,
                        ),
                      )
                    }
                  />
                </td>
                <td>
                  <input
                    aria-label={t("Catégorie de {term}").replace("{term}", term.source)}
                    value={term.category}
                    onChange={(e) =>
                      setTerms(
                        terms.map((v) =>
                          v.id === term.id
                            ? { ...v, category: e.target.value }
                            : v,
                        ),
                      )
                    }
                  />
                </td>
                <td>
                  <input
                    aria-label={t("Verrouiller {term}").replace("{term}", term.source)}
                    type="checkbox"
                    checked={term.locked}
                    onChange={(e) =>
                      setTerms(
                        terms.map((v) =>
                          v.id === term.id
                            ? { ...v, locked: e.target.checked }
                            : v,
                        ),
                      )
                    }
                  />
                </td>
                <td>
                  <input
                    aria-label={t("Accepter {term}").replace("{term}", term.source)}
                    type="checkbox"
                    checked={term.accepted}
                    onChange={(e) =>
                      setTerms(
                        terms.map((v) =>
                          v.id === term.id
                            ? { ...v, accepted: e.target.checked }
                            : v,
                        ),
                      )
                    }
                  />
                </td>
                <td>
                  <div className="actions">
                    <button onClick={() => void run(() => save(term))}>
                      {t("Enregistrer")}
                    </button>
                    <button
                      className="quiet"
                      onClick={() =>
                        void run(async () => {
                          await api(
                            `/projects/${project.id}/glossary/${term.id}`,
                            { method: "DELETE" },
                          );
                          await load();
                        })
                      }
                    >
                      {t("Supprimer")}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3>{t("Ajouter un choix humain")}</h3>
      <form
        className="actions"
        onSubmit={(e) => {
          e.preventDefault();
          void run(async () => {
            await send(`/projects/${project.id}/glossary`, {
              source,
              translation,
              accepted: true,
              locked: true,
            });
            setSource("");
            setTranslation("");
            await load();
          });
        }}
      >
        <input
          aria-label={t("Nouvelle expression source")}
          placeholder={t("Expression source")}
          value={source}
          onChange={(e) => setSource(e.target.value)}
          required
        />
        <input
          aria-label={t("Nouvelle traduction")}
          placeholder={t("Traduction")}
          value={translation}
          onChange={(e) => setTranslation(e.target.value)}
          required
        />
        <button className="primary">{t("Ajouter et verrouiller")}</button>
      </form>
      <p role="status">{message}</p>
    </section>
  );
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
  const [text, setText] = useState(JSON.stringify(project.bible, null, 2));
  const [dirty, setDirty] = useState(false);
  const [data, setData] = useState<{
    bible: Record<string, unknown>;
    validated: boolean;
    entities: {
      id: string;
      name: string;
      data: Record<string, unknown>;
      validated: boolean;
    }[];
  } | null>(null);
  useEffect(() => {
    void run(async () => {
      setData(await api(`/projects/${project.id}/bible`));
    });
    if (!dirty) setText(JSON.stringify(project.bible, null, 2));
  }, [project.id, project.updated_at, run, tick]);
  return (
    <section>
      <div className="page-heading">
        <div>
          <h2>Book Bible</h2>
          <span className="muted">
            {data?.validated
              ? t("Validée par un humain")
              : t("Analyse IA — à examiner")}
          </span>
        </div>
        <button onClick={() => download("book-bible.json", project.bible)}>
          {t("Exporter JSON")}
        </button>
      </div>
      <div className="bible-layout">
        <div>
          <h3>{t("Résumé éditorial")}</h3>
          <p className="literary">
            {String(
              data?.bible.summary ||
                t("Lancez l’analyse pour construire la mémoire du livre."),
            )}
          </p>
          <dl>
            {[
              "genre",
              "tone",
              "narrative_style",
              "narrative_point_of_view",
              "tense",
              "translation_guidelines",
              "relationships",
              "locations",
              "honorifics",
              "known_wordplay",
            ].map((k) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd>
                  {Array.isArray(data?.bible[k])
                    ? (data?.bible[k] as string[]).join(" · ")
                    : String(data?.bible[k] || "—")}
                </dd>
              </div>
            ))}
          </dl>
          <h3>{t("Personnages")}</h3>
          {data?.entities.map((e) => (
            <details key={e.id}>
              <summary>
                {e.name} {e.validated ? `· ${t("Validé")}` : ""}
              </summary>
              <pre>{JSON.stringify(e.data, null, 2)}</pre>
              <button
                onClick={() =>
                  void run(async () => {
                    const { first_position: _position, ...profile } = e.data;
                    const edited = prompt(
                      t("Fiche personnage (JSON)"),
                      JSON.stringify(profile, null, 2),
                    );
                    if (!edited) return;
                    await send(
                      `/projects/${project.id}/characters/${e.id}`,
                      JSON.parse(edited),
                      "PUT",
                    );
                    setData(await api(`/projects/${project.id}/bible`));
                    refresh();
                  })
                }
              >
                {t("Modifier / valider")}
              </button>
            </details>
          ))}
        </div>
        <aside>
          <MemoryPanel pid={project.id} run={run} refreshKey={tick} />
        </aside>
      </div>
      <details>
        <summary>{t("Éditer la Book Bible structurée")}</summary>
        <textarea
          className="prompt-editor"
          aria-label="Book Bible JSON"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setDirty(true);
          }}
        />
        <button
          className="primary"
          onClick={() =>
            void run(async () => {
              await send(
                `/projects/${project.id}/bible`,
                JSON.parse(text),
                "PUT",
              );
              setData(await api(`/projects/${project.id}/bible`));
              setDirty(false);
              refresh();
            })
          }
        >
          {t("Enregistrer et valider la Book Bible")}
        </button>
      </details>
    </section>
  );
}

export function Quality({
  project,
  run,
  tick,
}: {
  project: Project;
  run: Run;
  tick: number;
}) {
  const { t } = useI18n();
  const [issues, setIssues] = useState<Issue[]>([]);
  useEffect(() => {
    void run(async () =>
      setIssues(await api(`/projects/${project.id}/issues`)),
    );
  }, [project.id, tick, run]);
  return (
    <section>
      <div className="page-heading">
        <div>
          <h2>{t("Relecture ciblée")}</h2>
          <p className="muted">
            {t(
              "Les signaux automatiques orientent la relecture ; ils ne mesurent pas seuls la qualité littéraire.",
            )}
          </p>
        </div>
        <button
          onClick={() =>
            void run(async () => {
              await send(`/projects/${project.id}/jobs`, {
                operation: "consistency",
              });
            })
          }
        >
          {t("Contrôle global de cohérence")}
        </button>
      </div>
      {issues.length ? (
        issues.map((i) => (
          <article className="issue" key={i.id}>
            <span className={`badge ${i.severity}`}>
              {i.resolved ? t("Traité") : i.severity}
            </span>
            <strong>{i.code}</strong>
            <p>{i.message}</p>
            <small>{t("Passage {id}").replace("{id}", String(i.segment_id))}</small>
            {!i.resolved && (
              <button
                onClick={() =>
                  void run(async () => {
                    await send(`/issues/${i.id}/resolve`);
                    setIssues(await api(`/projects/${project.id}/issues`));
                  })
                }
              >
                {t("Marquer comme traité")}
              </button>
            )}
          </article>
        ))
      ) : (
        <div className="empty">
          {t("Aucun problème enregistré par les contrôles exécutés.")}
        </div>
      )}
    </section>
  );
}

export function Observability({
  project,
  run,
  tick,
}: {
  project: Project;
  run: Run;
  tick: number;
}) {
  const { t } = useI18n();
  const [requests, setRequests] = useState<LLMRequest[]>([]);
  const [metrics, setMetrics] = useState<Record<string, number>>({});
  const [selected, setSelected] = useState<LLMRequest | null>(null);
  const [offset, setOffset] = useState(0);
  const progress = projectProgress(project);
  useEffect(() => {
    void run(async () => {
      const [r, m] = await Promise.all([
        api<LLMRequest[]>(`/projects/${project.id}/requests?offset=${offset}`),
        api<Record<string, number>>(`/projects/${project.id}/metrics`),
      ]);
      setRequests(r);
      setMetrics(m);
    });
  }, [project.id, tick, run, offset]);
  return (
    <section>
      <h2>{t("Observabilité")}</h2>
      <div className="metric-strip" aria-label={t("Estimations du travail actif")}>
        <div>
          <strong>{duration(progress.estimate.remaining_seconds)}</strong>
          <small>{t("temps restant estimé")}</small>
        </div>
        <div>
          <strong>{number(progress.estimate.spent_cost)}</strong>
          <small>{t("coût tarifaire consommé")}</small>
        </div>
        <div>
          <strong>
            {progress.estimate.remaining_cost === null
              ? "—"
              : number(progress.estimate.remaining_cost)}
          </strong>
          <small>{t("coût restant estimé")}</small>
        </div>
        <div>
          <strong>{progress.estimate.confidence}</strong>
          <small>{t("confiance de l’estimation")}</small>
        </div>
      </div>
      <p className="muted">
        {t("Estimations fondées sur les requêtes réussies de l’étape active et les tarifs actuellement configurés. Elles sont indisponibles tant que l’échantillon est insuffisant.")}
      </p>
      <div className="metric-strip">
        {Object.entries(metrics).map(([k, v]) => (
          <div key={k}>
            <strong>{number(v)}</strong>
            <small>{k}</small>
          </div>
        ))}
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t("Heure")}</th>
              <th>{t("Opération")}</th>
              <th>{t("Modèle")}</th>
              <th>{t("État")}</th>
              <th>{t("Durée")}</th>
              <th>{t("Entrée / sortie")}</th>
              <th>{t("Débit moyen*")}</th>
              <th>{t("Essai")}</th>
            </tr>
          </thead>
          <tbody>
            {requests.map((r) => (
              <tr key={r.id}>
                <td data-label={t("Heure")}>{date(r.created_at)}</td>
                <td>
                  <button
                    className="link"
                    onClick={() =>
                      void run(async () =>
                        setSelected(await api(`/requests/${r.id}`)),
                      )
                    }
                  >
                    {r.operation}
                  </button>
                  {r.cached && <small>{t("Cache")}</small>}
                </td>
                <td>{r.model}</td>
                <td>
                  <span className={`badge ${r.status}`}>
                    {labels[r.status] || r.status}
                  </span>
                </td>
                <td>{r.duration.toFixed(1)} s</td>
                <td>
                  {number(r.prompt_tokens)} / {number(r.completion_tokens)}
                </td>
                <td>
                  {r.duration > 0
                    ? (r.completion_tokens / r.duration).toFixed(1)
                    : "—"}{" "}
                  t/s
                </td>
                <td>{r.attempt}/5</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted">
        * Tokens de sortie / durée totale de requête, préremplissage inclus. Les
        comptes absents du provider sont enregistrés à zéro, pas inventés.
      </p>
      <div className="pagination">
        <button
          disabled={!offset}
          onClick={() => setOffset((v) => Math.max(0, v - 100))}
        >
          Précédentes
        </button>
        <button
          disabled={requests.length < 100}
          onClick={() => setOffset((v) => v + 100)}
        >
          Suivantes
        </button>
      </div>
      {selected && (
        <aside
          className="inspector"
          role="dialog"
          aria-label={t("Détail de la requête")}
        >
          <header>
            <h2>{selected.operation}</h2>
            <button onClick={() => setSelected(null)}>{t("Fermer")}</button>
          </header>
          <RequestDetails request={selected} />
        </aside>
      )}
    </section>
  );
}
