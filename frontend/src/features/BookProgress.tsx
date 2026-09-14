import type { Project } from "../types";
import { registerTranslations, useI18n } from "../i18n";
import { duration, projectProgress } from "./progress";

const translations: Record<string, string> = {
  "Import": "Import",
  "Analyse & mémoire": "Analysis & memory",
  "Traduction": "Translation",
  "Relecture": "Review",
  "Export": "Export",
  "passages ·": "segments ·",
  "sections": "sections",
  "passages traduits": "segments translated",
  "examinés": "reviewed",
  "propositions IA": "AI proposals",
  "à vérifier": "to review",
  "Livre prêt à exporter": "Book ready to export",
  "EPUB importé": "EPUB imported",
  "de": "for",
  "Reprise prévue à": "Retry scheduled at",
  "En attente d’une place chez le provider": "Waiting for provider capacity",
};

registerTranslations(translations);

export function BookProgress({ project }: { project: Project }) {
  const { t, locale } = useI18n();
  const progress = projectProgress(project);
  const stage = progress.current;
  const detail =
    stage.key === "analysis"
      ? `${project.stats.analyzed_segments}/${project.stats.total} ${t("passages ·")} ${project.stats.synthesized_chapters}/${project.stats.chapters} ${t("sections")}`
      : stage.key === "translation"
        ? `${project.stats.translated}/${project.stats.total} ${t("passages traduits")}`
        : stage.key === "review"
          ? progress.operation === "accept_critiques" && progress.state !== "completed"
            ? `${stage.done}/${stage.total} ${t("propositions IA")}`
            : `${stage.done}/${stage.total} ${t("examinés")} · ${progress.review.remaining} ${t("à vérifier")}`
          : stage.key === "export"
            ? t("Livre prêt à exporter")
            : t("EPUB importé");
  return (
    <div className="book-progress">
      <div className={`${stage.key}-progress`} title={detail}>
        <div className="progress-label">
          <span>{t(stage.label)}</span>
          <strong>{stage.percent} %</strong>
        </div>
        <progress
          aria-label={`${t(stage.label)} ${t("de")} ${project.title}`}
          max={100}
          value={stage.percent}
        />
      </div>
      <small>{detail}</small>
      {progress.state === "waiting" && !!progress.next_attempt && <small>
        {progress.next_attempt * 1000 > Date.now()
          ? `${t("Reprise prévue à")} ${new Date(progress.next_attempt * 1000).toLocaleTimeString(locale)}`
          : t("En attente d’une place chez le provider")}
      </small>}
      {progress.estimate.remaining_seconds !== null && stage.percent < 100 && (
        <small>{duration(progress.estimate.remaining_seconds)}</small>
      )}
    </div>
  );
}
