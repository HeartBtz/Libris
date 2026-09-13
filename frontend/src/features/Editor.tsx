import { useEffect, useLayoutEffect, useState } from "react";
import { api, date, labels, send } from "../api";
import { registerTranslations, useI18n } from "../i18n";
import type {
  Chapter,
  LLMRequest,
  Project,
  Run,
  Segment,
  Unit,
  Version,
} from "../types";

const translations: Record<string, string> = {
  "Filtrer les passages": "Filter passages",
  "Tous les passages": "All passages",
  "À vérifier": "Needs review",
  Erreurs: "Errors",
  Incertitudes: "Uncertainties",
  "Refus du provider": "Provider refusals",
  "Originaux conservés": "Source retained",
  Prévisualiser: "Preview",
  "Instructions pour cette section": "Instructions for this section",
  Instructions: "Instructions",
  "Retraduire cette section ? Les corrections humaines seront conservées et les résultats proposés dans l’historique.":
    "Retranslate this section? Human corrections will be retained and proposed results will appear in the history.",
  "Retraduire la section": "Retranslate section",
  Source: "Source",
  Traduction: "Translation",
  "Aucun passage pour ce filtre.": "No passages for this filter.",
  Précédents: "Previous",
  "Passages {start}–{end}": "Passages {start}–{end}",
  Suivants: "Next",
  "Prévisualisation du chapitre": "Chapter preview",
  "Rendu simplifié": "Simplified rendering",
  Fermer: "Close",
  "Les passages non traduits restent en langue source. CSS simplifiée, scripts et ressources externes désactivés.":
    "Untranslated passages remain in the source language. Simplified CSS, scripts, and external resources are disabled.",
  "Rendu du chapitre": "Chapter rendering",
  "Correction enregistrée. Ajouter une expression au glossaire ?":
    "Correction saved. Add an expression to the glossary?",
  "Expression source": "Source expression",
  "Traduction à conserver": "Translation to retain",
  "Instruction pour cette retraduction": "Instruction for this retranslation",
  unité: "unit",
  s: "s",
  "Validé humainement": "Human-validated",
  "Correction humaine protégée": "Protected human correction",
  "Version {revision}": "Version {revision}",
  " · Non enregistré": " · Unsaved",
  "Traduction passage {passage} unité {unit}":
    "Translation passage {passage} unit {unit}",
  "La traduction apparaîtra ici…": "The translation will appear here…",
  "Une nouvelle version est arrivée. Votre saisie est conservée ; enregistrement soumis à vérification de version.":
    "A new version has arrived. Your input is retained; saving is subject to version verification.",
  "Original conservé par décision humaine. Ce passage n’est pas compté comme traduit ; utilisez l’export partiel ou saisissez une traduction.":
    "Source retained by human decision. This passage is not counted as translated; use the partial export or enter a translation.",
  "Refus enregistré. Vous pouvez saisir une traduction ou ouvrir l’inspecteur pour ajouter une analyse humaine.":
    "Refusal recorded. You can enter a translation or open the inspector to add a human analysis.",
  "Conserver le texte source pour ce passage ? Il restera signalé comme non traduit et sera disponible dans l’export partiel. Cela ne remplace pas une analyse humaine manquante.":
    "Retain the source text for this passage? It will remain marked as untranslated and be available in the partial export. This does not replace missing human analysis.",
  "Conserver l’original pour l’export": "Retain source for export",
  "{count} incertitude(s)": "{count} uncertainty(ies)",
  Enregistrer: "Save",
  Valider: "Validate",
  "Retraduire ▾": "Retranslate ▾",
  Retraduire: "Retranslate",
  "Avec plus de contexte": "With more context",
  "Avec une instruction": "With an instruction",
  "Contexte / historique / Ask AI": "Context / history / Ask AI",
  tentative: "attempt",
  "Contexte réellement sélectionné / écarté":
    "Context actually selected / excluded",
  "Prompt final": "Final prompt",
  "Réponse interprétée": "Parsed response",
  "Réponse brute du provider": "Raw provider response",
  "Inspecteur du passage": "Passage inspector",
  "Passage {position}": "Passage {position}",
  "Fermer l’inspecteur": "Close inspector",
  Contexte: "Context",
  Historique: "History",
  Critique: "Critique",
  "Analyse humaine": "Human analysis",
  "Requête enregistrée": "Saved request",
  "Aucune requête enregistrée pour ce passage.":
    "No saved request for this passage.",
  "Appliquée lors de sa création": "Applied when created",
  Proposition: "Proposal",
  "Restaurer comme correction humaine": "Restore as human correction",
  "Résumez les informations nécessaires à la continuité : événements, personnages et références. Cette saisie sera enregistrée comme une analyse humaine, sans appel au modèle.":
    "Summarize the information needed for continuity: events, characters, and references. This input will be saved as a human analysis without calling the model.",
  "Texte source du passage": "Passage source text",
  "Résumé humain": "Human summary",
  "Enregistrer l’analyse humaine": "Save human analysis",
  "Analyse enregistrée. Vous pouvez reprendre le travail.":
    "Analysis saved. You can resume work.",
  "Analyse linguistique contextualisée ; aucune modification automatique du passage.":
    "Contextualized linguistic analysis; no automatic changes to the passage.",
  "Phrase sélectionnée (facultatif)": "Selected sentence (optional)",
  "Collez la phrase à examiner": "Paste the sentence to examine",
  Question: "Question",
  "Donne-moi trois variantes qui préservent le double sens.":
    "Give me three variants that preserve the double meaning.",
  "Consultation du modèle…": "Consulting the model…",
  "Demander à l’IA": "Ask AI",
};

registerTranslations(translations);

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
  const { t } = useI18n();
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
            aria-label={t("Filtrer les passages")}
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">{t("Tous les passages")}</option>
            <option value="check">{t("À vérifier")}</option>
            <option value="error">{t("Erreurs")}</option>
            <option value="uncertain">{t("Incertitudes")}</option>
            <option value="refused">{t("Refus du provider")}</option>
            <option value="source_retained">{t("Originaux conservés")}</option>
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
            {t("Prévisualiser")}
          </button>
          <button
            onClick={() =>
              void run(async () => {
                const instructions = window.prompt(
                  t("Instructions pour cette section"),
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
            {t("Instructions")}
          </button>
          <button
            onClick={() => {
              if (
                confirm(
                  t(
                    "Retraduire cette section ? Les corrections humaines seront conservées et les résultats proposés dans l’historique.",
                  ),
                )
              )
                void run(chapterAction);
            }}
          >
            {t("Retraduire la section")}
          </button>
        </div>
      </div>
      <div className="column-labels">
        <span>
          {t("Source")} · {project.source_language}
        </span>
        <span>
          {t("Traduction")} · {project.target_language}
        </span>
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
          <div className="empty">{t("Aucun passage pour ce filtre.")}</div>
        )}
      </div>
      <div className="pagination">
        <button
          disabled={offset === 0}
          onClick={() => setOffset((v) => Math.max(0, v - 50))}
        >
          ← {t("Précédents")}
        </button>
        <span>
          {t("Passages {start}–{end}")
            .replace("{start}", String(offset + 1))
            .replace("{end}", String(offset + segments.length))}
        </span>
        <button
          disabled={segments.length < 50}
          onClick={() => setOffset((v) => v + 50)}
        >
          {t("Suivants")} →
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
            aria-label={t("Prévisualisation du chapitre")}
          >
            <header>
              <h2>{t("Rendu simplifié")}</h2>
              <button onClick={() => setPreview("")}>{t("Fermer")}</button>
            </header>
            <p className="muted">
              {t(
                "Les passages non traduits restent en langue source. CSS simplifiée, scripts et ressources externes désactivés.",
              )}
            </p>
            <iframe
              title={t("Rendu du chapitre")}
              sandbox=""
              srcDoc={preview}
            />
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
  const { t } = useI18n();
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
        confirm(
          t("Correction enregistrée. Ajouter une expression au glossaire ?"),
        )
      ) {
        const source = prompt(t("Expression source"));
        if (!source) return;
        const translation = prompt(t("Traduction à conserver"));
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
      ? prompt(t("Instruction pour cette retraduction"))
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
            {segment.units.length} {t("unité")}
            {segment.units.length > 1 ? t("s") : ""}
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
              ? t("Validé humainement")
              : labels[segment.status] || segment.status}
          </span>
          <span>
            {segment.human
              ? t("Correction humaine protégée")
              : t("Version {revision}").replace(
                  "{revision}",
                  String(segment.revision),
                )}
            {dirty ? t(" · Non enregistré") : ""}
          </span>
        </div>
        {units.map((u, i) => (
          <textarea
            key={u.id}
            aria-label={t("Traduction passage {passage} unité {unit}")
              .replace("{passage}", String(segment.position + 1))
              .replace("{unit}", String(i + 1))}
            value={u.text}
            placeholder={t("La traduction apparaîtra ici…")}
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
            {t(
              "Une nouvelle version est arrivée. Votre saisie est conservée ; enregistrement soumis à vérification de version.",
            )}
          </p>
        )}
        {segment.error && <p className="inline-error">{segment.error}</p>}
        {segment.retained_source && (
          <p className="notice">
            {t(
              "Original conservé par décision humaine. Ce passage n’est pas compté comme traduit ; utilisez l’export partiel ou saisissez une traduction.",
            )}
          </p>
        )}
        {segment.status === "refused" && (
          <div className="notice">
            {t(
              "Refus enregistré. Vous pouvez saisir une traduction ou ouvrir l’inspecteur pour ajouter une analyse humaine.",
            )}
            <button
              onClick={() => {
                if (
                  confirm(
                    t(
                      "Conserver le texte source pour ce passage ? Il restera signalé comme non traduit et sera disponible dans l’export partiel. Cela ne remplace pas une analyse humaine manquante.",
                    ),
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
              {t("Conserver l’original pour l’export")}
            </button>
          </div>
        )}
        {!!segment.uncertainties.length && (
          <details>
            <summary>
              {t("{count} incertitude(s)").replace(
                "{count}",
                String(segment.uncertainties.length),
              )}
            </summary>
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
            {t("Enregistrer")}
          </button>
          <button
            disabled={busy || !units.every((u) => u.text)}
            onClick={() => void save(true)}
          >
            {t("Valider")}
          </button>
          <details className="retranslate">
            <summary>{t("Retraduire ▾")}</summary>
            <div>
              <button onClick={() => void run(() => translate())}>
                {t("Retraduire")}
              </button>
              <button onClick={() => void run(() => translate(true))}>
                {t("Avec plus de contexte")}
              </button>
              <button onClick={() => void run(() => translate(false, true))}>
                {t("Avec une instruction")}
              </button>
            </div>
          </details>
          <button onClick={inspect}>
            {t("Contexte / historique / Ask AI")}
          </button>
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
  const { t } = useI18n();
  return (
    <>
      <div className="metrics-inline">
        {request.model} · {request.duration.toFixed(1)} s · {t("tentative")}{" "}
        {request.attempt}/5 · {request.cached ? "cache" : "provider"}
      </div>
      {request.error && <p className="inline-error">{request.error}</p>}
      <details open>
        <summary>{t("Contexte réellement sélectionné / écarté")}</summary>
        <pre>{JSON.stringify(request.context, null, 2)}</pre>
      </details>
      {request.messages?.map((m, i) => (
        <details key={i}>
          <summary>
            {t("Prompt final")} · {m.role}
          </summary>
          <pre>{m.content}</pre>
        </details>
      ))}
      <details>
        <summary>{t("Réponse interprétée")}</summary>
        <pre>{JSON.stringify(request.parsed, null, 2)}</pre>
      </details>
      <details>
        <summary>{t("Réponse brute du provider")}</summary>
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
  const { t } = useI18n();
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
      aria-label={t("Inspecteur du passage")}
    >
      <header>
        <h2>
          {t("Passage {position}").replace(
            "{position}",
            String(segment.position + 1),
          )}
        </h2>
        <button onClick={close} aria-label={t("Fermer l’inspecteur")}>
          ×
        </button>
      </header>
      <div className="tabs">
        {[
          ["context", t("Contexte")],
          ["history", t("Historique")],
          ["review", t("Critique")],
          ["ask", "Ask AI"],
          ["analysis", t("Analyse humaine")],
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
                aria-label={t("Requête enregistrée")}
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
            <p className="muted">
              {t("Aucune requête enregistrée pour ce passage.")}
            </p>
          )}
        </>
      ) : tab === "history" ? (
        <>
          {versions.map((v) => (
            <section className="version" key={v.id}>
              <strong>
                {v.origin} ·{" "}
                {v.applied
                  ? t("Appliquée lors de sa création")
                  : t("Proposition")}
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
                {t("Restaurer comme correction humaine")}
              </button>
            </section>
          ))}
        </>
      ) : tab === "review" ? (
        <pre>{JSON.stringify(segment.critique, null, 2)}</pre>
      ) : tab === "analysis" ? (
        <>
          <p className="muted">
            {t(
              "Résumez les informations nécessaires à la continuité : événements, personnages et références. Cette saisie sera enregistrée comme une analyse humaine, sans appel au modèle.",
            )}
          </p>
          <details>
            <summary>{t("Texte source du passage")}</summary>
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
              {t("Résumé humain")}
              <textarea
                rows={8}
                value={humanSummary}
                required
                onChange={(e) => setHumanSummary(e.target.value)}
              />
            </label>
            <button className="primary">
              {t("Enregistrer l’analyse humaine")}
            </button>
          </form>
          {analysisSaved && (
            <p role="status">
              {t("Analyse enregistrée. Vous pouvez reprendre le travail.")}
            </p>
          )}
        </>
      ) : (
        <>
          <p className="muted">
            {t(
              "Analyse linguistique contextualisée ; aucune modification automatique du passage.",
            )}
          </p>
          <label>
            {t("Phrase sélectionnée (facultatif)")}
            <textarea
              value={selection}
              onChange={(e) => setSelection(e.target.value)}
              placeholder={t("Collez la phrase à examiner")}
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
              {t("Question")}
              <textarea
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder={t(
                  "Donne-moi trois variantes qui préservent le double sens.",
                )}
                required
              />
            </label>
            <button className="primary" disabled={busy}>
              {busy ? t("Consultation du modèle…") : t("Demander à l’IA")}
            </button>
          </form>
          {answer != null && <pre>{JSON.stringify(answer, null, 2)}</pre>}
        </>
      )}
    </aside>
  );
}
