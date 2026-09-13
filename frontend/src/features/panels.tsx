import { useEffect, useState } from "react";
import { api, download, labels, number, send } from "../api";
import type { Issue, LLMRequest, Project, Provider, Run, Term } from "../types";
import { RequestDetails } from "./Editor";
import { MemoryPanel } from "./MemoryPanel";
import { duration, projectProgress } from "./progress";

export function ProjectSettings({
  project,
  run,
  refresh,
}: {
  project: Project;
  run: Run;
  refresh: () => void;
}) {
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
      <h2>Stratégie du livre</h2>
      <p className="muted">
        {number(project.book_info.words)} mots · {project.stats.chapters}{" "}
        sections / documents · {project.book_info.images} images ·{" "}
        {(project.book_info.size / 1024 ** 2).toFixed(1)} Mo
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
            setSaved("Configuration enregistrée.");
          });
        }}
      >
        <div className="form-grid">
          <label>
            Titre à l’export
            <input
              value={value.title}
              onChange={(e) => setValue({ ...value, title: e.target.value })}
            />
          </label>
          <label>
            Auteur
            <input
              value={value.author}
              onChange={(e) => setValue({ ...value, author: e.target.value })}
            />
          </label>
          <label>
            Série
            <input
              value={value.series_name}
              onChange={(e) =>
                setValue({ ...value, series_name: e.target.value })
              }
              placeholder="Ex. Mushoku Tensei"
            />
          </label>
          <label>
            Numéro du volume
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
            Langue source
            <input
              value={value.source_language}
              onChange={(e) =>
                setValue({ ...value, source_language: e.target.value })
              }
            />
          </label>
          <label>
            Langue cible (code BCP 47)
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
              <option value="">Choisir…</option>
              {providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} · {p.model}
                </option>
              ))}
            </select>
          </label>
          <label>
            Qualité
            <select
              value={value.quality}
              onChange={(e) => setValue({ ...value, quality: e.target.value })}
            >
              <option value="fast">Rapide</option>
              <option value="normal">Normal · vérification</option>
              <option value="high">Haute qualité · critique & révision</option>
              <option value="maximum">Maximum · polishing</option>
            </select>
          </label>
          <label>
            Moteur de contexte
            <select
              value={value.context_backend}
              onChange={(e) =>
                setValue({ ...value, context_backend: e.target.value })
              }
            >
              <option value="hybrid">Hybrid — recommandé</option>
              <option value="internal">Internal</option>
              <option value="openviking">OpenViking</option>
            </select>
          </label>
        </div>
        <label>
          Instructions globales
          <textarea
            rows={6}
            value={value.instructions}
            onChange={(e) =>
              setValue({ ...value, instructions: e.target.value })
            }
            placeholder="Conserver les suffixes -san, -chan, -sama. Tutoyer entre Alice et Bob…"
          />
        </label>
        <button className="primary">Enregistrer</button>
        <p role="status">{saved}</p>
      </form>
      <details>
        <summary>Rapport de validation à l’import</summary>
        <pre>{JSON.stringify(project.book_info.validation, null, 2)}</pre>
      </details>
      <h3>Partager ce projet</h3>
      <form
        className="actions"
        onSubmit={(e) => {
          e.preventDefault();
          void run(async () => {
            await send(`/projects/${project.id}/members`, { username, role });
            setSaved("Accès accordé.");
          });
        }}
      >
        <input
          aria-label="Utilisateur à inviter"
          placeholder="Utilisateur existant"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          required
        />
        <select
          aria-label="Rôle sur le projet"
          value={role}
          onChange={(e) => setRole(e.target.value)}
        >
          <option value="reader">Lecteur</option>
          <option value="editor">Éditeur</option>
        </select>
        <button>Partager</button>
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
      "Terme enregistré. Les passages concernés sont marqués à réévaluer.",
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
          <h2>Choix terminologiques</h2>
          <p className="muted">
            {terms.filter((t) => t.accepted).length} acceptés ·{" "}
            {terms.filter((t) => !t.accepted).length} propositions ·{" "}
            {terms.filter((t) => t.locked).length} verrouillés
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
            Importer
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
          aria-label="Rechercher dans le glossaire"
          placeholder="Rechercher un terme…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th>Traduction</th>
              <th>Catégorie</th>
              <th>Verrouillé</th>
              <th>Accepté</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {filtered.map((t) => (
              <tr key={t.id} className={!t.accepted ? "proposal" : ""}>
                <td>{t.source}</td>
                <td>
                  <input
                    aria-label={`Traduction de ${t.source}`}
                    value={t.translation}
                    onChange={(e) =>
                      setTerms(
                        terms.map((v) =>
                          v.id === t.id
                            ? { ...v, translation: e.target.value }
                            : v,
                        ),
                      )
                    }
                  />
                </td>
                <td>
                  <input
                    aria-label={`Catégorie de ${t.source}`}
                    value={t.category}
                    onChange={(e) =>
                      setTerms(
                        terms.map((v) =>
                          v.id === t.id
                            ? { ...v, category: e.target.value }
                            : v,
                        ),
                      )
                    }
                  />
                </td>
                <td>
                  <input
                    aria-label={`Verrouiller ${t.source}`}
                    type="checkbox"
                    checked={t.locked}
                    onChange={(e) =>
                      setTerms(
                        terms.map((v) =>
                          v.id === t.id
                            ? { ...v, locked: e.target.checked }
                            : v,
                        ),
                      )
                    }
                  />
                </td>
                <td>
                  <input
                    aria-label={`Accepter ${t.source}`}
                    type="checkbox"
                    checked={t.accepted}
                    onChange={(e) =>
                      setTerms(
                        terms.map((v) =>
                          v.id === t.id
                            ? { ...v, accepted: e.target.checked }
                            : v,
                        ),
                      )
                    }
                  />
                </td>
                <td>
                  <div className="actions">
                    <button onClick={() => void run(() => save(t))}>
                      Enregistrer
                    </button>
                    <button
                      className="quiet"
                      onClick={() =>
                        void run(async () => {
                          await api(
                            `/projects/${project.id}/glossary/${t.id}`,
                            { method: "DELETE" },
                          );
                          await load();
                        })
                      }
                    >
                      Supprimer
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3>Ajouter un choix humain</h3>
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
          aria-label="Nouvelle expression source"
          placeholder="Expression source"
          value={source}
          onChange={(e) => setSource(e.target.value)}
          required
        />
        <input
          aria-label="Nouvelle traduction"
          placeholder="Traduction"
          value={translation}
          onChange={(e) => setTranslation(e.target.value)}
          required
        />
        <button className="primary">Ajouter et verrouiller</button>
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
              ? "Validée par un humain"
              : "Analyse IA — à examiner"}
          </span>
        </div>
        <button onClick={() => download("book-bible.json", project.bible)}>
          Exporter JSON
        </button>
      </div>
      <div className="bible-layout">
        <div>
          <h3>Résumé éditorial</h3>
          <p className="literary">
            {String(
              data?.bible.summary ||
                "Lancez l’analyse pour construire la mémoire du livre.",
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
          <h3>Personnages</h3>
          {data?.entities.map((e) => (
            <details key={e.id}>
              <summary>
                {e.name} {e.validated ? "· Validé" : ""}
              </summary>
              <pre>{JSON.stringify(e.data, null, 2)}</pre>
              <button
                onClick={() =>
                  void run(async () => {
                    const { first_position: _position, ...profile } = e.data;
                    const edited = prompt(
                      "Fiche personnage (JSON)",
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
                Modifier / valider
              </button>
            </details>
          ))}
        </div>
        <aside>
          <MemoryPanel pid={project.id} run={run} refreshKey={tick} />
        </aside>
      </div>
      <details>
        <summary>Éditer la Book Bible structurée</summary>
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
          Enregistrer et valider la Book Bible
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
          <h2>Relecture ciblée</h2>
          <p className="muted">
            Les signaux automatiques orientent la relecture ; ils ne mesurent
            pas seuls la qualité littéraire.
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
          Contrôle global de cohérence
        </button>
      </div>
      {issues.length ? (
        issues.map((i) => (
          <article className="issue" key={i.id}>
            <span className={`badge ${i.severity}`}>
              {i.resolved ? "Traité" : i.severity}
            </span>
            <strong>{i.code}</strong>
            <p>{i.message}</p>
            <small>Passage {i.segment_id}</small>
            {!i.resolved && (
              <button
                onClick={() =>
                  void run(async () => {
                    await send(`/issues/${i.id}/resolve`);
                    setIssues(await api(`/projects/${project.id}/issues`));
                  })
                }
              >
                Marquer comme traité
              </button>
            )}
          </article>
        ))
      ) : (
        <div className="empty">
          Aucun problème enregistré par les contrôles exécutés.
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
      <h2>Observabilité</h2>
      <div className="metric-strip" aria-label="Estimations du travail actif">
        <div>
          <strong>{duration(progress.estimate.remaining_seconds)}</strong>
          <small>temps restant estimé</small>
        </div>
        <div>
          <strong>{number(progress.estimate.spent_cost)}</strong>
          <small>coût tarifaire consommé</small>
        </div>
        <div>
          <strong>
            {progress.estimate.remaining_cost === null
              ? "—"
              : number(progress.estimate.remaining_cost)}
          </strong>
          <small>coût restant estimé</small>
        </div>
        <div>
          <strong>{progress.estimate.confidence}</strong>
          <small>confiance de l’estimation</small>
        </div>
      </div>
      <p className="muted">
        Estimations fondées sur les requêtes réussies de l’étape active et les
        tarifs actuellement configurés. Elles sont indisponibles tant que
        l’échantillon est insuffisant.
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
              <th>Opération</th>
              <th>Modèle</th>
              <th>État</th>
              <th>Durée</th>
              <th>Entrée / sortie</th>
              <th>Débit moyen*</th>
              <th>Essai</th>
            </tr>
          </thead>
          <tbody>
            {requests.map((r) => (
              <tr key={r.id}>
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
                  {r.cached && <small>Cache</small>}
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
          aria-label="Détail de la requête"
        >
          <header>
            <h2>{selected.operation}</h2>
            <button onClick={() => setSelected(null)}>Fermer</button>
          </header>
          <RequestDetails request={selected} />
        </aside>
      )}
    </section>
  );
}
