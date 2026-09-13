import type { Project } from "../types";

export function BookProgress({ project }: { project: Project }) {
  const stats = project.stats;
  const analysisTotal = stats.total + stats.chapters;
  const analysisDone =
    (stats.analyzed_segments || 0) + (stats.synthesized_chapters || 0);
  const analysis = analysisTotal
    ? Math.min(100, Math.floor((analysisDone / analysisTotal) * 100))
    : 0;
  const translation = stats.total
    ? Math.min(100, Math.floor((stats.translated / stats.total) * 100))
    : 0;
  const review = stats.total
    ? Math.min(
        100,
        Math.floor(((stats.reviewed_segments || 0) / stats.total) * 100),
      )
    : 0;
  const analysisStage = {
    key: "analysis",
    label: "Analyse",
    value: analysis,
    detail: `${stats.analyzed_segments || 0}/${stats.total} passages · ${stats.synthesized_chapters || 0}/${stats.chapters} sections`,
  };
  let stage: { key: string; label: string; value: number; detail: string };
  if (project.status === "completed") {
    stage = {
      key: "export",
      label: "Export",
      value: 100,
      detail: "Livre prêt à exporter",
    };
  } else if (project.status === "analyzing") {
    stage = analysisStage;
  } else if (project.status === "reviewing" || translation === 100) {
    stage = {
      key: "review",
      label: "Relecture",
      value: review,
      detail: `${stats.reviewed_segments || 0}/${stats.total} passages relus`,
    };
  } else if (project.status === "translating" || analysis === 100) {
    stage = {
      key: "translation",
      label: "Traduction",
      value: translation,
      detail: `${stats.translated}/${stats.total} passages traduits`,
    };
  } else {
    stage = analysisStage;
  }
  return (
    <div className="book-progress">
      <div className={`${stage.key}-progress`} title={stage.detail}>
        <div className="progress-label">
          <span>{stage.label}</span>
          <strong>{stage.value} %</strong>
        </div>
        <progress
          aria-label={`${stage.label} de ${project.title}`}
          max={100}
          value={stage.value}
        />
      </div>
      <small>{stage.detail}</small>
    </div>
  );
}
