import type { Project } from "../types";
import { formatPercent, registerTranslations, useI18n } from "../i18n";
import { ProgressBar } from "../ui";
import { duration, projectProgress } from "./progress";

registerTranslations({
  "{done}/{total} passages · {sections}/{chapters} sections": "{done}/{total} passages · {sections}/{chapters} sections",
  "{done}/{total} passages traduits": "{done}/{total} passages translated",
  "{done}/{total} propositions IA": "{done}/{total} AI proposals",
  "{done}/{total} examinés · {remaining} ouverts": "{done}/{total} reviewed · {remaining} open",
  "Livre prêt à exporter": "Book ready to export",
  "EPUB importé": "EPUB imported",
  "Chapitres importés": "Chapters imported",
  "{stage} de {title}": "{stage} for {title}",
  "Reprise prévue à {time}": "Retry scheduled at {time}",
  "En attente d’une place chez le provider": "Waiting for provider capacity",
});

export function BookProgress({ project, compact = false }: { project: Project; compact?: boolean }) {
  const { t, locale } = useI18n();
  const progress = projectProgress(project);
  const stage = progress.current;
  const stats = project.stats;
  const detail =
    stage.key === "analysis"
      ? t("{done}/{total} passages · {sections}/{chapters} sections", {
          done: stats.analyzed_segments,
          total: stats.total,
          sections: stats.synthesized_chapters,
          chapters: stats.chapters,
        })
      : stage.key === "translation"
        ? t("{done}/{total} passages traduits", { done: stats.translated, total: stats.total })
        : stage.key === "review"
          ? progress.operation === "accept_critiques" && progress.state !== "completed"
            ? t("{done}/{total} propositions IA", { done: stage.done, total: stage.total })
            : t("{done}/{total} examinés · {remaining} ouverts", {
                done: stage.done,
                total: stage.total,
                remaining: progress.review.remaining,
              })
          : stage.key === "export"
            ? t("Livre prêt à exporter")
            : project.source_format && project.source_format !== "epub"
              ? t("Chapitres importés")
              : t("EPUB importé");
  const complete = stage.key === "export" && stage.percent === 100;
  return (
    <div className={compact ? "book-progress book-progress-compact" : "book-progress"} title={detail}>
      <div className="book-progress-label">
        <span>{t(stage.label)}</span>
        <strong className="tabular">{formatPercent(stage.percent)}</strong>
      </div>
      <ProgressBar
        size="sm"
        tone={complete ? "success" : progress.state === "waiting" || progress.state === "paused" ? "warning" : "accent"}
        label={t("{stage} de {title}", { stage: t(stage.label), title: project.title })}
        value={stage.percent}
      />
      <small className="book-progress-detail">{detail}</small>
      {progress.state === "waiting" && !!progress.next_attempt && (
        <small className="book-progress-detail">
          {progress.next_attempt * 1000 > Date.now()
            ? t("Reprise prévue à {time}", {
                time: new Date(progress.next_attempt * 1000).toLocaleTimeString(locale, {
                  hour: "2-digit",
                  minute: "2-digit",
                }),
              })
            : t("En attente d’une place chez le provider")}
        </small>
      )}
      {!compact && progress.estimate.remaining_seconds !== null && stage.percent < 100 && (
        <small className="book-progress-detail">{duration(progress.estimate.remaining_seconds)}</small>
      )}
    </div>
  );
}
