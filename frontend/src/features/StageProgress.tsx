import { useState } from "react";
import type { Job, Project } from "../types";
import { duration, projectProgress } from "./progress";

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
      ? "EPUB importé et structure chargée."
      : stage.key === "analysis"
        ? `${project.stats.analyzed_segments}/${project.stats.total} passages analysés · ${project.stats.synthesized_chapters}/${project.stats.chapters} sections synthétisées.`
        : stage.key === "translation"
          ? _job?.checkpoint.step === "recovery_required"
            ? `${Number(_job.checkpoint.recovery_required) || 0} passage(s) doivent être récupérés avant la revue finale.`
            : `${project.stats.translated}/${project.stats.total} passages traduits · ${project.stats.retained_source} conservés en original.`
          : stage.key === "review"
            ? `${progress.review.examined}/${progress.review.total} passages examinés · ${progress.review.resolved} résolus · ${progress.review.remaining} à vérifier.`
            : exportState === "running"
              ? "Génération et réception du fichier en cours…"
              : exportState === "done"
                ? "Fichier reçu ; téléchargement transmis au navigateur."
                : exportState === "error"
                  ? "Export échoué. Consultez le message d’erreur et réessayez."
                  : stage.percent === 100
                    ? "Livre prêt à exporter."
                    : "Terminez les alertes restantes avant l’export final.";
  return (
    <section className="stage-progress" aria-label="Avancement par étape">
      <div
        className="stage-selectors"
        role="group"
        aria-label="Choisir une étape"
      >
        {stages.map((item, i) => (
          <button
            key={item.key}
            aria-pressed={index === i}
            aria-controls="stage-progress-detail"
            onClick={() => setSelected(i)}
          >
            {i + 1} · {item.label}
          </button>
        ))}
      </div>
      <div id="stage-progress-detail">
        <div className="stage-progress-heading">
          <strong>{stage.label}</strong>
          <span>{busy ? "En cours" : `${stage.percent} %`}</span>
        </div>
        <progress
          aria-label={`Progression ${stage.label}`}
          value={busy ? undefined : stage.percent}
          max={100}
        />
        <p>{detail}</p>
        {index === active &&
          progress.estimate.remaining_seconds !== null &&
          stage.percent < 100 && (
            <small>
              {duration(progress.estimate.remaining_seconds)} · coût restant
              estimé {progress.estimate.remaining_cost?.toFixed(3) ?? "—"} ·
              confiance {progress.estimate.confidence}
            </small>
          )}
        {stage.key === "review" && (
          <small>
            Traitement terminé et alertes résolues sont deux mesures distinctes.
          </small>
        )}
        {selected !== null && (
          <button className="quiet" onClick={() => setSelected(null)}>
            Suivre l’étape active
          </button>
        )}
      </div>
    </section>
  );
}
