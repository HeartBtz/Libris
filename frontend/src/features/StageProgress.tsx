import { useState } from "react";
import type { Job, Project } from "../types";

export type ExportState = "idle" | "running" | "done" | "error";

export function StageProgress({
  project,
  job,
  exportState,
}: {
  project: Project;
  job?: Job;
  exportState: ExportState;
}) {
  const [selected, setSelected] = useState<number | null>(null);
  const stats = project.stats;
  const analysisTotal = stats.total + stats.chapters;
  const analysisDone = stats.analyzed_segments + stats.synthesized_chapters;
  const finalReview = job?.checkpoint.step === "final_review";
  const reviewed = finalReview
    ? Array.isArray(job?.checkpoint.final_review_done)
      ? job.checkpoint.final_review_done.length
      : 0
    : stats.reviewed_segments || 0;
  const reviewTotal = finalReview
    ? Number(job?.checkpoint.total || 0)
    : stats.review_total || stats.total;
  const active =
    exportState === "running" ||
    exportState === "done" ||
    exportState === "error"
      ? 4
      : finalReview ||
          job?.operation === "review" ||
          job?.operation === "resolve_validations" ||
          job?.operation === "consistency"
        ? 3
        : job?.operation === "analyze"
          ? 1
          : job?.operation === "translate"
            ? 2
            : analysisDone < analysisTotal
              ? 1
              : stats.translated < stats.total
                ? 2
                : 3;
  const stages = [
    {
      title: "Import",
      done: 1,
      total: 1,
      detail: "EPUB importé et structure chargée.",
    },
    {
      title: "Analyse & mémoire",
      done: analysisDone,
      total: analysisTotal,
      detail: `${stats.analyzed_segments}/${stats.total} passages analysés · ${stats.synthesized_chapters}/${stats.chapters} sections synthétisées.`,
    },
    {
      title: "Traduction",
      done: stats.translated,
      total: stats.total,
      detail: `${stats.translated}/${stats.total} passages traduits · ${stats.retained_source} conservés en original.`,
    },
    {
      title: "Relecture",
      done: reviewed,
      total: reviewTotal,
      detail: finalReview
        ? `${reviewed}/${reviewTotal} passages traités par la revue finale · ${stats.flagged} à vérifier.`
        : `${reviewed}/${reviewTotal} passages ayant reçu une relecture IA ou une validation humaine · ${stats.flagged} à vérifier.`,
    },
    {
      title: "Export",
      done: exportState === "done" ? 1 : 0,
      total: 1,
      detail:
        exportState === "running"
          ? "Génération et réception du fichier en cours…"
          : exportState === "done"
            ? "Fichier reçu ; téléchargement transmis au navigateur."
            : exportState === "error"
              ? "Export échoué. Consultez le message d’erreur et réessayez."
              : "Choisissez un format dans Exporter. Aucun fichier généré dans cette session.",
    },
  ];
  const index = selected ?? active;
  const stage = stages[index];
  const busy = index === 4 && exportState === "running";
  const percent = stage.total
    ? Math.min(100, Math.round((stage.done / stage.total) * 100))
    : 0;
  return (
    <section className="stage-progress" aria-label="Avancement par étape">
      <div
        className="stage-selectors"
        role="group"
        aria-label="Choisir une étape"
      >
        {stages.map((item, i) => (
          <button
            key={item.title}
            aria-pressed={index === i}
            aria-controls="stage-progress-detail"
            onClick={() => setSelected(i)}
          >
            {i + 1} · {item.title}
          </button>
        ))}
      </div>
      <div id="stage-progress-detail">
        <div className="stage-progress-heading">
          <strong>{stage.title}</strong>
          <span>{busy ? "En cours" : `${percent} %`}</span>
        </div>
        <progress
          aria-label={`Progression ${stage.title}`}
          value={busy ? undefined : percent}
          max={100}
        />
        <p>{stage.detail}</p>
        {index === 3 && (
          <small>
            Une relecture effectuée ne signifie pas que toutes les alertes sont
            résolues.
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
