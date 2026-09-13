import { useEffect, useState } from "react";
import { api, send } from "../api";
import type { Chapter, Issue, Project, Provider, Run, Segment } from "../types";
import { Inspector, SegmentRow } from "./Editor";

const PAGE_SIZE = 250;

export function ValidationPanel({
  project,
  chapters,
  run,
  refresh,
}: {
  project: Project;
  chapters: Chapter[];
  run: Run;
  refresh: () => void;
}) {
  const [segments, setSegments] = useState<Segment[]>([]);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [refused, setRefused] = useState<Segment[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [recoveryProvider, setRecoveryProvider] = useState("");
  const [selected, setSelected] = useState<Segment | null>(null);
  const [loading, setLoading] = useState(true);
  const [reloading, setReloading] = useState(false);
  const [reload, setReload] = useState(0);
  const [accepting, setAccepting] = useState("");
  const [finalReview, setFinalReview] = useState({
    automatic: false,
    web_enabled: false,
    eligible: 0,
  });
  const [startingReview, setStartingReview] = useState(false);

  useEffect(() => {
    let active = true;
    void run(async () => {
      async function segmentsWithStatus(status: string) {
        const found: Segment[] = [];
        let offset = 0;
        while (true) {
          const page = await api<Segment[]>(
            `/projects/${project.id}/segments?status=${status}&offset=${offset}&limit=${PAGE_SIZE}`,
          );
          found.push(...page);
          if (page.length < PAGE_SIZE) return found;
          offset += PAGE_SIZE;
        }
      }
      const [
        found,
        refusedSegments,
        projectIssues,
        availableProviders,
        reviewInfo,
      ] = await Promise.all([
        segmentsWithStatus("check"),
        segmentsWithStatus("refused"),
        api<Issue[]>(`/projects/${project.id}/issues`),
        api<Provider[]>("/providers"),
        api<typeof finalReview>(`/projects/${project.id}/final-review`),
      ]);
      if (active) {
        setSegments(found);
        setRefused(refusedSegments);
        setProviders(availableProviders);
        setFinalReview(reviewInfo);
        setRecoveryProvider((current) =>
          current &&
          availableProviders.some((provider) => provider.id === current)
            ? current
            : availableProviders.find(
                (provider) => provider.id !== project.provider_id,
              )?.id || "",
        );
        setIssues(projectIssues.filter((issue) => !issue.resolved));
        setSelected((current) =>
          current
            ? found.find((segment) => segment.id === current.id) || null
            : null,
        );
      }
    }).finally(() => {
      if (active) {
        setLoading(false);
        setReloading(false);
      }
    });
    return () => {
      active = false;
    };
  }, [project.id, run, reload]);

  function refreshQueue() {
    setReloading(true);
    setReload((value) => value + 1);
  }

  function refreshAfterAction() {
    refresh();
    refreshQueue();
  }

  const chapterNames = new Map(
    chapters.map((chapter) => [chapter.id, chapter.title]),
  );
  const issuesBySegment = new Map<string, Issue[]>();
  for (const issue of issues) {
    if (!issue.segment_id) continue;
    const current = issuesBySegment.get(issue.segment_id) || [];
    current.push(issue);
    issuesBySegment.set(issue.segment_id, current);
  }

  return (
    <section className="validation-panel">
      <div className="validation-heading">
        <div>
          <p className="eyebrow">Relecture humaine</p>
          <h2>Validations de traduction</h2>
          <p className="muted">
            Corrigez si nécessaire, puis validez. Le passage disparaîtra de
            cette file une fois la décision enregistrée.
          </p>
        </div>
        <div className="validation-heading-actions">
          <button disabled={reloading} onClick={refreshQueue}>
            {reloading ? "Actualisation…" : "Actualiser la file"}
          </button>
          <span
            className="validation-count"
            aria-label={`${segments.length} passages à vérifier`}
          >
            {segments.length}
            <small>à vérifier</small>
          </span>
        </div>
      </div>

      <div className="notice">
        <strong>Revue finale IA</strong>
        <p>
          {finalReview.automatic
            ? "Lancée automatiquement après la traduction du livre."
            : "La revue automatique est désactivée sur cette installation."}{" "}
          L’IA réexamine les alertes, tente une correction puis la vérifie. Les
          choix humains restent protégés. {finalReview.eligible} passage(s)
          éligible(s).
        </p>
        <p className="muted">
          {finalReview.web_enabled
            ? "Recherche terminologique SearXNG disponible si nécessaire."
            : "Recherche web désactivée : analyse fondée sur le livre et son contexte."}
        </p>
        <button
          disabled={
            startingReview ||
            !finalReview.eligible ||
            [
              "pending",
              "waiting",
              "paused",
              "blocked",
              "analyzing",
              "translating",
              "reviewing",
              "syncing",
            ].includes(project.status)
          }
          onClick={() => {
            setStartingReview(true);
            void run(async () => {
              try {
                await send(`/projects/${project.id}/jobs`, {
                  operation: "resolve_validations",
                });
                refreshAfterAction();
              } finally {
                setStartingReview(false);
              }
            });
          }}
        >
          {startingReview ? "Mise en file…" : "Lancer la revue IA"}
        </button>
      </div>

      {!!refused.length && (
        <section className="refusal-recovery">
          <div>
            <p className="eyebrow">Reprise ciblée</p>
            <h3>{refused.length} passage(s) refusé(s) et ignoré(s)</h3>
            <p className="muted">
              Après deux refus du provider initial, Libris poursuit le livre.
              Choisissez ici un autre modèle, par exemple un modèle non censuré,
              pour ne retraduire que ces passages.
            </p>
            <details>
              <summary>Afficher les passages concernés</summary>
              <ol>
                {refused.map((segment) => (
                  <li key={segment.id}>
                    {chapterNames.get(segment.chapter_id) || segment.section} ·
                    passage {segment.position + 1}
                  </li>
                ))}
              </ol>
            </details>
          </div>
          <div className="refusal-recovery-action">
            <label>
              Provider de reprise
              <select
                value={recoveryProvider}
                onChange={(event) => setRecoveryProvider(event.target.value)}
              >
                <option value="">Choisir un autre provider</option>
                {providers.map((provider) => (
                  <option key={provider.id} value={provider.id}>
                    {provider.name} · {provider.model}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="primary"
              disabled={!recoveryProvider}
              onClick={() =>
                void run(async () => {
                  await send(`/projects/${project.id}/jobs`, {
                    operation: "translate",
                    provider_id: recoveryProvider,
                    refused_only: true,
                    force: true,
                  });
                  refreshAfterAction();
                })
              }
            >
              Retraduire les passages refusés
            </button>
          </div>
        </section>
      )}

      {loading ? (
        <p className="muted">Chargement des validations…</p>
      ) : segments.length ? (
        <div className="validation-queue">
          {segments.map((segment) => {
            const segmentIssues = issuesBySegment.get(segment.id) || [];
            return (
              <section className="validation-item" key={segment.id}>
                <header>
                  <div>
                    <strong>
                      {chapterNames.get(segment.chapter_id) || segment.section}
                    </strong>
                    <small>Passage {segment.position + 1}</small>
                  </div>
                  <div className="validation-reasons">
                    {!!segment.uncertainties.length && (
                      <span>{segment.uncertainties.length} incertitude(s)</span>
                    )}
                    {!!segment.critique.length && (
                      <span>{segment.critique.length} remarque(s) IA</span>
                    )}
                    {segmentIssues.map((issue) => (
                      <span
                        className={issue.severity}
                        key={issue.id}
                        title={issue.message}
                      >
                        {issue.message}
                      </span>
                    ))}
                    {!segment.uncertainties.length &&
                      !segment.critique.length &&
                      !segmentIssues.length && (
                        <span>Contrôle manuel demandé</span>
                      )}
                  </div>
                </header>
                {(segment.uncertainties.length > 0 ||
                  segment.critique.length > 0) && (
                  <div className="ai-guidance">
                    <div className="ai-guidance-title">
                      <img src="/assets/libris-icon.png" alt="" />
                      <div>
                        <strong>Avis de l’IA</strong>
                        <small>
                          Points à arbitrer avant la validation humaine
                        </small>
                      </div>
                    </div>
                    <div className="ai-guidance-list">
                      {segment.uncertainties.map((uncertainty, index) => (
                        <article className="ai-doubt" key={`doubt-${index}`}>
                          <strong>Ce qui fait douter l’IA</strong>
                          <p>{uncertainty}</p>
                        </article>
                      ))}
                      {segment.critique.map((critique, index) => (
                        <article
                          className={`ai-suggestion ${critique.severity}`}
                          key={`${critique.unit_id}-${critique.category}-${index}`}
                        >
                          <div className="ai-suggestion-label">
                            <strong>Amélioration proposée</strong>
                            <span>{critique.category}</span>
                          </div>
                          <p>{critique.description}</p>
                          <blockquote>
                            <strong>Proposition</strong>
                            {critique.suggestion}
                          </blockquote>
                          <div className="ai-suggestion-actions">
                            <button
                              className="accept-ai-suggestion"
                              disabled={accepting.endsWith(
                                `${segment.id}-${index}`,
                              )}
                              onClick={() => {
                                const key = `accept-${segment.id}-${index}`;
                                setAccepting(key);
                                void run(async () => {
                                  try {
                                    await send(
                                      `/segments/${segment.id}/critique/${index}/accept`,
                                      { revision: segment.revision },
                                    );
                                    refreshAfterAction();
                                  } finally {
                                    setAccepting("");
                                  }
                                });
                              }}
                            >
                              {accepting === `accept-${segment.id}-${index}`
                                ? "Application…"
                                : "Accepter cette proposition"}
                            </button>
                            <button
                              className="reject-ai-suggestion"
                              disabled={accepting.endsWith(
                                `${segment.id}-${index}`,
                              )}
                              onClick={() => {
                                const key = `reject-${segment.id}-${index}`;
                                setAccepting(key);
                                void run(async () => {
                                  try {
                                    await send(
                                      `/segments/${segment.id}/critique/${index}/reject`,
                                      { revision: segment.revision },
                                    );
                                    refreshAfterAction();
                                  } finally {
                                    setAccepting("");
                                  }
                                });
                              }}
                            >
                              {accepting === `reject-${segment.id}-${index}`
                                ? "Refus…"
                                : "Refuser cette proposition"}
                            </button>
                          </div>
                        </article>
                      ))}
                    </div>
                  </div>
                )}
                {!segment.uncertainties.length &&
                  !segment.critique.length &&
                  segmentIssues.length > 0 && (
                    <div className="technical-guidance">
                      Ce passage a été signalé par un contrôle technique. Le
                      détail est affiché ci-dessus ; aucune proposition IA n’a
                      été enregistrée pour ce signalement.
                    </div>
                  )}
                <SegmentRow
                  segment={segment}
                  project={project}
                  run={run}
                  refresh={refreshAfterAction}
                  inspect={() => setSelected(segment)}
                />
              </section>
            );
          })}
        </div>
      ) : (
        <div className="empty validation-empty">
          <img src="/assets/libris-icon.png" alt="" />
          <h2>Aucune validation en attente.</h2>
          <p>
            Les passages signalés par l’IA ou les contrôles apparaîtront ici.
          </p>
        </div>
      )}

      {selected && (
        <Inspector
          segment={selected}
          run={run}
          close={() => setSelected(null)}
          refresh={refreshAfterAction}
        />
      )}
    </section>
  );
}
