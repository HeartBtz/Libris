import type { Project, ProjectProgress, ProgressStage } from "../types";
import { formatNumber, message, registerTranslations } from "../i18n";

const translations: Record<string, string> = {
  "Import": "Import",
  "Analyse & mémoire": "Analysis & memory",
  "Traduction": "Translation",
  "Relecture": "Review",
  "Export": "Export",
  "Estimation en attente": "Estimate pending",
  "~{count} s restantes": "~{count} s remaining",
  "~{count} min restantes": "~{count} min remaining",
  "~{count} h restantes": "~{count} h remaining",
};

registerTranslations(translations);

export function projectProgress(project: Project): ProjectProgress {
  if (project.progress) return project.progress;
  const stats = project.stats;
  const analysisDone = stats.analyzed_segments + stats.synthesized_chapters;
  const analysisTotal = stats.total + stats.chapters;
  const translationStarted = Boolean(
    stats.translated || stats.errors || stats.refused,
  );
  const reviewTotal = stats.review_total || stats.total;
  const values: Array<[ProgressStage["key"], string, number, number]> = [
    ["import", "Import", 1, 1],
    ["analysis", "Analyse & mémoire", analysisDone, analysisTotal],
    ["translation", "Traduction", stats.translated, stats.total],
    ["review", "Relecture", stats.reviewed_segments || 0, reviewTotal],
    [
      "export",
      "Export",
      project.status === "completed" && !stats.flagged ? 1 : 0,
      1,
    ],
  ];
  const stages = values.map(([key, label, done, total]) => ({
    key,
    label,
    done,
    total,
    percent: total ? Math.min(100, Math.round((done / total) * 100)) : 0,
  }));
  const active_stage =
    project.status === "analyzing" && !translationStarted
      ? "analysis"
      : (project.status === "reviewing" || stats.translated === stats.total) &&
          !stats.errors &&
          !stats.refused
        ? stats.flagged
          ? "review"
          : "export"
        : translationStarted
          ? "translation"
          : analysisDone < analysisTotal
            ? "analysis"
            : "translation";
  return {
    active_stage,
    state: project.status,
    operation: null,
    job_id: null,
    model: null,
    current: stages.find((stage) => stage.key === active_stage)!,
    stages,
    review: {
      examined: stats.reviewed_segments || 0,
      total: reviewTotal,
      resolved: 0,
      needs_human: 0,
      remaining: stats.flagged,
      protected: stats.validated,
      revised: 0,
      failed: 0,
    },
    estimate: {
      remaining_seconds: null,
      remaining_cost: null,
      spent_cost: 0,
      confidence: "insufficient",
    },
  };
}

export function duration(seconds: number | null): string {
  if (seconds === null) return message("Estimation en attente");
  if (seconds < 60) return message("~{count} s restantes", { count: Math.max(1, Math.round(seconds)) });
  if (seconds < 3600) return message("~{count} min restantes", { count: Math.round(seconds / 60) });
  return message("~{count} h restantes", {
    count: formatNumber(seconds / 3600, { maximumFractionDigits: seconds < 36000 ? 1 : 0 }),
  });
}
