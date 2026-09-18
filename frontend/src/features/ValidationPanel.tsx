import { useEffect, useState } from "react";
import { api, send } from "../api";
import { registerTranslations, useI18n } from "../i18n";
import type { Chapter, Issue, Project, Provider, Run, Segment } from "../types";
import { useLeaveGuard } from "../unsaved";
import {
  Badge,
  Button,
  Callout,
  Card,
  EmptyState,
  Field,
  Icon,
  LoadingBlock,
  Select,
  Stat,
  useDialogs,
} from "../ui";
import { Inspector, SegmentRow } from "./Editor";
import type { SuggestionDraft } from "./Editor";
import { EstimateNote } from "./Estimate";
import { MarkedText } from "./MarkedText";

const PAGE_SIZE = 20;
const BUSY = ["pending", "waiting", "paused", "blocked", "analyzing", "translating", "reviewing", "syncing"];

registerTranslations({
  "Validations de traduction": "Translation validations",
  "Corrigez si nécessaire, puis validez. Le passage quitte la file une fois la décision enregistrée.":
    "Correct as needed, then validate. The passage leaves the queue once the decision is saved.",
  "Actualiser la file": "Refresh queue",
  "{count} passage à vérifier": "{count} passage to review",
  "{count} passages à vérifier": "{count} passages to review",
  "Bilan de la revue finale": "Final review summary",
  Examinés: "Reviewed",
  Résolus: "Resolved",
  Corrigés: "Corrected",
  "À vérifier": "To review",
  Protégés: "Protected",
  Échecs: "Failures",
  "Revue finale IA": "AI final review",
  "Lancée automatiquement après la traduction du livre.": "Started automatically after translating the book.",
  "La revue automatique est désactivée sur cette installation.": "Automatic review is disabled on this installation.",
  "L’IA réexamine les alertes, tente une correction puis la vérifie. Les choix humains restent protégés.":
    "AI re-examines alerts, attempts a correction, then verifies it. Human choices remain protected.",
  "{count} passage éligible.": "{count} eligible passage.",
  "{count} passages éligibles.": "{count} eligible passages.",
  "Recherche terminologique SearXNG disponible si nécessaire.": "SearXNG terminology search is available when needed.",
  "Recherche web désactivée : analyse fondée sur le livre et son contexte.":
    "Web search disabled: analysis is based on the book and its context.",
  "Mise en file…": "Queuing…",
  "Lancer la revue IA": "Start AI review",
  "Lancer la revue IA ?": "Start the AI review?",
  "Le provider du livre réexaminera les passages signalés. Les modifications humaines seront conservées.":
    "The book's provider will re-examine flagged passages. Human changes will be kept.",
  Lancer: "Start",
  "Vérification des passages éligibles…": "Checking eligible passages…",
  "Aucun passage éligible : les décisions humaines sont protégées, ou il ne reste rien à réexaminer.":
    "No eligible passages: human decisions are protected, or nothing remains to be re-examined.",
  "Un travail occupe ce livre. Terminez-le ou annulez-le pour lancer une revue manuelle ; une simple pause ne libère pas le livre.":
    "A job is using this book. Finish or cancel it to start a manual review; simply pausing does not release the book.",
  "{count} passage refusé et ignoré": "{count} refused and skipped passage",
  "{count} passages refusés et ignorés": "{count} refused and skipped passages",
  "Après deux refus du provider initial, Libris poursuit le livre. Choisissez ici un autre modèle, par exemple un modèle non censuré, pour ne retraduire que ces passages.":
    "After two refusals from the initial provider, Libris continues the book. Choose another model here, such as an uncensored model, to retranslate only these passages.",
  "Afficher les passages concernés": "Show affected passages",
  "{chapter} · passage {position}": "{chapter} · passage {position}",
  "Provider de reprise": "Retry provider",
  "Choisir un autre provider": "Choose another provider",
  "Retraduire les passages refusés": "Retranslate refused passages",
  "Chargement des validations…": "Loading validations…",
  "Passage {position}": "Passage {position}",
  "{count} incertitude": "{count} uncertainty",
  "{count} incertitudes": "{count} uncertainties",
  "{count} remarque IA": "{count} AI comment",
  "{count} remarques IA": "{count} AI comments",
  "Contrôle manuel demandé": "Manual check requested",
  "Avis de l’IA": "AI opinion",
  "Ce qui fait douter l’IA": "What makes AI uncertain",
  "Amélioration proposée": "Proposed improvement",
  Proposition: "Proposal",
  "Accepter cette proposition": "Accept this proposal",
  Accepter: "Accept",
  Refuser: "Reject",
  Modifier: "Edit",
  "Modifier cette proposition": "Edit this proposal",
  "En attente de l’IA": "Queued for AI",
  "Tout accepter": "Accept all",
  "Accepter toutes les propositions ?": "Accept all proposals?",
  "Chaque proposition IA encore ouverte sera appliquée à son passage. Les corrections humaines restent protégées ; l’opération est mise en file.":
    "Every open AI proposal will be applied to its passage. Human corrections remain protected; the operation is queued.",
  "Acceptation en file…": "Queuing acceptances…",
  "Refuser cette proposition": "Reject this proposal",
  "Ce passage a été signalé par un contrôle technique. Le détail est affiché ci-dessus ; aucune proposition IA n’a été enregistrée pour ce signalement.":
    "This passage was flagged by a technical check. The details are shown above; no AI proposal was saved for this report.",
  "Aucune validation en attente.": "No validations pending.",
  "Les passages signalés par l’IA ou les contrôles apparaîtront ici.": "Passages flagged by AI or checks will appear here.",
  "Page précédente": "Previous page",
  "Page suivante": "Next page",
  "Page {page} sur {pages}": "Page {page} of {pages}",
  "La proposition est chargée dans la traduction : ajustez-la puis enregistrez ou validez.":
    "The proposal is loaded in the translation: adjust it, then save or validate.",
});

interface FinalReview {
  automatic: boolean;
  web_enabled: boolean;
  eligible: number;
  summary: NonNullable<Project["progress"]>["review"];
}

export function ValidationPanel({
  project,
  chapters,
  run,
  refresh,
  tick,
}: {
  project: Project;
  chapters: Chapter[];
  run: Run;
  refresh: () => void;
  tick: number;
}) {
  const { t, tp } = useI18n();
  const { confirm } = useDialogs();
  const confirmLeave = useLeaveGuard();
  const [page, setPage] = useState(0);
  const [segments, setSegments] = useState<Segment[] | null>(null);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [refused, setRefused] = useState<Segment[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [recoveryProvider, setRecoveryProvider] = useState("");
  const [selected, setSelected] = useState<Segment | null>(null);
  const [reloading, setReloading] = useState(false);
  const [reload, setReload] = useState(0);
  const [accepting, setAccepting] = useState("");
  const [acceptingAll, setAcceptingAll] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, SuggestionDraft>>({});
  const [finalReview, setFinalReview] = useState<FinalReview | null>(null);
  const [startingReview, setStartingReview] = useState(false);
  const flagged = project.stats.flagged;
  const pages = Math.max(1, Math.ceil(flagged / PAGE_SIZE));

  useEffect(() => {
    let active = true;
    void run
      .background(async () => {
        const [found, refusedSegments, projectIssues, availableProviders, reviewInfo] = await Promise.all([
          api<Segment[]>(`/projects/${project.id}/segments?status=check&offset=${page * PAGE_SIZE}&limit=${PAGE_SIZE}`),
          api<Segment[]>(`/projects/${project.id}/segments?status=refused&offset=0&limit=100`),
          api<Issue[]>(`/projects/${project.id}/issues`),
          api<Provider[]>("/providers"),
          api<FinalReview>(`/projects/${project.id}/final-review`),
        ]);
        if (!active) return;
        setSegments(found);
        setRefused(refusedSegments);
        setProviders(availableProviders);
        setFinalReview(reviewInfo);
        setRecoveryProvider((current) =>
          current && availableProviders.some((provider) => provider.id === current)
            ? current
            : availableProviders.find((provider) => provider.id !== project.provider_id)?.id || "",
        );
        setIssues(projectIssues.filter((issue) => !issue.resolved));
        setSelected((current) => (current ? found.find((segment) => segment.id === current.id) || null : null));
      })
      .finally(() => {
        if (active) setReloading(false);
      });
    return () => {
      active = false;
    };
  }, [project.id, run, reload, page, tick]);

  useEffect(() => {
    // Deciding on the last passages of the last page must not leave an empty page behind.
    if (page > 0 && page >= pages) setPage(pages - 1);
  }, [page, pages]);

  function refreshQueue() {
    setReloading(true);
    setReload((value) => value + 1);
  }
  function refreshAfterAction() {
    refresh();
    refreshQueue();
  }
  const chapterNames = new Map(chapters.map((chapter) => [chapter.id, chapter.title]));
  const issuesBySegment = new Map<string, Issue[]>();
  for (const issue of issues) {
    if (!issue.segment_id) continue;
    issuesBySegment.set(issue.segment_id, [...(issuesBySegment.get(issue.segment_id) || []), issue]);
  }
  const summary = finalReview?.summary || project.progress?.review;
  const busyProject = BUSY.includes(project.status);
  const reviewReason = !finalReview
    ? t("Vérification des passages éligibles…")
    : !finalReview.eligible
      ? t("Aucun passage éligible : les décisions humaines sont protégées, ou il ne reste rien à réexaminer.")
      : busyProject
        ? t(
            "Un travail occupe ce livre. Terminez-le ou annulez-le pour lancer une revue manuelle ; une simple pause ne libère pas le livre.",
          )
        : "";
  const decide = (segment: Segment, index: number, action: "accept" | "reject") => {
    const key = `${action}-${segment.id}-${index}`;
    setAccepting(key);
    void run(async () => {
      try {
        await send(`/segments/${segment.id}/critique/${index}/${action}`, { revision: segment.revision });
        refreshAfterAction();
      } finally {
        setAccepting("");
      }
    });
  };
  const changePage = async (next: number) => {
    if (!(await confirmLeave())) return;
    setPage(next);
    setSegments(null);
    document.getElementById("validation-queue")?.scrollIntoView({ block: "start" });
  };
  return (
    <section className="review-panel">
      <div className="panel-header">
        <div>
          <h2>{t("Validations de traduction")}</h2>
          <p className="muted">
            {t("Corrigez si nécessaire, puis validez. Le passage quitte la file une fois la décision enregistrée.")}
          </p>
        </div>
        <div className="panel-actions">
          <Badge tone={flagged ? "warning" : "success"}>
            {tp(flagged, "{count} passage à vérifier", "{count} passages à vérifier")}
          </Badge>
          <Button icon="refresh" loading={reloading} onClick={refreshQueue}>
            {t("Actualiser la file")}
          </Button>
          <Button
            variant="primary"
            icon="check"
            loading={acceptingAll}
            disabled={!segments?.some((segment) => segment.critique.some((item) => !item.queued))}
            onClick={() =>
              void (async () => {
                const accepted = await confirm({
                  title: t("Accepter toutes les propositions ?"),
                  message: t(
                    "Chaque proposition IA encore ouverte sera appliquée à son passage. Les corrections humaines restent protégées ; l’opération est mise en file.",
                  ),
                  confirmLabel: t("Tout accepter"),
                });
                if (!accepted) return;
                setAcceptingAll(true);
                await run(async () => {
                  try {
                    await send(`/projects/${project.id}/critiques/accept-all`);
                    refreshAfterAction();
                  } finally {
                    setAcceptingAll(false);
                  }
                });
              })()
            }
          >
            {acceptingAll ? t("Acceptation en file…") : t("Tout accepter")}
          </Button>
        </div>
      </div>

      <div className="review-overview">
        {summary && (
          <div className="stat-grid" role="group" aria-label={t("Bilan de la revue finale")}>
            <Stat label={t("Examinés")} value={summary.examined} />
            <Stat label={t("Résolus")} value={summary.resolved} tone="success" />
            <Stat label={t("Corrigés")} value={summary.revised} />
            <Stat label={t("À vérifier")} value={summary.remaining} tone={summary.remaining ? "warning" : undefined} />
            <Stat label={t("Protégés")} value={summary.protected} />
            <Stat label={t("Échecs")} value={summary.failed} tone={summary.failed ? "danger" : undefined} />
          </div>
        )}
        <Card className="ai-review-card">
          <div className="ai-review">
            <span className="ai-review-icon">
              <Icon name="sparkles" />
            </span>
            <div className="stack-sm grow">
              <strong>{t("Revue finale IA")}</strong>
              <p className="muted">
                {finalReview?.automatic
                  ? t("Lancée automatiquement après la traduction du livre.")
                  : t("La revue automatique est désactivée sur cette installation.")}{" "}
                {t("L’IA réexamine les alertes, tente une correction puis la vérifie. Les choix humains restent protégés.")}{" "}
                {finalReview && tp(finalReview.eligible, "{count} passage éligible.", "{count} passages éligibles.")}
              </p>
              <p className="subtle">
                {finalReview?.web_enabled
                  ? t("Recherche terminologique SearXNG disponible si nécessaire.")
                  : t("Recherche web désactivée : analyse fondée sur le livre et son contexte.")}
              </p>
              {reviewReason && <p className="subtle review-reason">{reviewReason}</p>}
            </div>
            <Button
              loading={startingReview}
              disabled={!finalReview?.eligible || busyProject}
              onClick={() =>
                void (async () => {
                  const accepted = await confirm({
                    title: t("Lancer la revue IA ?"),
                    message: t(
                      "Le provider du livre réexaminera les passages signalés. Les modifications humaines seront conservées.",
                    ),
                    details: <EstimateNote projectId={project.id} operation="review" />,
                    confirmLabel: t("Lancer"),
                  });
                  if (!accepted) return;
                  setStartingReview(true);
                  await run(async () => {
                    try {
                      await send(`/projects/${project.id}/jobs`, { operation: "resolve_validations" });
                      refreshAfterAction();
                    } finally {
                      setStartingReview(false);
                    }
                  });
                })()
              }
            >
              {startingReview ? t("Mise en file…") : t("Lancer la revue IA")}
            </Button>
          </div>
        </Card>
      </div>

      {!!refused.length && (
        <Card
          className="refusal-recovery"
          title={tp(refused.length, "{count} passage refusé et ignoré", "{count} passages refusés et ignorés")}
          description={t(
            "Après deux refus du provider initial, Libris poursuit le livre. Choisissez ici un autre modèle, par exemple un modèle non censuré, pour ne retraduire que ces passages.",
          )}
        >
          <div className="refusal-recovery-body">
            <details className="disclosure disclosure-plain">
              <summary>{t("Afficher les passages concernés")}</summary>
              <ol className="compact-list">
                {refused.map((segment) => (
                  <li key={segment.id}>
                    {t("{chapter} · passage {position}", {
                      chapter: chapterNames.get(segment.chapter_id) || segment.section,
                      position: segment.position + 1,
                    })}
                  </li>
                ))}
              </ol>
            </details>
            <div className="row">
              <Field label={t("Provider de reprise")} className="grow">
                <Select value={recoveryProvider} onChange={(event) => setRecoveryProvider(event.target.value)}>
                  <option value="">{t("Choisir un autre provider")}</option>
                  {providers.map((provider) => (
                    <option key={provider.id} value={provider.id}>
                      {provider.name} · {provider.model}
                    </option>
                  ))}
                </Select>
              </Field>
              <Button
                variant="primary"
                className="align-end"
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
              </Button>
            </div>
          </div>
        </Card>
      )}

      <div id="validation-queue" className="validation-queue">
        {segments === null ? (
          <LoadingBlock label={t("Chargement des validations…")} lines={5} />
        ) : segments.length ? (
          segments.map((segment) => {
            const segmentIssues = issuesBySegment.get(segment.id) || [];
            const guidance = segment.uncertainties.length > 0 || segment.critique.length > 0;
            return (
              <article className="review-item" key={segment.id} aria-label={t("Passage {position}", { position: segment.position + 1 })}>
                <header className="review-item-header">
                  <div className="review-item-title">
                    <strong>{chapterNames.get(segment.chapter_id) || segment.section}</strong>
                    <span className="subtle tabular">{t("Passage {position}", { position: segment.position + 1 })}</span>
                  </div>
                  <div className="review-reasons">
                    {!!segment.uncertainties.length && (
                      <Badge tone="warning">
                        {tp(segment.uncertainties.length, "{count} incertitude", "{count} incertitudes")}
                      </Badge>
                    )}
                    {!!segment.critique.length && (
                      <Badge tone="accent">{tp(segment.critique.length, "{count} remarque IA", "{count} remarques IA")}</Badge>
                    )}
                    {segmentIssues.map((issue) => (
                      <Badge key={issue.id} tone={issue.severity === "error" ? "danger" : "warning"} title={issue.message}>
                        {issue.message}
                      </Badge>
                    ))}
                    {!guidance && !segmentIssues.length && <Badge>{t("Contrôle manuel demandé")}</Badge>}
                  </div>
                </header>
                <div className="review-item-body">
                  <div className="review-source">
                    <span className="column-caption">
                      {t("Source")} · {project.source_language.toUpperCase()}
                    </span>
                    <div className="book-text source-text">
                      {segment.units.map((unit) => (
                        <p key={unit.id}>
                          <MarkedText text={unit.text} />
                        </p>
                      ))}
                    </div>
                  </div>
                  <SegmentRow
                    segment={segment}
                    project={project}
                    run={run}
                    refresh={refreshAfterAction}
                    inspect={() => setSelected(segment)}
                    suggestion={drafts[segment.id]}
                    hideSource
                  />
                </div>
                {guidance && (
                  <div className="ai-guidance">
                    <div className="ai-guidance-title">
                      <Icon name="sparkles" />
                      <strong>{t("Avis de l’IA")}</strong>
                    </div>
                    {segment.uncertainties.map((uncertainty, index) => (
                      <div className="ai-note" key={`doubt-${index}`}>
                        <span className="ai-note-label">{t("Ce qui fait douter l’IA")}</span>
                        <p>{uncertainty}</p>
                      </div>
                    ))}
                    {segment.critique.map((critique, index) => {
                      const pending = accepting.endsWith(`${segment.id}-${index}`);
                      return (
                        <div
                          className={`ai-suggestion severity-${critique.severity}`}
                          key={`${critique.unit_id}-${critique.category}-${index}`}
                        >
                          <div className="ai-note-label">
                            {t("Amélioration proposée")}
                            <Badge tone={critique.severity === "error" ? "danger" : "warning"}>{critique.category}</Badge>
                          </div>
                          <p>{critique.description}</p>
                          <blockquote className="book-text">
                            <span className="subtle">{t("Proposition")}</span>
                            <MarkedText text={critique.suggestion} />
                          </blockquote>
                          <div className="ai-suggestion-actions">
                            <Button
                              size="sm"
                              variant="primary"
                              icon="check"
                              disabled={critique.queued || pending}
                              loading={accepting === `accept-${segment.id}-${index}`}
                              aria-label={t("Accepter cette proposition")}
                              onClick={() => decide(segment, index, "accept")}
                            >
                              {critique.queued ? t("En attente de l’IA") : t("Accepter")}
                            </Button>
                            <Button
                              size="sm"
                              icon="edit"
                              disabled={critique.queued || pending}
                              aria-label={t("Modifier cette proposition")}
                              onClick={() =>
                                setDrafts((all) => ({
                                  ...all,
                                  [segment.id]: {
                                    unitId: critique.unit_id,
                                    text: critique.suggestion,
                                    nonce: (all[segment.id]?.nonce || 0) + 1,
                                  },
                                }))
                              }
                            >
                              {t("Modifier")}
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              icon="x"
                              disabled={pending}
                              loading={accepting === `reject-${segment.id}-${index}`}
                              aria-label={t("Refuser cette proposition")}
                              onClick={() => decide(segment, index, "reject")}
                            >
                              {t("Refuser")}
                            </Button>
                          </div>
                        </div>
                      );
                    })}
                    {drafts[segment.id] && (
                      <p className="subtle" role="status">
                        {t("La proposition est chargée dans la traduction : ajustez-la puis enregistrez ou validez.")}
                      </p>
                    )}
                  </div>
                )}
                {!guidance && segmentIssues.length > 0 && (
                  <Callout tone="neutral">
                    {t(
                      "Ce passage a été signalé par un contrôle technique. Le détail est affiché ci-dessus ; aucune proposition IA n’a été enregistrée pour ce signalement.",
                    )}
                  </Callout>
                )}
              </article>
            );
          })
        ) : (
          <Card>
            <EmptyState
              icon="check"
              title={t("Aucune validation en attente.")}
              description={t("Les passages signalés par l’IA ou les contrôles apparaîtront ici.")}
            />
          </Card>
        )}
      </div>
      {pages > 1 && (
        <div className="pagination">
          <Button size="sm" variant="ghost" icon="chevronLeft" disabled={page === 0} onClick={() => void changePage(page - 1)}>
            {t("Page précédente")}
          </Button>
          <span className="subtle tabular">{t("Page {page} sur {pages}", { page: page + 1, pages })}</span>
          <Button
            size="sm"
            variant="ghost"
            iconAfter="chevronRight"
            disabled={page + 1 >= pages}
            onClick={() => void changePage(page + 1)}
          >
            {t("Page suivante")}
          </Button>
        </div>
      )}
      {selected && (
        <Inspector segment={selected} project={project} run={run} close={() => setSelected(null)} refresh={refreshAfterAction} />
      )}
    </section>
  );
}
