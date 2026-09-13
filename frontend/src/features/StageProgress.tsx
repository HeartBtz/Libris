import { useState } from "react";
import { registerTranslations, useI18n } from "../i18n";
import type { Job, Project } from "../types";
import { duration, projectProgress } from "./progress";

const translations: Record<string, string> = {
  "Import": "Import",
  "Analyse & mémoire": "Analysis & memory",
  "Traduction": "Translation",
  "Relecture": "Review",
  "Export": "Export",
  "EPUB importé et structure chargée.": "EPUB imported and structure loaded.",
  "passages analysés": "segments analyzed",
  "sections synthétisées.": "sections synthesized.",
  "passage(s) doivent être récupérés avant la revue finale.": "segments must be recovered before the final review.",
  "passages traduits": "segments translated",
  "conservés en original.": "retained in the original.",
  "passages examinés": "segments reviewed",
  "propositions IA traitées": "AI proposals processed",
  "résolus": "resolved",
  "à vérifier.": "to review.",
  "Génération et réception du fichier en cours…": "Generating and receiving the file…",
  "Fichier reçu ; téléchargement transmis au navigateur.": "File received; download sent to the browser.",
  "Export échoué. Consultez le message d’erreur et réessayez.": "Export failed. Check the error message and try again.",
  "Livre prêt à exporter.": "Book ready to export.",
  "Terminez les alertes restantes avant l’export final.": "Resolve the remaining alerts before the final export.",
  "Avancement par étape": "Progress by stage",
  "Choisir une étape": "Choose a stage",
  "En cours": "In progress",
  "Progression": "Progress",
  "coût restant estimé": "estimated remaining cost",
  "confiance": "confidence",
  "Traitement terminé et alertes résolues sont deux mesures distinctes.": "Completed processing and resolved alerts are separate measures.",
  "Suivre l’étape active": "Follow the active stage",
};

registerTranslations(translations);

export type ExportState = "idle" | "running" | "done" | "error";

export function StageProgress({
  project,
  job: _job,
  exportState,
}: {
  project: Project;
  job?: Job;
  exportState: ExportState;
}) {
  const { t } = useI18n();
  const [selected, setSelected] = useState<number | null>(null);
  const progress = projectProgress(project);
  const stages = progress.stages.map((stage) =>
    stage.key === "export" && exportState !== "idle"
      ? {
          ...stage,
          done: exportState === "done" ? 1 : 0,
          percent: exportState === "done" ? 100 : 0,
        }
      : stage,
  );
  const active =
    exportState !== "idle"
      ? stages.findIndex((stage) => stage.key === "export")
      : stages.findIndex((stage) => stage.key === progress.active_stage);
  const index = selected ?? active;
  const stage = stages[index];
  const busy = stage.key === "export" && exportState === "running";
  const detail =
    stage.key === "import"
      ? t("EPUB importé et structure chargée.")
      : stage.key === "analysis"
        ? `${project.stats.analyzed_segments}/${project.stats.total} ${t("passages analysés")} · ${project.stats.synthesized_chapters}/${project.stats.chapters} ${t("sections synthétisées.")}`
        : stage.key === "translation"
          ? _job?.checkpoint.step === "recovery_required"
            ? `${Number(_job.checkpoint.recovery_required) || 0} ${t("passage(s) doivent être récupérés avant la revue finale.").replace("segment(s)", Number(_job.checkpoint.recovery_required) === 1 ? "segment" : "segments")}`
            : `${project.stats.translated}/${project.stats.total} ${t("passages traduits")} · ${project.stats.retained_source} ${t("conservés en original.")}`
          : stage.key === "review"
            ? progress.operation === "accept_critiques" && progress.state !== "completed"
              ? `${stage.done}/${stage.total} ${t("propositions IA traitées")}`
              : `${stage.done}/${stage.total} ${t("passages examinés")} · ${progress.review.resolved} ${t("résolus")} · ${progress.review.remaining} ${t("à vérifier.")}`
            : exportState === "running"
              ? t("Génération et réception du fichier en cours…")
              : exportState === "done"
                ? t("Fichier reçu ; téléchargement transmis au navigateur.")
                : exportState === "error"
                  ? t("Export échoué. Consultez le message d’erreur et réessayez.")
                  : stage.percent === 100
                    ? t("Livre prêt à exporter.")
                    : t("Terminez les alertes restantes avant l’export final.");
  return (
    <section className="stage-progress" aria-label={t("Avancement par étape")}>
      <div
        className="stage-selectors"
        role="group"
        aria-label={t("Choisir une étape")}
      >
        {stages.map((item, i) => (
          <button
            key={item.key}
            aria-pressed={index === i}
            aria-controls="stage-progress-detail"
            onClick={() => setSelected(i)}
          >
            {i + 1} · {t(item.label)}
          </button>
        ))}
      </div>
      <div id="stage-progress-detail">
        <div className="stage-progress-heading">
          <strong>{t(stage.label)}</strong>
          <span>{busy ? t("En cours") : `${stage.percent} %`}</span>
        </div>
        <progress
          aria-label={`${t("Progression")} ${t(stage.label)}`}
          value={busy ? undefined : stage.percent}
          max={100}
        />
        <p>{detail}</p>
        {index === active &&
          progress.estimate.remaining_seconds !== null &&
          stage.percent < 100 && (
            <small>
              {duration(progress.estimate.remaining_seconds)} · {t("coût restant estimé")} {progress.estimate.remaining_cost?.toFixed(3) ?? "—"} · {t("confiance")} {progress.estimate.confidence}
            </small>
          )}
        {stage.key === "review" && (
          <small>
            {t("Traitement terminé et alertes résolues sont deux mesures distinctes.")}
          </small>
        )}
        {selected !== null && (
          <button className="quiet" onClick={() => setSelected(null)}>
            {t("Suivre l’étape active")}
          </button>
        )}
      </div>
    </section>
  );
}
