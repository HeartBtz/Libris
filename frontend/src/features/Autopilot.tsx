import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api, date } from "../api";
import { registerTranslations, useI18n } from "../i18n";
import type { AutopilotDecision, AutopilotView, Chapter, Job, Project, Run } from "../types";
import { Badge, Button, Callout, Card, EmptyState, Field, LoadingBlock, Select, Stat } from "../ui";
import type { Tone } from "../ui";

registerTranslations({
  "Pilote automatique": "Autopilot",
  "Libris mène le livre jusqu’au résultat sans intervention ; chaque décision est consignée avec sa raison. Vous pouvez toujours corriger un passage, sans que ce soit nécessaire.":
    "Libris takes the book all the way to the result on its own; every decision is logged with its reason. You can still correct any passage, but you never have to.",
  "Actualiser le journal": "Refresh the log",
  "Chargement du pilote automatique…": "Loading the autopilot…",
  Activé: "On",
  Désactivé: "Off",
  "Le pilote automatique est désactivé pour ce livre : les étapes qui attendent une décision restent à votre charge. Réactivez-le dans les réglages du livre.":
    "The autopilot is off for this book: steps waiting for a decision are left to you. Turn it back on in the book settings.",
  "Ouvrir les réglages": "Open settings",
  "En cours": "Running",
  "Terminé · résultat prêt": "Finished · result ready",
  "Terminé · {count} passage conservé en original": "Finished · {count} passage kept in the original",
  "Terminé · {count} passages conservés en original": "Finished · {count} passages kept in the original",
  "Arrêté": "Stopped",
  "Pas encore lancé": "Not started yet",
  "Aucun travail automatique n’a encore rendu de rapport pour ce livre.":
    "No automatic run has reported on this book yet.",
  "Rapport final": "Final report",
  "Tours de convergence": "Convergence rounds",
  "Passages conservés en original": "Passages kept in the original",
  "Décisions consignées": "Logged decisions",
  "Terminé le {date}": "Finished on {date}",
  "Résultat": "Result",
  "Télécharger le résultat": "Download the result",
  "Le résultat est prêt : téléchargez-le ou choisissez un autre format dans Exporter.":
    "The result is ready: download it or pick another format under Export.",
  "Le pilote n’a pas pu traduire ces passages : leur texte original est gardé dans le résultat, avec la raison. Vous pouvez les traduire vous-même si vous le souhaitez.":
    "The autopilot could not translate these passages: their original text is kept in the result, with the reason. You may translate them yourself if you wish.",
  "Original conservé": "Source retained",
  "Ouvrir le passage": "Open the passage",
  "Voir ses décisions": "See its decisions",
  "Journal des décisions": "Decision log",
  "Les décisions les plus récentes d’abord.": "Most recent decisions first.",
  Étape: "Stage",
  "Toutes les étapes": "All stages",
  "Passage filtré": "Filtered passage",
  "Retirer le filtre de passage": "Remove the passage filter",
  "Aucune décision pour ce filtre.": "No decision for this filter.",
  "{from}–{to} sur {total}": "{from}–{to} of {total}",
  "Décisions précédentes": "Previous decisions",
  "Décisions suivantes": "Next decisions",
  Passage: "Passage",
  "Phase : {phase}": "Phase: {phase}",
  "Tour {round} sur {max}": "Round {round} of {max}",
  "Fournisseur de secours : {name}": "Fallback provider: {name}",
  "Pilote automatique en cours": "Autopilot running",
  "Pilote automatique terminé": "Autopilot finished",
  "Pilote automatique arrêté": "Autopilot stopped",
  "Voir le rapport": "See the report",
  "Rien n’est attendu de vous : le livre avance seul jusqu’au résultat.":
    "Nothing is expected from you: the book moves on its own to the result.",
  "Limites de l’installation : {rounds} tours au plus, {retries} attentes d’une panne au plus ({minutes} min).":
    "Installation limits: at most {rounds} rounds, at most {retries} waits for an outage ({minutes} min).",
  // Phases of the convergence loop and the steps before it.
  "Récupération des passages en échec": "Recovering failed passages",
  "Cohérence globale": "Global consistency",
  "Revue finale": "Final review",
  "Arbitrage IA": "AI arbitration",
  Clôture: "Settling",
  // Decision stages.
  Analyse: "Analysis",
  Traduction: "Translation",
  Récupération: "Recovery",
  Cohérence: "Consistency",
  Convergence: "Convergence",
  Mémoire: "Memory",
  Fournisseur: "Provider",
  Rapport: "Report",
  Relecture: "Review",
  Fin: "End",
  // Decision actions.
  Appliqué: "Applied",
  Accepté: "Accepted",
  Rejeté: "Rejected",
  Reporté: "Deferred",
  Sauté: "Skipped",
  Récupéré: "Recovered",
  "Traduction conservée": "Translation kept",
  "Fournisseur de secours": "Fallback provider",
  "Solution de repli": "Fallback",
  Échec: "Failed",
  Validé: "Validated",
  "Laissé non validé": "Left unvalidated",
  Lié: "Linked",
  Levé: "Cleared",
  Conservé: "Kept",
  Retiré: "Withdrawn",
  Stable: "Stable",
});

/** Phases of `checkpoint.autopilot_phase` (app/engines/autopilot/loop.py). */
const PHASES: Record<string, string> = {
  recovery: "Récupération des passages en échec",
  consistency: "Cohérence globale",
  review: "Revue finale",
  arbitration: "Arbitrage IA",
  done: "Clôture",
};
/** Decision stages, in the order a book meets them; unknown stages are shown as recorded. */
export const DECISION_STAGES: Record<string, string> = {
  analysis: "Analyse",
  memory: "Mémoire",
  translation: "Traduction",
  provider: "Fournisseur",
  recovery: "Récupération",
  consistency: "Cohérence",
  review: "Relecture",
  arbitration: "Arbitrage IA",
  convergence: "Convergence",
  settle: "Clôture",
  report: "Rapport",
  done: "Fin",
};
const ACTIONS: Record<string, [string, Tone]> = {
  applied: ["Appliqué", "success"],
  accepted: ["Accepté", "success"],
  rejected: ["Rejeté", "neutral"],
  deferred: ["Reporté", "neutral"],
  skipped: ["Sauté", "warning"],
  recovered: ["Récupéré", "success"],
  kept_translation: ["Traduction conservée", "info"],
  source_retained: ["Original conservé", "warning"],
  fallback_provider: ["Fournisseur de secours", "warning"],
  fallback: ["Solution de repli", "info"],
  failed: ["Échec", "danger"],
  validated: ["Validé", "success"],
  left_unvalidated: ["Laissé non validé", "neutral"],
  linked: ["Lié", "success"],
  cleared: ["Levé", "success"],
  kept: ["Conservé", "neutral"],
  withdrawn: ["Retiré", "neutral"],
  stable: ["Stable", "success"],
};
const PAGE = 25;

/** An answer from a server without the autopilot (or a mocked `[]`) is treated as no autopilot. */
export function normalizeAutopilot(value: unknown): AutopilotView | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const view = value as Partial<AutopilotView>;
  if (typeof view.enabled !== "boolean" || !view.decisions || !Array.isArray(view.decisions.items)) return null;
  return view as AutopilotView;
}

export async function fetchAutopilot(projectId: string, query: Record<string, string | number> = {}) {
  const search = new URLSearchParams(Object.entries(query).map(([key, value]) => [key, String(value)]));
  try {
    return normalizeAutopilot(await api<unknown>(`/projects/${projectId}/autopilot?${search}`));
  } catch {
    // Servers before 0.6 have no autopilot: the interface then keeps the 0.5 behaviour.
    return null;
  }
}

export function isAutopilotJob(job?: Job) {
  return job?.options?.autopilot === true;
}

export function phaseLabel(phase: unknown) {
  return PHASES[String(phase)] || String(phase || "");
}

export function stageLabel(stage: string) {
  return DECISION_STAGES[stage] || stage;
}

type Outcome = "running" | "completed" | "completed_with_residuals" | "failed" | "none";

export function autopilotOutcome(view: AutopilotView | null, job?: Job): Outcome {
  if (job && isAutopilotJob(job) && view?.report?.job_id !== job.id) return "running";
  return view?.report?.outcome || "none";
}

function OutcomeBadge({ outcome, residuals }: { outcome: Outcome; residuals: number }) {
  const { t, tp } = useI18n();
  const [label, tone]: [string, Tone] =
    outcome === "running"
      ? [t("En cours"), "accent"]
      : outcome === "completed"
        ? [t("Terminé · résultat prêt"), "success"]
        : outcome === "completed_with_residuals"
          ? [
              tp(
                residuals,
                "Terminé · {count} passage conservé en original",
                "Terminé · {count} passages conservés en original",
              ),
              "success",
            ]
          : outcome === "failed"
            ? [t("Arrêté"), "danger"]
            : [t("Pas encore lancé"), "neutral"];
  return (
    <Badge tone={tone} dot>
      {label}
    </Badge>
  );
}

/**
 * The live line of the workspace: what the autopilot is doing now (phase, round, fallback provider),
 * or how its last run ended with the result to download. Never asks anything of the person.
 */
export function AutopilotStatus({
  project,
  job,
  view,
  onOpen,
  download,
}: {
  project: Project;
  job?: Job;
  view: AutopilotView | null;
  onOpen: () => void;
  download: ReactNode;
}) {
  const { t, tp } = useI18n();
  if (!view) return null;
  const outcome = autopilotOutcome(view, job);
  if (outcome === "none" && !(job && isAutopilotJob(job))) return null;
  const checkpoint = job?.checkpoint || {};
  const round = Number(checkpoint.autopilot_round) || 0;
  const fallback = view.decisions.items.find(
    (decision) =>
      decision.stage === "provider" &&
      decision.action === "fallback_provider" &&
      decision.job_id === (outcome === "running" ? job?.id : view.report?.job_id),
  );
  const residuals = view.report?.residuals.length || 0;
  const details = [
    outcome === "running" && checkpoint.step === "autopilot" && checkpoint.autopilot_phase
      ? t("Phase : {phase}", { phase: t(phaseLabel(checkpoint.autopilot_phase)) })
      : "",
    outcome === "running" && round
      ? t("Tour {round} sur {max}", { round, max: view.settings.max_rounds })
      : outcome !== "running" && view.report
        ? tp(view.report.rounds, "{count} tour de convergence", "{count} tours de convergence")
        : "",
    fallback ? t("Fournisseur de secours : {name}", { name: fallback.provider || fallback.model }) : "",
  ].filter(Boolean);
  const ready = outcome === "completed" || outcome === "completed_with_residuals";
  return (
    <Callout
      tone={outcome === "failed" ? "danger" : ready ? "success" : "accent"}
      role="status"
      className="autopilot-status"
      icon="sparkles"
      title={
        <span className="row">
          {outcome === "running"
            ? t("Pilote automatique en cours")
            : outcome === "failed"
              ? t("Pilote automatique arrêté")
              : t("Pilote automatique terminé")}
          <OutcomeBadge outcome={outcome} residuals={residuals} />
        </span>
      }
      actions={
        <>
          {ready && project.stats.total > 0 && download}
          <Button size="sm" variant="ghost" onClick={onOpen}>
            {t("Voir le rapport")}
          </Button>
        </>
      }
    >
      {details.length > 0 && <span className="autopilot-details">{details.join(" · ")}</span>}
      {outcome === "running" && <span className="subtle"> {t("Rien n’est attendu de vous : le livre avance seul jusqu’au résultat.")}</span>}
      {outcome === "failed" && view.report?.reason && <span> {view.report.reason}</span>}
    </Callout>
  );
}

registerTranslations({
  "{count} tour de convergence": "{count} convergence round",
  "{count} tours de convergence": "{count} convergence rounds",
});

/** The decisions of the log, newest first, filterable by stage and passage. */
export function DecisionLog({
  project,
  run,
  tick,
  initialStage = "",
  segment,
  onSegment,
  onOpenPassage,
  title,
  description,
}: {
  project: Project;
  run: Run;
  tick: number;
  initialStage?: string;
  segment: string;
  onSegment: (segment: string) => void;
  onOpenPassage: (segment: string) => void;
  title?: ReactNode;
  description?: ReactNode;
}) {
  const { t } = useI18n();
  const [stage, setStage] = useState(initialStage);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<{ key: string; items: AutopilotDecision[]; total: number } | null>(null);
  const key = `${stage}|${segment}|${offset}`;
  useEffect(() => setOffset(0), [stage, segment]);
  useEffect(() => {
    let active = true;
    void run.background(async () => {
      const query: Record<string, string | number> = { limit: PAGE, offset };
      if (stage) query.stage = stage;
      if (segment) query.segment_id = segment;
      const view = await fetchAutopilot(project.id, query);
      if (active) setPage({ key, items: view?.decisions.items || [], total: view?.decisions.total || 0 });
    });
    return () => {
      active = false;
    };
  }, [project.id, run, tick, key, stage, segment, offset]);
  const total = page?.total || 0;
  return (
    <Card
      padded={false}
      className="decision-log"
      title={title || t("Journal des décisions")}
      description={description || t("Les décisions les plus récentes d’abord.")}
    >
      <div className="decision-toolbar">
        <Field label={t("Étape")} inline>
          <Select value={stage} onChange={(event) => setStage(event.target.value)}>
            <option value="">{t("Toutes les étapes")}</option>
            {Object.entries(DECISION_STAGES).map(([value, label]) => (
              <option key={value} value={value}>
                {t(label)}
              </option>
            ))}
          </Select>
        </Field>
        {segment && (
          <Button size="sm" variant="ghost" icon="x" aria-label={t("Retirer le filtre de passage")} onClick={() => onSegment("")}>
            {t("Passage filtré")}
          </Button>
        )}
      </div>
      {page === null || page.key !== key ? (
        <div className="card-inset">
          <LoadingBlock label={t("Chargement du pilote automatique…")} lines={3} />
        </div>
      ) : page.items.length ? (
        <ul className="series-list decision-list" aria-label={t("Journal des décisions")}>
          {page.items.map((decision) => {
            const [label, tone] = ACTIONS[decision.action] || [decision.action, "neutral" as Tone];
            return (
              <li key={decision.id} className="series-list-item decision-item">
                <div className="series-list-main">
                  <span className="decision-head">
                    <Badge>{t(stageLabel(decision.stage))}</Badge>
                    <Badge tone={tone}>{t(label)}</Badge>
                    <code className="decision-kind">{decision.kind}</code>
                  </span>
                  <span className="decision-reason">{decision.reason}</span>
                  <small className="subtle">
                    {[date(decision.created_at), [decision.provider, decision.model].filter(Boolean).join(" · ")]
                      .filter(Boolean)
                      .join(" · ")}
                  </small>
                </div>
                {decision.segment_id && (
                  <div className="series-list-actions">
                    <Button size="sm" variant="ghost" onClick={() => onOpenPassage(decision.segment_id!)}>
                      {t("Ouvrir le passage")}
                    </Button>
                    {decision.segment_id !== segment && (
                      <Button size="sm" variant="ghost" onClick={() => onSegment(decision.segment_id!)}>
                        {t("Voir ses décisions")}
                      </Button>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <EmptyState compact icon="list" title={t("Aucune décision pour ce filtre.")} />
      )}
      {total > PAGE && (
        <div className="card-footer pagination">
          <Button size="sm" variant="ghost" icon="chevronLeft" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
            {t("Décisions précédentes")}
          </Button>
          <span className="subtle tabular">
            {t("{from}–{to} sur {total}", { from: offset + 1, to: Math.min(offset + PAGE, total), total })}
          </span>
          <Button
            size="sm"
            variant="ghost"
            iconAfter="chevronRight"
            disabled={offset + PAGE >= total}
            onClick={() => setOffset(offset + PAGE)}
          >
            {t("Décisions suivantes")}
          </Button>
        </div>
      )}
    </Card>
  );
}

/** The autopilot tab: final report, residual passages, result download and the decision log. */
export function AutopilotPanel({
  project,
  chapters,
  job,
  run,
  tick,
  download,
  onOpenPassage,
  onSettings,
}: {
  project: Project;
  chapters: Chapter[];
  job?: Job;
  run: Run;
  tick: number;
  download: ReactNode;
  onOpenPassage: (segment: string) => void;
  onSettings: () => void;
}) {
  const { t } = useI18n();
  const [view, setView] = useState<AutopilotView | null | undefined>(undefined);
  const [segment, setSegment] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    void run.background(async () => {
      const value = await fetchAutopilot(project.id, { limit: 1 });
      if (active) setView(value);
    });
    return () => {
      active = false;
    };
  }, [project.id, run, tick, reload]);
  if (view === undefined) return <LoadingBlock label={t("Chargement du pilote automatique…")} lines={4} />;
  const chapterNames = new Map(chapters.map((chapter) => [chapter.id, chapter.title]));
  const report = view?.report;
  const outcome = autopilotOutcome(view, job);
  const ready = outcome === "completed" || outcome === "completed_with_residuals";
  const settings = view?.settings;
  return (
    <section className="review-panel autopilot-panel">
      <div className="panel-header">
        <div>
          <h2>{t("Pilote automatique")}</h2>
          <p className="muted">
            {t(
              "Libris mène le livre jusqu’au résultat sans intervention ; chaque décision est consignée avec sa raison. Vous pouvez toujours corriger un passage, sans que ce soit nécessaire.",
            )}
          </p>
        </div>
        <div className="panel-actions">
          {view && <Badge tone={view.enabled ? "success" : "neutral"} dot>{view.enabled ? t("Activé") : t("Désactivé")}</Badge>}
          <Button icon="refresh" onClick={() => setReload((value) => value + 1)}>
            {t("Actualiser le journal")}
          </Button>
        </div>
      </div>
      {view && !view.enabled && (
        <Callout
          tone="neutral"
          actions={
            <Button size="sm" onClick={onSettings}>
              {t("Ouvrir les réglages")}
            </Button>
          }
        >
          {t(
            "Le pilote automatique est désactivé pour ce livre : les étapes qui attendent une décision restent à votre charge. Réactivez-le dans les réglages du livre.",
          )}
        </Callout>
      )}
      <Card
        title={
          <span className="row">
            {t("Rapport final")}
            <OutcomeBadge outcome={outcome} residuals={report?.residuals.length || 0} />
          </span>
        }
        description={
          report
            ? [report.finished_at ? t("Terminé le {date}", { date: date(report.finished_at) }) : "", report.reason || ""]
                .filter(Boolean)
                .join(" · ") || undefined
            : t("Aucun travail automatique n’a encore rendu de rapport pour ce livre.")
        }
      >
        {report && (
          <div className="stat-grid">
            <Stat label={t("Tours de convergence")} value={report.rounds} />
            <Stat label={t("Passages conservés en original")} value={report.residuals.length} />
            <Stat label={t("Décisions consignées")} value={view?.decisions.total ?? 0} />
          </div>
        )}
        {ready && project.stats.total > 0 && (
          <div className="autopilot-result">
            <span className="muted">{t("Le résultat est prêt : téléchargez-le ou choisissez un autre format dans Exporter.")}</span>
            {download}
          </div>
        )}
        {settings && (
          <p className="subtle autopilot-limits">
            {t("Limites de l’installation : {rounds} tours au plus, {retries} attentes d’une panne au plus ({minutes} min).", {
              rounds: settings.max_rounds,
              retries: settings.outage_max_retries,
              minutes: Math.round(settings.outage_max_wait_seconds / 60),
            })}
          </p>
        )}
      </Card>
      {!!report?.residuals.length && (
        <Card
          padded={false}
          title={t("Passages conservés en original")}
          description={t(
            "Le pilote n’a pas pu traduire ces passages : leur texte original est gardé dans le résultat, avec la raison. Vous pouvez les traduire vous-même si vous le souhaitez.",
          )}
        >
          <ul className="series-list residual-list">
            {report.residuals.map((residual) => (
              <li key={residual.segment_id} className="series-list-item residual-item">
                <div className="series-list-main">
                  <span className="decision-head">
                    <Badge tone="warning" dot>
                      {t("Original conservé")}
                    </Badge>
                    <strong>{chapterNames.get(residual.chapter_id) || t("Passage")}</strong>
                  </span>
                  <span className="decision-reason">{residual.reason}</span>
                </div>
                <div className="series-list-actions">
                  <Button size="sm" onClick={() => onOpenPassage(residual.segment_id)}>
                    {t("Ouvrir le passage")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setSegment(residual.segment_id);
                      document.querySelector(".decision-log")?.scrollIntoView({ block: "start" });
                    }}
                  >
                    {t("Voir ses décisions")}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <DecisionLog
        project={project}
        run={run}
        tick={tick + reload}
        segment={segment}
        onSegment={setSegment}
        onOpenPassage={onOpenPassage}
      />
    </section>
  );
}
