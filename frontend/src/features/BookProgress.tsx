import type { Project } from "../types";
import { duration, projectProgress } from "./progress";

export function BookProgress({ project }: { project: Project }) {
  const progress = projectProgress(project);
  const stage = progress.current;
  const detail =
    stage.key === "analysis"
      ? `${project.stats.analyzed_segments}/${project.stats.total} passages · ${project.stats.synthesized_chapters}/${project.stats.chapters} sections`
      : stage.key === "translation"
        ? `${project.stats.translated}/${project.stats.total} passages traduits`
        : stage.key === "review"
          ? `${progress.review.examined}/${progress.review.total} examinés · ${progress.review.remaining} à vérifier`
          : stage.key === "export"
            ? "Livre prêt à exporter"
            : "EPUB importé";
  return (
    <div className="book-progress">
      <div className={`${stage.key}-progress`} title={detail}>
        <div className="progress-label">
          <span>{stage.label}</span>
          <strong>{stage.percent} %</strong>
        </div>
        <progress
          aria-label={`${stage.label} de ${project.title}`}
          max={100}
          value={stage.percent}
        />
      </div>
      <small>{detail}</small>
      {progress.estimate.remaining_seconds !== null && stage.percent < 100 && (
        <small>{duration(progress.estimate.remaining_seconds)}</small>
      )}
    </div>
  );
}
