import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { api, date, labels, send } from "../api";
import { registerTranslations, useI18n } from "../i18n";
import type { Chapter, Job, Project, Run, Segment, User } from "../types";
import { Editor } from "./Editor";
import { ValidationPanel } from "./ValidationPanel";
import { CompletionPanel } from "./CompletionPanel";
import { StageProgress, type ExportState } from "./StageProgress";
import { duration, projectProgress } from "./progress";
const CharacterGraph = lazy(() => import("./CharacterGraph"));
const translations: Record<string, string> = {
  "Analyse des passages": "Segment analysis",
  "Synthèse de la Book Bible": "Book Bible synthesis",
  "Traduction": "Translation",
  "Cohérence globale": "Global consistency",
  "Résolution finale des validations": "Final validation resolution",
  "Récupération requise": "Recovery required",
  "Connexion au suivi…": "Connecting to progress tracking…",
  "Suivi connecté": "Progress tracking connected",
  "Reconnexion du suivi…": "Reconnecting progress tracking…",
  "Ouverture du livre…": "Opening book…",
  "Export refusé": "Export refused",
  "Actualiser les données du livre": "Refresh book data",
  "Actualisation…": "Refreshing…",
  "Actualiser": "Refresh",
  "Mis à jour à": "Updated at",
  "Bibliothèque /": "Library /",
  "volume": "volume",
  "lot": "batch",
  "Reprendre le travail annulé": "Resume cancelled work",
  "Reprendre après connexion": "Resume after signing in",
  "Reprendre": "Resume",
  "Pause": "Pause",
  "Réessayer maintenant": "Retry now",
  "Annuler": "Cancel",
  "Analyse terminée": "Analysis complete",
  "Analyser le livre": "Analyze book",
  "Relancer une analyse complète des résultats automatiques ? Les analyses et décisions humaines sont conservées. Cette opération rappellera le modèle.": "Rerun a full analysis of the automatic results? Analyses and human decisions will be preserved. This operation will call the model again.",
  "Réanalyse complète": "Full reanalysis",
  "Traduire": "Translate",
  "Récupérer": "Recover",
  "passage(s)": "segment(s)",
  "Exporter ↓": "Export ↓",
  "EPUB traduit": "Translated EPUB",
  "Texte": "Text",
  "Projet complet": "Complete project",
  "EPUB partiel · originaux conservés": "Partial EPUB · originals retained",
  "Rapport de couverture": "Coverage report",
  "Configurez le provider et les langues dans": "Configure the provider and languages in",
  "Configuration": "Settings",
  "Gérer les providers →": "Manage providers →",
  "Voir les requêtes": "View requests",
  "Le provider a refusé le traitement. Le texte source est conservé ; ce passage n’est pas compté comme analysé ou traduit automatiquement.": "The provider declined processing. The source text is retained; this segment is not counted as automatically analyzed or translated.",
  "Ouvrir le passage à traiter": "Open the segment to process",
  "Compléter la Book Bible manuellement": "Complete the Book Bible manually",
  "Reprise automatique prévue :": "Automatic retry scheduled:",
  "interruption(s) consécutive(s). Les étapes enregistrées sont conservées. Utilisez Pause pour suspendre les tentatives automatiques.": "consecutive interruption(s). Completed stages are retained. Use Pause to suspend automatic retries.",
  "Pause volontaire — utilisez Reprendre pour continuer.": "Paused manually — use Resume to continue.",
  "Ouvrir les paramètres de connexion du provider →": "Open provider connection settings →",
  "validés humainement": "human-validated",
  "à vérifier": "to review",
  "erreurs": "errors",
  "Mémoire": "Memory",
  "passage(s) conservé(s) en original": "segment(s) retained in the original",
  "Navigation du livre": "Book navigation",
  "Validations": "Validations",
  "Bilan & récupération": "Summary & recovery",
  "Qualité": "Quality",
  "Mémoire du livre": "Book memory",
  "Personnages & liens": "Characters & relationships",
  "Glossaire": "Glossary",
  "Réglages & suivi": "Settings & tracking",
  "Observabilité": "Observability",
  "Sections du livre": "Book sections",
  "Analysé": "Analyzed",
  "Chargement du graphe…": "Loading graph…",
};

registerTranslations(translations);

const stageLabels: Record<string, string> = {
  chapter_analysis: "Analyse des passages",
  book_bible: "Synthèse de la Book Bible",
  translation: "Traduction",
  consistency: "Cohérence globale",
  final_review: "Résolution finale des validations",
  recovery_required: "Récupération requise",
};
import {
  Bible,
  Glossary,
  Observability,
  ProjectSettings,
  Quality,
} from "./panels";

export function Workspace({
  id,
  user,
  run,
}: {
  id: string;
  user: User;
  run: Run;
}) {
  const { t } = useI18n();
  const [project, setProject] = useState<Project | null>(null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [chapter, setChapter] = useState("");
  const [focusRefusal, setFocusRefusal] = useState("");
  const [tab, setTab] = useState("editor");
  const [exportState, setExportState] = useState<ExportState>("idle");
  const [tick, setTick] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshedAt, setRefreshedAt] = useState("");
  const [streamState, setStreamState] = useState(t("Connexion au suivi…"));
  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const [p, c, j] = await Promise.all([
        api<Project>(`/projects/${id}`),
        api<Chapter[]>(`/projects/${id}/chapters`),
        api<Job[]>(`/projects/${id}/jobs`),
      ]);
      setProject(p);
      setChapters(c);
      setJobs(j);
      setChapter((previous) => previous || c[0]?.id || "");
      setRefreshedAt(new Date().toLocaleTimeString("fr-FR"));
    } finally {
      setRefreshing(false);
    }
  }, [id]);
  useEffect(() => {
    void run(load);
  }, [run, load, tick]);
  useEffect(() => {
    const stream = new EventSource(`/api/projects/${id}/events`);
    let pending: ReturnType<typeof setTimeout> | undefined;
    stream.onopen = () => setStreamState(t("Suivi connecté"));
    stream.onerror = () => setStreamState(t("Reconnexion du suivi…"));
    stream.onmessage = () => {
      clearTimeout(pending);
      pending = setTimeout(() => setTick((v) => v + 1), 300);
    };
    return () => {
      clearTimeout(pending);
      stream.close();
    };
  }, [id, t]);
  const refresh = () => setTick((t) => t + 1);
  if (!project) return <main className="loading">{t("Ouverture du livre…")}</main>;
  const runningJob = jobs.find((j) =>
    [
      "pending",
      "waiting",
      "blocked",
      "analyzing",
      "translating",
      "reviewing",
      "syncing",
      "paused",
    ].includes(j.status),
  );
  const job =
    runningJob ||
    (["failed", "cancelled"].includes(project.status)
      ? jobs.find((j) => j.status === project.status)
      : undefined);
  const sourceDone = project.stats.analyzed_segments || 0;
  const bibleDone = project.stats.synthesized_chapters || 0;
  const analysisReady =
    sourceDone === project.stats.total && bibleDone === project.stats.chapters;
  async function start(operation: string, force = false) {
    await send(`/projects/${id}/jobs`, { operation, force });
    refresh();
  }
  const canonicalProgress = projectProgress(project);
  async function exportFile(format: string, allowSource = false) {
    if (exportState === "running") return;
    setExportState("running");
    try {
      const response = await fetch(
        `/api/projects/${id}/export/${format}${allowSource ? "?allow_source=true" : ""}`,
      );
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(
          typeof error.detail === "string"
            ? error.detail
            : `${t("Export refusé")} (HTTP ${response.status}).`,
        );
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = `${project!.title}.${format === "project" ? "zip" : format === "bible" ? "json" : format}`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      setExportState("done");
    } catch (error) {
      setExportState("error");
      throw error;
    }
  }
  return (
    <main className="workspace">
      <div className="workspace-heading">
        <div className="refresh-control">
          <button
            aria-label={t("Actualiser les données du livre")}
            aria-busy={refreshing}
            disabled={refreshing}
            onClick={refresh}
          >
            {refreshing ? t("Actualisation…") : t("Actualiser")}
          </button>
          <small>{refreshedAt && `${t("Mis à jour à")} ${refreshedAt}`}</small>
        </div>
        <div>
          <a className="breadcrumb" href="#library">
            {t("Bibliothèque /")}
          </a>
          <h1>{project.title}</h1>
          <span className="muted">
            {project.author} · {project.source_language} →{" "}
            {project.target_language}
          </span>
          {project.series_name && (
            <span className="series-meta">
              {project.series_name}
              {project.volume_number && ` · ${t("volume")} ${project.volume_number}`}
            </span>
          )}
        </div>
        <div className="workspace-progress">
          <strong>{canonicalProgress.current.percent}%</strong>
          <span>{t(canonicalProgress.current.label)}</span>
          {canonicalProgress.estimate.remaining_seconds !== null &&
            canonicalProgress.current.percent < 100 && (
              <small>
                {duration(canonicalProgress.estimate.remaining_seconds)}
              </small>
            )}
        </div>
      </div>
      <div className="pipeline-bar">
        <StageProgress project={project} job={job} exportState={exportState} />
        <div className="actions">
          {job ? (
            <>
              <span className={`badge ${job.status}`}>
                {labels[job.status]}
              </span>
              {["paused", "failed", "blocked", "cancelled"].includes(
                job.status,
              ) ? (
                <button
                  className="primary"
                  onClick={() =>
                    void run(async () => {
                      await send(`/projects/${id}/jobs/${job.id}/resume`);
                      refresh();
                    })
                  }
                >
                  {job.status === "cancelled"
                     ? t("Reprendre le travail annulé")
                    : job.status === "blocked" &&
                        job.stop_reason === "authentication_required"
                       ? t("Reprendre après connexion")
                       : t("Reprendre")}
                </button>
              ) : (
                <button
                  onClick={() =>
                    void run(async () => {
                      await send(`/projects/${id}/jobs/${job.id}/pause`);
                      refresh();
                    })
                  }
                >
                  {t("Pause")}
                </button>
              )}
              {job.status === "waiting" && (
                <button
                  onClick={() =>
                    void run(async () => {
                      await send(`/projects/${id}/jobs/${job.id}/retry`);
                      refresh();
                    })
                  }
                >
                  {t("Réessayer maintenant")}
                </button>
              )}
              {job.status !== "cancelled" && (
                <button
                  onClick={() =>
                    void run(async () => {
                      await send(`/projects/${id}/jobs/${job.id}/cancel`);
                      refresh();
                    })
                  }
                >
                  {t("Annuler")}
                </button>
              )}
            </>
          ) : (
            <>
              <button
                disabled={!project.provider_id || analysisReady}
                onClick={() => void run(() => start("analyze"))}
              >
                {analysisReady ? t("Analyse terminée") : t("Analyser le livre")}
              </button>
              {analysisReady && (
                <button
                  onClick={() => {
                    if (
                      confirm(
                        t("Relancer une analyse complète des résultats automatiques ? Les analyses et décisions humaines sont conservées. Cette opération rappellera le modèle."),
                      )
                    )
                      void run(() => start("analyze", true));
                  }}
                >
                  {t("Réanalyse complète")}
                </button>
              )}
              <button
                className="primary"
                disabled={
                  !project.provider_id || !Object.keys(project.bible).length
                }
                onClick={() => void run(() => start("translate"))}
              >
                  {t("Traduire")}
              </button>
              {!!(project.stats.errors || project.stats.refused) && (
                <button
                  className="primary"
                  onClick={() => setTab("completion")}
                >
                  {t("Récupérer")} {project.stats.errors + project.stats.refused}{" "}
                  {t("passage(s)").replace("segment(s)", project.stats.errors + project.stats.refused === 1 ? "segment" : "segments")}
                </button>
              )}
            </>
          )}
          <details className="export-menu">
            <summary className="button">{t("Exporter ↓")}</summary>
            <div>
              {[
                ["epub", t("EPUB traduit")],
                ["txt", t("Texte")],
                ["md", "Markdown"],
                ["bible", "Book Bible JSON"],
                ["project", t("Projet complet")],
              ].map(([format, label]) => (
                <a
                  key={format}
                  href={`/api/projects/${id}/export/${format}`}
                  onClick={(e) => {
                    e.preventDefault();
                    void run(() => exportFile(format));
                  }}
                >
                  {label}
                </a>
              ))}
              <a
                href={`/api/projects/${id}/export/epub?allow_source=true`}
                onClick={(e) => {
                  e.preventDefault();
                  void run(() => exportFile("epub", true));
                }}
              >
                {t("EPUB partiel · originaux conservés")}
              </a>
              <a
                href={`/api/projects/${id}/coverage`}
                target="_blank"
                rel="noreferrer"
              >
                {t("Rapport de couverture")}
              </a>
            </div>
          </details>
        </div>
      </div>
      {!project.provider_id && (
        <div className="notice">
          {t("Configurez le provider et les langues dans")} {" "}
          <button className="link" onClick={() => setTab("config")}>
            {t("Configuration")}
          </button>
          . {user.admin && <a href="#settings">{t("Gérer les providers →")}</a>}
        </div>
      )}
      {job?.error && (
        <div
          className={job.status === "waiting" ? "notice" : "error-banner"}
          role="alert"
        >
          {job.error}{" "}
          <button onClick={() => setTab("requests")}>{t("Voir les requêtes")}</button>
        </div>
      )}
      {job?.stop_reason === "content_refusal" && (
        <div className="notice" role="status">
          {t("Le provider a refusé le traitement. Le texte source est conservé ; ce passage n’est pas compté comme analysé ou traduit automatiquement.")}
          {job.checkpoint.segment_id ? (
            <button
              onClick={() =>
                void run(async () => {
                  const segment = await api<Segment>(
                    `/segments/${job.checkpoint.segment_id}`,
                  );
                  setChapter(segment.chapter_id);
                  setFocusRefusal(segment.id);
                  setTab("editor");
                })
              }
            >
              {t("Ouvrir le passage à traiter")}
            </button>
          ) : (
            <button onClick={() => setTab("bible")}>
              {t("Compléter la Book Bible manuellement")}
            </button>
          )}
        </div>
      )}
      {job?.status === "waiting" && (
        <div className="notice" role="status">
          {t("Reprise automatique prévue :")} {date(job.next_attempt)} ·{" "}
          {job.outage_count} {t("interruption(s) consécutive(s). Les étapes enregistrées sont conservées. Utilisez Pause pour suspendre les tentatives automatiques.").replace("interruption(s)", job.outage_count === 1 ? "interruption" : "interruptions")}
        </div>
      )}
      {job?.status === "paused" && (
        <p className="muted">
          {t("Pause volontaire — utilisez Reprendre pour continuer.")}
        </p>
      )}
      {job?.status === "blocked" &&
        job.stop_reason === "authentication_required" &&
        user.admin && (
          <p>
            <a href="#settings">
              {t("Ouvrir les paramètres de connexion du provider →")}
            </a>
          </p>
        )}
      <div className="workspace-status">
        <span>
          {streamState}
          {job?.checkpoint.step
            ? ` · ${t(stageLabels[String(job.checkpoint.step)] || String(job.checkpoint.step))}`
            : ""}
          {job?.checkpoint.current
            ? ` · ${String(job.checkpoint.current)} / ${String(job.checkpoint.total)}`
            : ""}
          {job?.checkpoint.step === "book_bible" && job.checkpoint.batch_current
            ? ` · ${t("lot")} ${String(job.checkpoint.batch_current)}/${String(job.checkpoint.batch_total)}`
            : ""}
        </span>
        <span>
          {project.stats.validated} {t("validés humainement")} · {project.stats.flagged} {t("à vérifier")} · {project.stats.errors} {t("erreurs")} · {t("Mémoire")} {project.context_backend}
          {project.stats.retained_source > 0
            ? ` · ${project.stats.retained_source} ${t("passage(s) conservé(s) en original").replace("segment(s)", project.stats.retained_source === 1 ? "segment" : "segments")}`
            : ""}
        </span>
      </div>
      <div className="workspace-shell">
        <nav className="workspace-nav" aria-label={t("Navigation du livre")}>
          {[
            {
              title: t("Traduire"),
              items: [
                ["editor", t("Traduction")],
                [
                  "validations",
                  `${t("Validations")}${project.stats.flagged + project.stats.refused ? ` (${project.stats.flagged + project.stats.refused})` : ""}`,
                ],
                ["completion", t("Bilan & récupération")],
                ["quality", t("Qualité")],
              ],
            },
            {
              title: t("Mémoire du livre"),
              items: [
                ["bible", "Book Bible"],
                ["characters", t("Personnages & liens")],
                ["glossary", t("Glossaire")],
              ],
            },
            {
              title: t("Réglages & suivi"),
              items: [
                ["config", t("Configuration")],
                ["requests", t("Observabilité")],
              ],
            },
          ].map((group) => (
            <div className="workspace-nav-group" key={group.title}>
              <h2>{group.title}</h2>
              {group.items.map(([key, label]) => (
                <button
                  key={key}
                  aria-current={tab === key ? "page" : undefined}
                  aria-controls="workspace-content"
                  onClick={() => setTab(key)}
                >
                  {label}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div id="workspace-content" className="workspace-content">
          {tab === "editor" ? (
            <div className="workspace-body">
              <aside className="chapter-list">
                <h3>
                  {t("Sections du livre")} <span>{chapters.length}</span>
                </h3>
                {chapters.map((c) => (
                  <button
                    key={c.id}
                    className={chapter === c.id ? "active" : ""}
                    onClick={() => setChapter(c.id)}
                  >
                    <small>{String(c.position + 1).padStart(2, "0")}</small>
                    <span>{c.title}</span>
                    {c.analyzed && <span className="dot" title={t("Analysé")} />}
                  </button>
                ))}
              </aside>
              {chapter && (
                <Editor
                  project={project}
                  chapter={chapters.find((c) => c.id === chapter)!}
                  tick={tick}
                  run={run}
                  refresh={refresh}
                  focusRefusal={focusRefusal}
                />
              )}
            </div>
          ) : (
            <div className="workspace-panel">
              {tab === "characters" ? (
                <Suspense fallback={<p>{t("Chargement du graphe…")}</p>}>
                  <CharacterGraph pid={id} tick={tick} run={run} />
                </Suspense>
              ) : tab === "bible" ? (
                <Bible
                  project={project}
                  run={run}
                  refresh={refresh}
                  tick={tick}
                />
              ) : tab === "glossary" ? (
                <Glossary project={project} run={run} tick={tick} />
              ) : tab === "quality" ? (
                <Quality project={project} run={run} tick={tick} />
              ) : tab === "completion" ? (
                <CompletionPanel
                  project={project}
                  run={run}
                  refresh={refresh}
                />
              ) : tab === "validations" ? (
                <ValidationPanel
                  project={project}
                  chapters={chapters}
                  run={run}
                  refresh={refresh}
                />
              ) : tab === "requests" ? (
                <Observability project={project} run={run} tick={tick} />
              ) : (
                <ProjectSettings
                  project={project}
                  run={run}
                  refresh={refresh}
                />
              )}
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
