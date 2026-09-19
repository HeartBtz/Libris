import { useState } from "react";
import type { ReactNode } from "react";
import { formatNumber, formatPercent, registerTranslations, useI18n } from "../i18n";
import type { Job, Project } from "../types";
import { Icon, ProgressBar, cx } from "../ui";
import { duration, projectProgress } from "./progress";

registerTranslations({
  Import: "Import",
  "Analyse & mémoire": "Analysis & memory",
  Traduction: "Translation",
  Relecture: "Review",
  Export: "Export",
  "EPUB importé et structure chargée.": "EPUB imported and structure loaded.",
  "{analyzed}/{total} passages analysés · {synthesized}/{chapters} sections synthétisées.":
    "{analyzed}/{total} passages analyzed · {synthesized}/{chapters} sections synthesized.",
  "{count} passage doit être récupéré avant la revue finale.": "{count} passage must be recovered before the final review.",
  "{count} passages doivent être récupérés avant la revue finale.": "{count} passages must be recovered before the final review.",
  "{translated}/{total} passages traduits · {retained} conservés en original.":
    "{translated}/{total} passages translated · {retained} retained in the original.",
  "{done}/{total} propositions IA traitées": "{done}/{total} AI proposals processed",
  "{done}/{total} passages examinés · {resolved} résolus · {remaining} ouverts.":
    "{done}/{total} passages reviewed · {resolved} resolved · {remaining} open.",
  "Génération et réception du fichier en cours…": "Generating and receiving the file…",
  "Fichier reçu ; téléchargement transmis au navigateur.": "File received; download sent to the browser.",
  "Export échoué. Consultez le message d’erreur et réessayez.": "Export failed. Check the error message and try again.",
  "Livre prêt à exporter.": "Book ready to export.",
  "Le résultat sera prêt à la fin du travail automatique ; l’export reste possible à tout moment.":
    "The result will be ready when the automatic work ends; exporting stays possible at any time.",
  "Avancement par étape": "Progress by stage",
  "Étape {number} : {stage}": "Stage {number}: {stage}",
  "En cours": "In progress",
  "Progression {stage}": "{stage} progress",
  "coût restant estimé {cost}": "estimated remaining cost {cost}",
  "confiance {level}": "confidence {level}",
  "Traitement terminé et alertes résolues sont deux mesures distinctes.":
    "Completed processing and resolved alerts are separate measures.",
  "Suivre l’étape active": "Follow the active stage",
});

export type ExportState = "idle" | "running" | "done" | "error";

export function StageProgress({
  project,
  job,
  exportState,
  status,
  counters,
}: {
  project: Project;
  job?: Job;
  exportState: ExportState;
  status?: ReactNode;
  counters?: ReactNode;
}) {
  const { t, tp } = useI18n();
  const [selected, setSelected] = useState<number | null>(null);
  const progress = projectProgress(project);
  const stages = progress.stages.map((stage) =>
    stage.key === "export" && exportState !== "idle"
      ? { ...stage, done: exportState === "done" ? 1 : 0, percent: exportState === "done" ? 100 : 0 }
      : stage,
  );
  const active =
    exportState !== "idle"
      ? stages.findIndex((stage) => stage.key === "export")
      : stages.findIndex((stage) => stage.key === progress.active_stage);
  const index = selected ?? active;
  const stage = stages[index];
  const busy = stage.key === "export" && exportState === "running";
  const stats = project.stats;
  const recovery = Number(job?.checkpoint.recovery_required) || 0;
  const detail =
    stage.key === "import"
      ? t("EPUB importé et structure chargée.")
      : stage.key === "analysis"
        ? t("{analyzed}/{total} passages analysés · {synthesized}/{chapters} sections synthétisées.", {
            analyzed: stats.analyzed_segments,
            total: stats.total,
            synthesized: stats.synthesized_chapters,
            chapters: stats.chapters,
          })
        : stage.key === "translation"
          ? job?.checkpoint.step === "recovery_required"
            ? tp(
                recovery,
                "{count} passage doit être récupéré avant la revue finale.",
                "{count} passages doivent être récupérés avant la revue finale.",
              )
            : t("{translated}/{total} passages traduits · {retained} conservés en original.", {
                translated: stats.translated,
                total: stats.total,
                retained: stats.retained_source,
              })
          : stage.key === "review"
            ? progress.operation === "accept_critiques" && progress.state !== "completed"
              ? t("{done}/{total} propositions IA traitées", { done: stage.done, total: stage.total })
              : t("{done}/{total} passages examinés · {resolved} résolus · {remaining} ouverts.", {
                  done: stage.done,
                  total: stage.total,
                  resolved: progress.review.resolved,
                  remaining: progress.review.remaining,
                })
            : exportState === "running"
              ? t("Génération et réception du fichier en cours…")
              : exportState === "done"
                ? t("Fichier reçu ; téléchargement transmis au navigateur.")
                : exportState === "error"
                  ? t("Export échoué. Consultez le message d’erreur et réessayez.")
                  : stage.percent === 100
                    ? t("Livre prêt à exporter.")
                    : t("Le résultat sera prêt à la fin du travail automatique ; l’export reste possible à tout moment.");
  return (
    <section className="stepper" aria-label={t("Avancement par étape")}>
      <ol className="stepper-steps">
        {stages.map((item, i) => {
          const complete = item.percent === 100;
          return (
            <li key={item.key} className={cx("step", complete && "is-complete", i === active && "is-active")}>
              <button
                type="button"
                className="step-button"
                aria-pressed={index === i}
                aria-controls="stage-progress-detail"
                aria-label={t("Étape {number} : {stage}", { number: i + 1, stage: t(item.label) })}
                onClick={() => setSelected(i === active ? null : i)}
              >
                <span className="step-marker" aria-hidden="true">
                  {complete ? <Icon name="check" size={12} /> : i + 1}
                </span>
                <span className="step-text">
                  <span className="step-label">{t(item.label)}</span>
                  <span className="step-percent tabular">{formatPercent(item.percent)}</span>
                </span>
              </button>
              <span className="step-track" aria-hidden="true">
                <span style={{ width: `${item.percent}%` }} />
              </span>
            </li>
          );
        })}
      </ol>
      <div id="stage-progress-detail" className="stepper-detail">
        <div className="stepper-detail-main">
          <strong>{t(stage.label)}</strong>
          <span className="muted">{detail}</span>
          {index === active && progress.estimate.remaining_seconds !== null && stage.percent < 100 && (
            <span className="subtle">
              {duration(progress.estimate.remaining_seconds)}
              {progress.estimate.remaining_cost !== null &&
                ` · ${t("coût restant estimé {cost}", {
                  cost: formatNumber(progress.estimate.remaining_cost, { maximumFractionDigits: 3 }),
                })}`}
              {` · ${t("confiance {level}", { level: progress.estimate.confidence })}`}
            </span>
          )}
          {stage.key === "review" && (
            <span className="subtle">{t("Traitement terminé et alertes résolues sont deux mesures distinctes.")}</span>
          )}
          {selected !== null && (
            <button type="button" className="link-button" onClick={() => setSelected(null)}>
              {t("Suivre l’étape active")}
            </button>
          )}
        </div>
        <div className="stepper-detail-side">
          <ProgressBar
            size="sm"
            label={t("Progression {stage}", { stage: t(stage.label) })}
            value={stage.percent}
            indeterminate={busy}
            tone={stage.percent === 100 ? "success" : "accent"}
          />
          <div className="stepper-status">
            {status}
            {counters}
          </div>
        </div>
      </div>
    </section>
  );
}
