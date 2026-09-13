import { useEffect, useState } from "react";
import { api, send } from "../api";
import { registerTranslations, useI18n } from "../i18n";
import type { Chapter, Issue, Project, Provider, Run, Segment } from "../types";
import { Inspector, SegmentRow } from "./Editor";

const PAGE_SIZE = 250;

const translations: Record<string, string> = {
  "Relecture humaine": "Human review",
  "Validations de traduction": "Translation validations",
  "Corrigez si nécessaire, puis validez. Le passage disparaîtra de cette file une fois la décision enregistrée.":
    "Correct as needed, then validate. The passage will disappear from this queue once the decision is saved.",
  "Actualisation…": "Refreshing…",
  "Actualiser la file": "Refresh queue",
  "{count} passages à vérifier": "{count} passages to review",
  "à vérifier": "to review",
  "Bilan de la revue finale": "Final review summary",
  Examinés: "Reviewed",
  Résolus: "Resolved",
  Corrigés: "Corrected",
  "À vérifier": "To review",
  Protégés: "Protected",
  Échecs: "Failures",
  "Revue finale IA": "AI final review",
  "Lancée automatiquement après la traduction du livre.":
    "Started automatically after translating the book.",
  "La revue automatique est désactivée sur cette installation.":
    "Automatic review is disabled on this installation.",
  "L’IA réexamine les alertes, tente une correction puis la vérifie. Les choix humains restent protégés. {count} passage(s) éligible(s).":
    "AI re-examines alerts, attempts a correction, then verifies it. Human choices remain protected. {count} eligible passage(s).",
  "Recherche terminologique SearXNG disponible si nécessaire.":
    "SearXNG terminology search is available when needed.",
  "Recherche web désactivée : analyse fondée sur le livre et son contexte.":
    "Web search disabled: analysis is based on the book and its context.",
  "Mise en file…": "Queuing…",
  "Lancer la revue IA": "Start AI review",
  "Vérification des passages éligibles…": "Checking eligible passages…",
  "Aucun passage éligible : les décisions humaines sont protégées, ou il ne reste rien à réexaminer.":
    "No eligible passages: human decisions are protected, or nothing remains to be re-examined.",
  "Un travail occupe ce livre. Terminez-le ou annulez-le pour lancer une revue manuelle ; une simple pause ne libère pas le livre.":
    "A job is using this book. Finish or cancel it to start a manual review; simply pausing does not release the book.",
  "Disponible : le provider du livre réexaminera les passages signalés. Les modifications humaines seront conservées.":
    "Available: the book's provider will re-examine flagged passages. Human changes will be retained.",
  "Reprise ciblée": "Targeted retry",
  "{count} passage(s) refusé(s) et ignoré(s)":
    "{count} refused and skipped passage(s)",
  "Après deux refus du provider initial, Libris poursuit le livre. Choisissez ici un autre modèle, par exemple un modèle non censuré, pour ne retraduire que ces passages.":
    "After two refusals from the initial provider, Libris continues the book. Choose another model here, such as an uncensored model, to retranslate only these passages.",
  "Afficher les passages concernés": "Show affected passages",
  "passage {position}": "passage {position}",
  "Provider de reprise": "Retry provider",
  "Choisir un autre provider": "Choose another provider",
  "Retraduire les passages refusés": "Retranslate refused passages",
  "Chargement des validations…": "Loading validations…",
  "Passage {position}": "Passage {position}",
  "{count} incertitude(s)": "{count} uncertainty(ies)",
  "{count} remarque(s) IA": "{count} AI comment(s)",
  "Contrôle manuel demandé": "Manual check requested",
  "Avis de l’IA": "AI opinion",
  "Points à arbitrer avant la validation humaine":
    "Points to resolve before human validation",
  "Ce qui fait douter l’IA": "What makes AI uncertain",
  "Amélioration proposée": "Proposed improvement",
  Proposition: "Proposal",
  "Application…": "Applying…",
  "Accepter cette proposition": "Accept this proposal",
  "Refus…": "Rejecting…",
  "Refuser cette proposition": "Reject this proposal",
  "Ce passage a été signalé par un contrôle technique. Le détail est affiché ci-dessus ; aucune proposition IA n’a été enregistrée pour ce signalement.":
    "This passage was flagged by a technical check. The details are shown above; no AI proposal was saved for this report.",
  "Aucune validation en attente.": "No validations pending.",
  "Les passages signalés par l’IA ou les contrôles apparaîtront ici.":
    "Passages flagged by AI or checks will appear here.",
};

registerTranslations(translations);

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
  const { t } = useI18n();
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
    summary: project.progress?.review || {
      examined: 0,
      total: 0,
      resolved: 0,
      needs_human: 0,
      remaining: 0,
      protected: 0,
      revised: 0,
      failed: 0,
    },
  });
  const [startingReview, setStartingReview] = useState(false);

  useEffect(() => {
    if (project.progress?.review) {
      setFinalReview((current) => ({
        ...current,
        summary: project.progress!.review,
      }));
    }
  }, [project.progress?.review]);

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
          <p className="eyebrow">{t("Relecture humaine")}</p>
          <h2>{t("Validations de traduction")}</h2>
          <p className="muted">
            {t(
              "Corrigez si nécessaire, puis validez. Le passage disparaîtra de cette file une fois la décision enregistrée.",
            )}
          </p>
        </div>
        <div className="validation-heading-actions">
          <button disabled={reloading} onClick={refreshQueue}>
            {reloading ? t("Actualisation…") : t("Actualiser la file")}
          </button>
          <span
            className="validation-count"
            aria-label={t("{count} passages à vérifier").replace(
              "{count}",
              String(segments.length),
            )}
          >
            {segments.length}
            <small>{t("à vérifier")}</small>
          </span>
        </div>
      </div>

      <div
        className="review-outcome"
        aria-label={t("Bilan de la revue finale")}
      >
        {[
          [t("Examinés"), finalReview.summary.examined],
          [t("Résolus"), finalReview.summary.resolved],
          [t("Corrigés"), finalReview.summary.revised],
          [t("À vérifier"), finalReview.summary.remaining],
          [t("Protégés"), finalReview.summary.protected],
          [t("Échecs"), finalReview.summary.failed],
        ].map(([label, value]) => (
          <span key={label}>
            <strong>{value}</strong>
            <small>{label}</small>
          </span>
        ))}
      </div>

      <div className="notice">
        <strong>{t("Revue finale IA")}</strong>
        <p>
          {finalReview.automatic
            ? t("Lancée automatiquement après la traduction du livre.")
            : t(
                "La revue automatique est désactivée sur cette installation.",
              )}{" "}
          {t(
            "L’IA réexamine les alertes, tente une correction puis la vérifie. Les choix humains restent protégés. {count} passage(s) éligible(s).",
          ).replace("{count}", String(finalReview.eligible))}
        </p>
        <p className="muted">
          {finalReview.web_enabled
            ? t("Recherche terminologique SearXNG disponible si nécessaire.")
            : t(
                "Recherche web désactivée : analyse fondée sur le livre et son contexte.",
              )}
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
          {startingReview ? t("Mise en file…") : t("Lancer la revue IA")}
        </button>
        <p className="review-reason">
          {loading
            ? t("Vérification des passages éligibles…")
            : !finalReview.eligible
              ? t(
                  "Aucun passage éligible : les décisions humaines sont protégées, ou il ne reste rien à réexaminer.",
                )
              : [
                    "pending",
                    "waiting",
                    "paused",
                    "blocked",
                    "analyzing",
                    "translating",
                    "reviewing",
                    "syncing",
                  ].includes(project.status)
                ? t(
                    "Un travail occupe ce livre. Terminez-le ou annulez-le pour lancer une revue manuelle ; une simple pause ne libère pas le livre.",
                  )
                : t(
                    "Disponible : le provider du livre réexaminera les passages signalés. Les modifications humaines seront conservées.",
                  )}
        </p>
      </div>

      {!!refused.length && (
        <section className="refusal-recovery">
          <div>
            <p className="eyebrow">{t("Reprise ciblée")}</p>
            <h3>
              {t("{count} passage(s) refusé(s) et ignoré(s)").replace(
                "{count}",
                String(refused.length),
              )}
            </h3>
            <p className="muted">
              {t(
                "Après deux refus du provider initial, Libris poursuit le livre. Choisissez ici un autre modèle, par exemple un modèle non censuré, pour ne retraduire que ces passages.",
              )}
            </p>
            <details>
              <summary>{t("Afficher les passages concernés")}</summary>
              <ol>
                {refused.map((segment) => (
                  <li key={segment.id}>
                    {chapterNames.get(segment.chapter_id) || segment.section} ·
                    {t("passage {position}").replace(
                      "{position}",
                      String(segment.position + 1),
                    )}
                  </li>
                ))}
              </ol>
            </details>
          </div>
          <div className="refusal-recovery-action">
            <label>
              {t("Provider de reprise")}
              <select
                value={recoveryProvider}
                onChange={(event) => setRecoveryProvider(event.target.value)}
              >
                <option value="">{t("Choisir un autre provider")}</option>
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
              {t("Retraduire les passages refusés")}
            </button>
          </div>
        </section>
      )}

      {loading ? (
        <p className="muted">{t("Chargement des validations…")}</p>
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
                    <small>
                      {t("Passage {position}").replace(
                        "{position}",
                        String(segment.position + 1),
                      )}
                    </small>
                  </div>
                  <div className="validation-reasons">
                    {!!segment.uncertainties.length && (
                      <span>
                        {t("{count} incertitude(s)").replace(
                          "{count}",
                          String(segment.uncertainties.length),
                        )}
                      </span>
                    )}
                    {!!segment.critique.length && (
                      <span>
                        {t("{count} remarque(s) IA").replace(
                          "{count}",
                          String(segment.critique.length),
                        )}
                      </span>
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
                        <span>{t("Contrôle manuel demandé")}</span>
                      )}
                  </div>
                </header>
                {(segment.uncertainties.length > 0 ||
                  segment.critique.length > 0) && (
                  <div className="ai-guidance">
                    <div className="ai-guidance-title">
                      <img src="/assets/libris-icon.png" alt="" />
                      <div>
                        <strong>{t("Avis de l’IA")}</strong>
                        <small>
                          {t("Points à arbitrer avant la validation humaine")}
                        </small>
                      </div>
                    </div>
                    <div className="ai-guidance-list">
                      {segment.uncertainties.map((uncertainty, index) => (
                        <article className="ai-doubt" key={`doubt-${index}`}>
                          <strong>{t("Ce qui fait douter l’IA")}</strong>
                          <p>{uncertainty}</p>
                        </article>
                      ))}
                      {segment.critique.map((critique, index) => (
                        <article
                          className={`ai-suggestion ${critique.severity}`}
                          key={`${critique.unit_id}-${critique.category}-${index}`}
                        >
                          <div className="ai-suggestion-label">
                            <strong>{t("Amélioration proposée")}</strong>
                            <span>{critique.category}</span>
                          </div>
                          <p>{critique.description}</p>
                          <blockquote>
                            <strong>{t("Proposition")}</strong>
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
                                ? t("Application…")
                                : t("Accepter cette proposition")}
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
                                ? t("Refus…")
                                : t("Refuser cette proposition")}
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
                      {t(
                        "Ce passage a été signalé par un contrôle technique. Le détail est affiché ci-dessus ; aucune proposition IA n’a été enregistrée pour ce signalement.",
                      )}
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
          <h2>{t("Aucune validation en attente.")}</h2>
          <p>
            {t(
              "Les passages signalés par l’IA ou les contrôles apparaîtront ici.",
            )}
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
