import type { Project } from "../types";

export function BookProgress({ project }: { project: Project }) {
  const stats = project.stats;
  const work = stats.total + stats.chapters;
  const done =
    (stats.analyzed_segments || 0) + (stats.synthesized_chapters || 0);
  const analysis = work ? Math.min(100, Math.floor((done / work) * 100)) : 0;
  const translation = stats.total
    ? Math.min(100, Math.floor((stats.translated / stats.total) * 100))
    : 0;
  return (
    <div className="book-progress">
      <div
        className="analysis-progress"
        title={`${stats.analyzed_segments || 0}/${stats.total} passages analysés · ${stats.synthesized_chapters || 0}/${stats.chapters} sections synthétisées`}
      >
        <div className="progress-label">
          <span>Analyse</span>
          <strong>{analysis} %</strong>
        </div>
        <progress
          aria-label={`Analyse de ${project.title}`}
          max={100}
          value={analysis}
        />
      </div>
      <div
        className="translation-progress"
        title={`${stats.translated}/${stats.total} passages traduits${stats.retained_source ? ` · ${stats.retained_source} originaux conservés` : ""}`}
      >
        <div className="progress-label">
          <span>Traduction</span>
          <strong>{translation} %</strong>
        </div>
        <progress
          aria-label={`Traduction de ${project.title}`}
          max={100}
          value={translation}
        />
      </div>
      <small>
        {stats.total} passages · {stats.chapters} sections
      </small>
    </div>
  );
}
