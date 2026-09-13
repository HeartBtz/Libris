import { useEffect, useLayoutEffect, useState } from "react";
import { api, date, labels, send } from "../api";
import type {
  Chapter,
  LLMRequest,
  Project,
  Run,
  Segment,
  Unit,
  Version,
} from "../types";

export function Editor({
  project,
  chapter,
  tick,
  run,
  refresh,
  focusRefusal,
}: {
  project: Project;
  chapter: Chapter;
  tick: number;
  run: Run;
  refresh: () => void;
  focusRefusal?: string;
}) {
  const [segments, setSegments] = useState<Segment[]>([]);
  const [filter, setFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Segment | null>(null);
  const [preview, setPreview] = useState("");
  useEffect(() => {
    if (focusRefusal) {
      setFilter("refused");
      setOffset(0);
    }
  }, [focusRefusal]);
  useEffect(() => {
    setOffset(0);
    setSelected(null);
  }, [chapter.id]);
  useEffect(() => {
    void run(async () =>
      setSegments(
        await api(
          `/projects/${project.id}/segments?chapter_id=${chapter.id}&status=${filter}&offset=${offset}&limit=50`,
        ),
      ),
    );
  }, [project.id, chapter.id, filter, offset, tick, run]);
  async function chapterAction() {
    await send(`/projects/${project.id}/jobs`, {
      operation: "translate",
      chapter_id: chapter.id,
      force: true,
    });
    refresh();
  }
  return (
    <section className="editor">
      <div className="editor-toolbar">
        <strong>{chapter.title}</strong>
        <div className="actions">
          <select
            aria-label="Filtrer les passages"
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">Tous les passages</option>
            <option value="check">À vérifier</option>
            <option value="error">Erreurs</option>
            <option value="uncertain">Incertitudes</option>
            <option value="refused">Refus du provider</option>
            <option value="source_retained">Originaux conservés</option>
          </select>
          <button
            onClick={() =>
              void run(async () => {
                const r = await api<{ html: string }>(
                  `/projects/${project.id}/preview/${chapter.id}`,
                );
                setPreview(r.html);
              })
            }
          >
            Prévisualiser
          </button>
          <button
            onClick={() =>
              void run(async () => {
                const instructions = window.prompt(
                  "Instructions pour cette section",
                  chapter.instructions,
                );
                if (instructions !== null) {
                  await send(
                    `/projects/${project.id}/chapters/${chapter.id}/instructions`,
                    { instructions },
                    "PUT",
                  );
                  refresh();
                }
              })
            }
          >
            Instructions
          </button>
          <button
            onClick={() => {
              if (
                confirm(
                  "Retraduire cette section ? Les corrections humaines seront conservées et les résultats proposés dans l’historique.",
                )
              )
                void run(chapterAction);
            }}
          >
            Retraduire la section
          </button>
        </div>
      </div>
      <div className="column-labels">
        <span>Source · {project.source_language}</span>
        <span>Traduction · {project.target_language}</span>
      </div>
      <div className="parallel-text">
        {segments.map((s) => (
          <SegmentRow
            key={s.id}
            segment={s}
            project={project}
            run={run}
            refresh={refresh}
            inspect={() => setSelected(s)}
          />
        ))}
        {!segments.length && (
          <div className="empty">Aucun passage pour ce filtre.</div>
        )}
      </div>
      <div className="pagination">
        <button
          disabled={offset === 0}
          onClick={() => setOffset((v) => Math.max(0, v - 50))}
        >
          ← Précédents
        </button>
        <span>
          Passages {offset + 1}–{offset + segments.length}
        </span>
        <button
          disabled={segments.length < 50}
          onClick={() => setOffset((v) => v + 50)}
        >
          Suivants →
        </button>
      </div>
      {selected && (
        <Inspector
          segment={selected}
          run={run}
          close={() => setSelected(null)}
          refresh={refresh}
        />
      )}
      {preview && (
        <div className="modal-backdrop">
          <section
            className="preview-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Prévisualisation du chapitre"
          >
            <header>
              <h2>Rendu simplifié</h2>
              <button onClick={() => setPreview("")}>Fermer</button>
            </header>
            <p className="muted">
              Les passages non traduits restent en langue source. CSS
              simplifiée, scripts et ressources externes désactivés.
            </p>
            <iframe title="Rendu du chapitre" sandbox="" srcDoc={preview} />
          </section>
        </div>
      )}
    </section>
  );
}

export function SegmentRow({
  segment,
  project,
  run,
  refresh,
  inspect,
}: {
  segment: Segment;
  project: Project;
  run: Run;
  refresh: () => void;
  inspect: () => void;
}) {
  const fromServer = () =>
    segment.units.map((u) => ({
      id: u.id,
      text: segment.translated_units.find((t) => t.id === u.id)?.text || "",
    }));
  const [units, setUnits] = useState<Unit[]>(fromServer);
  const [base, setBase] = useState(segment.revision);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  useLayoutEffect(() => {
    if (!dirty) {
      setUnits(
        segment.units.map((u) => ({
          id: u.id,
          text: segment.translated_units.find((t) => t.id === u.id)?.text || "",
        })),
      );
      setBase(segment.revision);
    }
  }, [segment.revision]);
  async function save(validated: boolean) {
    setBusy(true);
    await run(async () => {
      const saved = await send<Segment>(
        `/segments/${segment.id}`,
        { revision: base, units, validated },
        "PUT",
      );
      setUnits(saved.translated_units);
      setBase(saved.revision);
      setDirty(false);
      refresh();
      if (
        validated &&
        confirm("Correction enregistrée. Ajouter une expression au glossaire ?")
      ) {
        const source = prompt("Expression source");
        if (!source) return;
        const translation = prompt("Traduction à conserver");
        if (!translation) return;
        await send(`/projects/${project.id}/glossary`, {
          source,
          translation,
          locked: true,
          accepted: true,
        });
        refresh();
      }
    });
    setBusy(false);
  }
  async function translate(deep = false, custom = false) {
    const instruction = custom
      ? prompt("Instruction pour cette retraduction")
      : "";
    if (instruction === null) return;
    await send(`/projects/${project.id}/jobs`, {
      operation: "translate",
      segment_id: segment.id,
      force: true,
      deep,
      instruction,
    });
    refresh();
  }
  return (
    <article
      className={`segment-row ${segment.status}`}
      id={`segment-${segment.id}`}
    >
      <div className="source">
        <div className="segment-meta">
          <span>§ {segment.position + 1}</span>
          <span>
            {segment.units.length} unité{segment.units.length > 1 ? "s" : ""}
          </span>
        </div>
        {segment.units.map((u) => (
          <p key={u.id}>
            <CodedText text={u.text} />
          </p>
        ))}
      </div>
      <div className="translation">
        <div className="segment-meta">
          <span className={`badge ${segment.status}`}>
            {segment.validated
              ? "Validé humainement"
              : labels[segment.status] || segment.status}
          </span>
          <span>
            {segment.human
              ? "Correction humaine protégée"
              : `Version ${segment.revision}`}
            {dirty ? " · Non enregistré" : ""}
          </span>
        </div>
        {units.map((u, i) => (
          <textarea
            key={u.id}
            aria-label={`Traduction passage ${segment.position + 1} unité ${i + 1}`}
            value={u.text}
            placeholder="La traduction apparaîtra ici…"
            rows={Math.max(
              2,
              Math.ceil(
                Math.max(u.text.length, segment.units[i]?.text.length || 0) /
                  85,
              ),
            )}
            onChange={(e) => {
              setDirty(true);
              setUnits(
                units.map((v, j) =>
                  j === i ? { ...v, text: e.target.value } : v,
                ),
              );
            }}
          />
        ))}
        {dirty && base !== segment.revision && (
          <p className="notice">
            Une nouvelle version est arrivée. Votre saisie est conservée ;
            enregistrement soumis à vérification de version.
          </p>
        )}
        {segment.error && <p className="inline-error">{segment.error}</p>}
        {segment.retained_source && (
          <p className="notice">
            Original conservé par décision humaine. Ce passage n’est pas compté
            comme traduit ; utilisez l’export partiel ou saisissez une
            traduction.
          </p>
        )}
        {segment.status === "refused" && (
          <div className="notice">
            Refus enregistré. Vous pouvez saisir une traduction ou ouvrir
            l’inspecteur pour ajouter une analyse humaine.
            <button
              onClick={() => {
                if (
                  confirm(
                    "Conserver le texte source pour ce passage ? Il restera signalé comme non traduit et sera disponible dans l’export partiel. Cela ne remplace pas une analyse humaine manquante.",
                  )
                )
                  void run(async () => {
                    await send(`/segments/${segment.id}/retain-source`, {
                      revision: segment.revision,
                    });
                    refresh();
                  });
              }}
            >
              Conserver l’original pour l’export
            </button>
          </div>
        )}
        {!!segment.uncertainties.length && (
          <details>
            <summary>{segment.uncertainties.length} incertitude(s)</summary>
            <ul>
              {segment.uncertainties.map((u, i) => (
                <li key={i}>{u}</li>
              ))}
            </ul>
          </details>
        )}
        <div className="segment-actions">
          <button
            disabled={busy || !units.every((u) => u.text)}
            onClick={() => void save(false)}
          >
            Enregistrer
          </button>
          <button
            disabled={busy || !units.every((u) => u.text)}
            onClick={() => void save(true)}
          >
            Valider
          </button>
          <details className="retranslate">
            <summary>Retraduire ▾</summary>
            <div>
              <button onClick={() => void run(() => translate())}>
                Retraduire
              </button>
              <button onClick={() => void run(() => translate(true))}>
                Avec plus de contexte
              </button>
              <button onClick={() => void run(() => translate(false, true))}>
                Avec une instruction
              </button>
            </div>
          </details>
          <button onClick={inspect}>Contexte / historique / Ask AI</button>
        </div>
      </div>
    </article>
  );
}

function CodedText({ text }: { text: string }) {
  return (
    <>
      {text.split(/(⟦\/?[tx]\d+⟧)/g).map((p, i) =>
        p.startsWith("⟦") ? (
          <span className="code" key={i}>
            {p}
          </span>
        ) : (
          p
        ),
      )}
    </>
  );
}

export function RequestDetails({ request }: { request: LLMRequest }) {
  return (
    <>
      <div className="metrics-inline">
        {request.model} · {request.duration.toFixed(1)} s · tentative{" "}
        {request.attempt}/5 · {request.cached ? "cache" : "provider"}
      </div>
      {request.error && <p className="inline-error">{request.error}</p>}
      <details open>
        <summary>Contexte réellement sélectionné / écarté</summary>
        <pre>{JSON.stringify(request.context, null, 2)}</pre>
      </details>
      {request.messages?.map((m, i) => (
        <details key={i}>
          <summary>Prompt final · {m.role}</summary>
          <pre>{m.content}</pre>
        </details>
      ))}
      <details>
        <summary>Réponse interprétée</summary>
        <pre>{JSON.stringify(request.parsed, null, 2)}</pre>
      </details>
      <details>
        <summary>Réponse brute du provider</summary>
        <pre>{JSON.stringify(request.raw, null, 2)}</pre>
      </details>
    </>
  );
}

export function Inspector({
  segment,
  run,
  close,
  refresh,
}: {
  segment: Segment;
  run: Run;
  close: () => void;
  refresh: () => void;
}) {
  const [tab, setTab] = useState("context");
  const [requests, setRequests] = useState<LLMRequest[]>([]);
  const [versions, setVersions] = useState<Version[]>([]);
  const [index, setIndex] = useState(0);
  const [question, setQuestion] = useState("");
  const [selection, setSelection] = useState("");
  const [answer, setAnswer] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [humanSummary, setHumanSummary] = useState("");
  const [analysisSaved, setAnalysisSaved] = useState(false);
  useEffect(() => {
    void run(async () => {
      const [r, v] = await Promise.all([
        api<LLMRequest[]>(`/segments/${segment.id}/requests`),
        api<Version[]>(`/segments/${segment.id}/versions`),
      ]);
      setRequests(r);
      setVersions(v);
    });
  }, [run, segment.id]);
  return (
    <aside
      className="inspector"
      role="dialog"
      aria-label="Inspecteur du passage"
    >
      <header>
        <h2>Passage {segment.position + 1}</h2>
        <button onClick={close} aria-label="Fermer l’inspecteur">
          ×
        </button>
      </header>
      <div className="tabs">
        {[
          ["context", "Contexte"],
          ["history", "Historique"],
          ["review", "Critique"],
          ["ask", "Ask AI"],
          ["analysis", "Analyse humaine"],
        ].map(([id, name]) => (
          <button
            className={tab === id ? "active" : ""}
            key={id}
            onClick={() => setTab(id)}
          >
            {name}
          </button>
        ))}
      </div>
      {tab === "context" ? (
        <>
          {requests.length ? (
            <>
              <select
                aria-label="Requête enregistrée"
                value={index}
                onChange={(e) => setIndex(+e.target.value)}
              >
                {requests.map((r, i) => (
                  <option key={r.id} value={i}>
                    {r.operation} · {date(r.created_at)} · {r.status}
                  </option>
                ))}
              </select>
              <RequestDetails request={requests[index]} />
            </>
          ) : (
            <p className="muted">Aucune requête enregistrée pour ce passage.</p>
          )}
        </>
      ) : tab === "history" ? (
        <>
          {versions.map((v) => (
            <section className="version" key={v.id}>
              <strong>
                {v.origin} ·{" "}
                {v.applied ? "Appliquée lors de sa création" : "Proposition"}
              </strong>
              <small>{date(v.created_at)}</small>
              <pre>{v.units.map((u) => u.text).join("\n\n")}</pre>
              <button
                onClick={() =>
                  void run(async () => {
                    const current = await api<Segment>(
                      `/segments/${segment.id}`,
                    );
                    await send(
                      `/segments/${segment.id}/versions/${v.id}/restore`,
                      {
                        revision: current.revision,
                        units: [],
                        validated: false,
                      },
                    );
                    refresh();
                    close();
                  })
                }
              >
                Restaurer comme correction humaine
              </button>
            </section>
          ))}
        </>
      ) : tab === "review" ? (
        <pre>{JSON.stringify(segment.critique, null, 2)}</pre>
      ) : tab === "analysis" ? (
        <>
          <p className="muted">
            Résumez les informations nécessaires à la continuité : événements,
            personnages et références. Cette saisie sera enregistrée comme une
            analyse humaine, sans appel au modèle.
          </p>
          <details>
            <summary>Texte source du passage</summary>
            <pre>{segment.source}</pre>
          </details>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                await send(
                  `/segments/${segment.id}/analysis`,
                  { summary: humanSummary },
                  "PUT",
                );
                setAnalysisSaved(true);
                refresh();
              });
            }}
          >
            <label>
              Résumé humain
              <textarea
                rows={8}
                value={humanSummary}
                required
                onChange={(e) => setHumanSummary(e.target.value)}
              />
            </label>
            <button className="primary">Enregistrer l’analyse humaine</button>
          </form>
          {analysisSaved && (
            <p role="status">
              Analyse enregistrée. Vous pouvez reprendre le travail.
            </p>
          )}
        </>
      ) : (
        <>
          <p className="muted">
            Analyse linguistique contextualisée ; aucune modification
            automatique du passage.
          </p>
          <label>
            Phrase sélectionnée (facultatif)
            <textarea
              value={selection}
              onChange={(e) => setSelection(e.target.value)}
              placeholder="Collez la phrase à examiner"
            />
          </label>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setBusy(true);
              void run(async () =>
                setAnswer(
                  await send(`/segments/${segment.id}/ask`, {
                    question,
                    selection,
                  }),
                ),
              ).finally(() => setBusy(false));
            }}
          >
            <label>
              Question
              <textarea
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="Donne-moi trois variantes qui préservent le double sens."
                required
              />
            </label>
            <button className="primary" disabled={busy}>
              {busy ? "Consultation du modèle…" : "Demander à l’IA"}
            </button>
          </form>
          {answer != null && <pre>{JSON.stringify(answer, null, 2)}</pre>}
        </>
      )}
    </aside>
  );
}
