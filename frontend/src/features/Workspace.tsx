import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { api, date, downloadGet, send } from "../api";
import { formatPercent, getLocale, registerTranslations, useI18n } from "../i18n";
import type { AutopilotView, Chapter, Job, Project, Run, Segment, User } from "../types";
import { useLeaveGuard } from "../unsaved";
import {
  Badge,
  Button,
  Callout,
  Icon,
  IconButton,
  LoadingBlock,
  Menu,
  Page,
  StatusPill,
  TabPanel,
  Tabs,
  cx,
  useDialogs,
} from "../ui";
import type { MenuEntry } from "../ui";
import { Editor } from "./Editor";
import { EstimateNote } from "./Estimate";
import type { EstimatedOperation } from "./Estimate";
import { ValidationPanel } from "./ValidationPanel";
import { CompletionPanel } from "./CompletionPanel";
import { StageProgress } from "./StageProgress";
import type { ExportState } from "./StageProgress";
import { duration, projectProgress } from "./progress";
import { Bible, Glossary, Observability, ProjectSettings, Quality } from "./panels";
import { ExportMenu, exportFormats, exportName, exportPath } from "./ExportMenu";
import type { ExportFormat, ExportOptions } from "./ExportMenu";
import { AutopilotPanel, AutopilotStatus, autopilotOutcome, fetchAutopilot, phaseLabel } from "./Autopilot";
import { BookBudget } from "./Budget";
import { QueueHint } from "./Queue";

const CharacterGraph = lazy(() => import("./CharacterGraph"));

registerTranslations({
  "Analyse des passages": "Passage analysis",
  "Synthèse de la Book Bible": "Book Bible synthesis",
  Traduction: "Translation",
  "Seconde passe ciblée": "Targeted second pass",
  "Cohérence globale": "Global consistency",
  "Résolution finale des validations": "Final validation resolution",
  "Récupération requise": "Recovery required",
  "Application des critiques acceptées": "Applying accepted critiques",
  "Analyse déjà terminée": "Analysis already complete",
  "Lecture de l’EPUB": "Reading the EPUB",
  "Connexion au suivi…": "Connecting to progress tracking…",
  "Suivi connecté": "Progress tracking connected",
  "Reconnexion du suivi…": "Reconnecting progress tracking…",
  "Ouverture du livre…": "Opening book…",
  "Actualiser les données du livre": "Refresh book data",
  "Mis à jour à {time}": "Updated at {time}",
  Bibliothèque: "Library",
  "volume {volume}": "volume {volume}",
  "flux continu": "continuous flow",
  "lot {current}/{total}": "batch {current}/{total}",
  "Reprendre le travail annulé": "Resume cancelled work",
  "Reprendre après connexion": "Resume after signing in",
  Reprendre: "Resume",
  Pause: "Pause",
  "Réessayer maintenant": "Retry now",
  "Annuler le travail": "Cancel job",
  "Analyser le livre": "Analyze book",
  "Analyser le livre ?": "Analyze the book?",
  "L’analyse lit chaque passage, construit la Book Bible et la mémoire du livre. Elle appelle le modèle configuré.":
    "Analysis reads every passage and builds the Book Bible and the book memory. It calls the configured model.",
  "Relancer une analyse complète ?": "Rerun a full analysis?",
  "Les résultats automatiques seront recalculés ; les analyses et décisions humaines sont conservées. Cette opération rappellera le modèle.":
    "Automatic results will be recomputed; human analyses and decisions are kept. This operation will call the model again.",
  "Réanalyse complète": "Full reanalysis",
  Traduire: "Translate",
  "Traduire le livre ?": "Translate the book?",
  "Les passages non traduits seront envoyés au modèle configuré, dans l’ordre du livre. Les corrections humaines sont protégées.":
    "Untranslated passages will be sent to the configured model, in book order. Human corrections are protected.",
  Lancer: "Start",
  "Récupérer {count} passage": "Recover {count} passage",
  "Récupérer {count} passages": "Recover {count} passages",
  "Lancer le pilote automatique": "Start the autopilot",
  "Lancer le pilote automatique ?": "Start the autopilot?",
  "Libris analyse, traduit, relit et arbitre seul chaque passage jusqu’au résultat, dans les limites de l’installation. Les corrections humaines sont protégées ; aucune validation ne vous sera demandée.":
    "Libris analyzes, translates, reviews and arbitrates every passage on its own until the result, within the installation's limits. Human corrections are protected; no validation will be asked of you.",
  "Télécharger l’EPUB": "Download the EPUB",
  "Télécharger les chapitres": "Download the chapters",
  "Pilote automatique": "Autopilot",
  "Journal des relectures": "Review log",
  "Exporter l’EPUB": "Export EPUB",
  "Exporter les chapitres": "Export the chapters",
  "Configurer le livre": "Configure the book",
  "Autres actions": "More actions",
  "Aucun provider n’est configuré pour ce livre.": "No provider is configured for this book.",
  "Choisissez le modèle et les langues dans les réglages du livre.": "Choose the model and languages in the book settings.",
  "Ouvrir les réglages": "Open settings",
  "Gérer les providers": "Manage providers",
  "Voir les requêtes": "View requests",
  "Le provider a refusé le traitement. Le texte source est conservé ; ce passage n’est pas compté comme analysé ou traduit automatiquement.":
    "The provider declined processing. The source text is retained; this passage is not counted as automatically analyzed or translated.",
  "Ouvrir le passage à traiter": "Open the passage to process",
  "Compléter la Book Bible manuellement": "Complete the Book Bible manually",
  "Reprise automatique prévue le {date}.": "Automatic retry scheduled on {date}.",
  "{count} interruption consécutive. Les étapes enregistrées sont conservées ; Pause suspend les tentatives automatiques.":
    "{count} consecutive interruption. Completed stages are kept; Pause suspends automatic retries.",
  "{count} interruptions consécutives. Les étapes enregistrées sont conservées ; Pause suspend les tentatives automatiques.":
    "{count} consecutive interruptions. Completed stages are kept; Pause suspends automatic retries.",
  "Pause volontaire — utilisez Reprendre pour continuer.": "Paused manually — use Resume to continue.",
  "Ouvrir les paramètres de connexion du provider": "Open provider connection settings",
  "Navigation du livre": "Book navigation",
  Validations: "Validations",
  "Bilan & récupération": "Summary & recovery",
  Qualité: "Quality",
  Personnages: "Characters",
  Glossaire: "Glossary",
  Réglages: "Settings",
  Observabilité: "Observability",
  "Chargement du graphe…": "Loading graph…",
  "{validated} validés · {flagged} ouverts à une relecture facultative · {errors} erreurs · mémoire {memory}":
    "{validated} validated · {flagged} open to optional review · {errors} errors · {memory} memory",
  "{count} conservé en original": "{count} retained in the original",
  "{count} conservés en original": "{count} retained in the original",
  "Progression globale": "Overall progress",
  "Budget atteint : travail en pause": "Budget reached: job paused",
  "Relever le budget": "Raise the budget",
});

const stageLabels: Record<string, string> = {
  chapter_analysis: "Analyse des passages",
  book_bible: "Synthèse de la Book Bible",
  translation: "Traduction",
  automatic_recovery: "Seconde passe ciblée",
  consistency: "Cohérence globale",
  final_review: "Résolution finale des validations",
  recovery_required: "Récupération requise",
  critique_acceptance: "Application des critiques acceptées",
  already_analyzed: "Analyse déjà terminée",
  parsing: "Lecture de l’EPUB",
};

const HELD = ["pending", "waiting", "blocked", "analyzing", "translating", "reviewing", "syncing", "paused"];

export function Workspace({
  id,
  user,
  run,
  passage,
}: {
  id: string;
  user: User;
  run: Run;
  /** A passage to open in the editor once the book is loaded (`#project/<id>/passage/<segment>`). */
  passage?: string;
}) {
  const { t, tp } = useI18n();
  const { confirm } = useDialogs();
  const confirmLeave = useLeaveGuard();
  const [project, setProject] = useState<Project | null>(null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [chapter, setChapter] = useState("");
  const [focusRefusal, setFocusRefusal] = useState("");
  const [tab, setTab] = useState("editor");
  const [exportState, setExportState] = useState<ExportState>("idle");
  useEffect(() => {
    // The export outcome is a transient notice: hand the indicator back to the live pipeline stage.
    if (exportState !== "done" && exportState !== "error") return;
    const timer = setTimeout(() => setExportState("idle"), exportState === "done" ? 4000 : 10000);
    return () => clearTimeout(timer);
  }, [exportState]);
  const [tick, setTick] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshedAt, setRefreshedAt] = useState("");
  const [streamState, setStreamState] = useState<"connecting" | "connected" | "reconnecting">("connecting");
  const [autopilot, setAutopilot] = useState<AutopilotView | null>(null);
  const [focus, setFocus] = useState<{ segment: string; filter: string } | null>(null);
  const loadSequence = useRef(0);
  const load = useCallback(async () => {
    // Loads overlap while a job emits events: only the most recent request may update the screen.
    const sequence = ++loadSequence.current;
    setRefreshing(true);
    try {
      const [p, c, j, a] = await Promise.all([
        api<Project>(`/projects/${id}`),
        api<Chapter[]>(`/projects/${id}/chapters`),
        api<Job[]>(`/projects/${id}/jobs`),
        // The provider decisions name the fallback provider a run switched to; the view also
        // carries the last report. Absent on servers before 0.6: the 0.5 screens are kept.
        fetchAutopilot(id, { stage: "provider", limit: 5 }),
      ]);
      if (sequence !== loadSequence.current) return;
      setAutopilot(a);
      setProject(p);
      setChapters(c);
      setJobs(j);
      setChapter((previous) => previous || c[0]?.id || "");
      setRefreshedAt(new Date().toLocaleTimeString(getLocale(), { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
    } finally {
      if (sequence === loadSequence.current) setRefreshing(false);
    }
  }, [id]);
  useEffect(() => {
    void run.background(load);
  }, [run, load, tick]);
  useEffect(() => {
    // One stream per open book. The server caps concurrent streams per account, so a failed
    // stream is closed and reopened with a growing delay rather than retried immediately.
    let stream: EventSource | null = null;
    let pending: ReturnType<typeof setTimeout> | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let delay = 1000;
    let stopped = false;
    const reload = () => {
      clearTimeout(pending);
      pending = setTimeout(() => setTick((v) => v + 1), 300);
    };
    const connect = () => {
      stream = new EventSource(`/api/projects/${id}/events`);
      stream.onopen = () => {
        delay = 1000;
        setStreamState("connected");
        // The stream only carries what happens from now on: catch up once on anything
        // that changed between the initial load and the connection.
        reload();
      };
      stream.onerror = () => {
        stream?.close();
        setStreamState("reconnecting");
        // The stream cannot report why it failed; a reload reveals an expired session (401).
        reload();
        if (!stopped) retry = setTimeout(connect, delay);
        delay = Math.min(delay * 2, 60000);
      };
      stream.onmessage = reload;
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(pending);
      clearTimeout(retry);
      stream?.close();
    };
  }, [id]);
  const pendingPassage = useRef(passage || "");
  useEffect(() => {
    // Opened from a link to one passage (the series quality dashboard): show it in the editor once.
    const target = pendingPassage.current;
    if (!project || !target) return;
    pendingPassage.current = "";
    void run(async () => {
      const segment = await api<Segment>(`/segments/${target}`);
      setChapter(segment.chapter_id);
      setFocus({ segment: segment.id, filter: segment.retained_source ? "source_retained" : "" });
      setTab("editor");
    });
  }, [project, run]);
  const refresh = () => setTick((value) => value + 1);
  if (!project)
    return (
      <Page>
        <LoadingBlock label={t("Ouverture du livre…")} lines={4} />
      </Page>
    );
  const stats = project.stats;
  const runningJob = jobs.find((j) => HELD.includes(j.status));
  const job =
    runningJob ||
    (["failed", "cancelled"].includes(project.status) ? jobs.find((j) => j.status === project.status) : undefined);
  const analysisReady =
    (stats.analyzed_segments || 0) === stats.total && (stats.synthesized_chapters || 0) === stats.chapters;
  const canTranslate = !!project.provider_id && !!Object.keys(project.bible || {}).length;
  const recoverable = stats.errors + stats.refused;
  const progress = projectProgress(project);
  // Without the autopilot (opted out, or a server before 0.6) the 0.5 actions are kept.
  const automatic = !!autopilot?.enabled;
  const outcome = autopilotOutcome(autopilot, job);
  const unfinished = !analysisReady || stats.translated + stats.retained_source < stats.total || recoverable > 0;
  const residuals = new Map((autopilot?.report?.residuals || []).map((item) => [item.segment_id, item.reason]));
  const openPassage = (segmentId: string) =>
    void run(async () => {
      const segment = await api<Segment>(`/segments/${segmentId}`);
      if (!(await confirmLeave())) return;
      setChapter(segment.chapter_id);
      setFocus({ segment: segment.id, filter: segment.retained_source ? "source_retained" : "" });
      setTab("editor");
    });
  const openTab = async (next: string) => {
    if (next === tab || !(await confirmLeave())) return;
    setTab(next);
    requestAnimationFrame(() => document.getElementById("workspace-panel")?.focus({ preventScroll: true }));
  };
  async function launch(operation: "analyze" | "translate", force = false) {
    const estimated: EstimatedOperation = operation;
    const accepted = await confirm(
      operation === "analyze"
        ? force
          ? {
              title: t("Relancer une analyse complète ?"),
              message: t(
                "Les résultats automatiques seront recalculés ; les analyses et décisions humaines sont conservées. Cette opération rappellera le modèle.",
              ),
              details: <EstimateNote projectId={id} operation={estimated} />,
              confirmLabel: t("Réanalyse complète"),
            }
          : {
              title: t("Analyser le livre ?"),
              message: t(
                "L’analyse lit chaque passage, construit la Book Bible et la mémoire du livre. Elle appelle le modèle configuré.",
              ),
              details: <EstimateNote projectId={id} operation={estimated} />,
              confirmLabel: t("Lancer"),
            }
        : {
            title: t("Traduire le livre ?"),
            message: t(
              "Les passages non traduits seront envoyés au modèle configuré, dans l’ordre du livre. Les corrections humaines sont protégées.",
            ),
            details: <EstimateNote projectId={id} operation={estimated} />,
            confirmLabel: t("Lancer"),
          },
    );
    if (!accepted) return;
    await run(async () => {
      await send(`/projects/${id}/jobs`, { operation, force });
      refresh();
    });
  }
  async function launchAutopilot() {
    const accepted = await confirm({
      title: t("Lancer le pilote automatique ?"),
      message: t(
        "Libris analyse, traduit, relit et arbitre seul chaque passage jusqu’au résultat, dans les limites de l’installation. Les corrections humaines sont protégées ; aucune validation ne vous sera demandée.",
      ),
      details: <EstimateNote projectId={id} operation={analysisReady ? "translate" : "analyze"} />,
      confirmLabel: t("Lancer"),
    });
    if (!accepted) return;
    await run(async () => {
      // A book not analysed yet runs the whole pipeline; the server adds the autopilot options.
      await send(
        `/projects/${id}/jobs`,
        analysisReady ? { operation: "translate" } : { operation: "analyze", continue_pipeline: true },
      );
      refresh();
    });
  }
  async function exportFile(format: ExportFormat, options: ExportOptions = {}) {
    if (exportState === "running") return;
    setExportState("running");
    try {
      await downloadGet(exportPath(id, format, options), exportName(project!.title, format, options));
      setExportState("done");
    } catch (error) {
      setExportState("error");
      throw error;
    }
  }
  const jobAction = (action: "resume" | "pause" | "retry" | "cancel") =>
    void run(async () => {
      await send(`/projects/${id}/jobs/${job!.id}/${action}`);
      refresh();
    });
  const primaryFormat = exportFormats(project)[0];
  const downloadButton = (variant: "primary" | "secondary", size?: "sm") => (
    <Button
      variant={variant}
      size={size}
      icon="download"
      loading={exportState === "running"}
      onClick={() => void run(() => exportFile(primaryFormat))}
    >
      {primaryFormat === "epub" ? t("Télécharger l’EPUB") : t("Télécharger les chapitres")}
    </Button>
  );
  const primary = job ? null : !project.provider_id ? (
    <Button variant="primary" icon="settings" onClick={() => void openTab("config")}>
      {t("Configurer le livre")}
    </Button>
  ) : automatic ? (
    unfinished && outcome !== "completed" && outcome !== "completed_with_residuals" ? (
      <Button variant="primary" icon="sparkles" onClick={() => void launchAutopilot()}>
        {t("Lancer le pilote automatique")}
      </Button>
    ) : (
      downloadButton("primary")
    )
  ) : !analysisReady ? (
    <Button variant="primary" icon="sparkles" onClick={() => void launch("analyze")}>
      {t("Analyser le livre")}
    </Button>
  ) : recoverable ? (
    <Button variant="primary" icon="refresh" onClick={() => void openTab("completion")}>
      {tp(recoverable, "Récupérer {count} passage", "Récupérer {count} passages")}
    </Button>
  ) : stats.translated < stats.total ? (
    <Button variant="primary" icon="languages" disabled={!canTranslate} onClick={() => void launch("translate")}>
      {t("Traduire")}
    </Button>
  ) : (
    // Passages open to an optional review never hold the result back.
    downloadButton("primary")
  );
  const moreItems: MenuEntry[] = [
    ...(analysisReady
      ? [{ label: t("Réanalyse complète"), icon: "sparkles" as const, disabled: !!job || !project.provider_id, onSelect: () => void launch("analyze", true) }]
      : [{ label: t("Analyser le livre"), icon: "sparkles" as const, disabled: !!job || !project.provider_id, onSelect: () => void launch("analyze") }]),
    { label: t("Traduire"), icon: "languages", disabled: !!job || !canTranslate, onSelect: () => void launch("translate") },
    { kind: "separator" },
    { label: t("Réglages"), icon: "settings", onSelect: () => void openTab("config") },
    { label: t("Observabilité"), icon: "chart", onSelect: () => void openTab("requests") },
  ];
  const tabs = [
    { id: "editor", label: t("Traduction") },
    ...(autopilot ? [{ id: "autopilot", label: t("Pilote automatique") }] : []),
    { id: "validations", label: t("Journal des relectures") },
    { id: "completion", label: t("Bilan & récupération") },
    { id: "quality", label: t("Qualité") },
    { id: "bible", label: "Book Bible", groupStart: true },
    { id: "characters", label: t("Personnages") },
    { id: "glossary", label: t("Glossaire") },
    { id: "config", label: t("Réglages"), groupStart: true },
    { id: "requests", label: t("Observabilité") },
  ];
  const checkpoint = job?.checkpoint || {};
  const liveDetail = [
    checkpoint.step === "autopilot"
      ? `${t("Pilote automatique")} · ${t(phaseLabel(checkpoint.autopilot_phase))}`
      : checkpoint.step
        ? t(stageLabels[String(checkpoint.step)] || String(checkpoint.step))
        : "",
    checkpoint.current ? `${String(checkpoint.current)} / ${String(checkpoint.total)}` : "",
    checkpoint.step === "book_bible" && checkpoint.batch_current
      ? t("lot {current}/{total}", { current: String(checkpoint.batch_current), total: String(checkpoint.batch_total) })
      : "",
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <Page className="workspace" width="full">
      <header className="workspace-header">
        <div className="workspace-heading">
          <div className="breadcrumb">
            <a href="#library">{t("Bibliothèque")}</a>
            <Icon name="chevronRight" size={12} />
            {project.series_id && (
              <>
                <a href={`#series/${project.series_id}`}>{project.series_name}</a>
                <Icon name="chevronRight" size={12} />
              </>
            )}
            <span className="breadcrumb-current">{project.title}</span>
          </div>
          <h1 className="page-title">{project.title}</h1>
          <p className="workspace-meta">
            <span>{project.author}</span>
            <span className="language-pair">
              <span>{project.source_language.toUpperCase()}</span>
              <Icon name="arrowRight" size={12} />
              <span>{project.target_language.toUpperCase()}</span>
            </span>
            {project.series_name && (
              <span className="book-series">
                {project.series_id ? (
                  <a href={`#series/${project.series_id}`}>{project.series_name}</a>
                ) : (
                  project.series_name
                )}
                {project.project_kind === "serial"
                  ? ` · ${t("flux continu")}`
                  : project.volume_number && ` · ${t("volume {volume}", { volume: project.volume_number })}`}
              </span>
            )}
            {project.source_format && <Badge>{project.source_format.toUpperCase()}</Badge>}
          </p>
        </div>
        <div className="workspace-actions">
          <div className="workspace-overall" aria-label={t("Progression globale")}>
            <strong className="tabular">{formatPercent(progress.current.percent)}</strong>
            <span>{t(progress.current.label)}</span>
            {progress.estimate.remaining_seconds !== null && progress.current.percent < 100 && (
              <small>{duration(progress.estimate.remaining_seconds)}</small>
            )}
          </div>
          {job && (
            <div className="job-controls">
              <StatusPill status={job.status} />
              {["paused", "failed", "blocked", "cancelled"].includes(job.status) ? (
                <Button variant="primary" icon="play" onClick={() => jobAction("resume")}>
                  {job.status === "cancelled"
                    ? t("Reprendre le travail annulé")
                    : job.status === "blocked" && job.stop_reason === "authentication_required"
                      ? t("Reprendre après connexion")
                      : t("Reprendre")}
                </Button>
              ) : (
                <Button icon="pause" onClick={() => jobAction("pause")}>
                  {t("Pause")}
                </Button>
              )}
              {job.status === "waiting" && (
                <Button icon="refresh" onClick={() => jobAction("retry")}>
                  {t("Réessayer maintenant")}
                </Button>
              )}
              {job.status !== "cancelled" && (
                <Button variant="ghost" icon="stop" onClick={() => jobAction("cancel")}>
                  {t("Annuler le travail")}
                </Button>
              )}
            </div>
          )}
          {primary}
          <ExportMenu
            project={project}
            running={exportState === "running"}
            onExport={(format, options) => void run(() => exportFile(format, options))}
          />
          <IconButton
            icon="refresh"
            variant="secondary"
            label={t("Actualiser les données du livre")}
            aria-busy={refreshing}
            disabled={refreshing}
            className={cx(refreshing && "is-spinning")}
            onClick={refresh}
          />
          <Menu
            label={t("Autres actions")}
            trigger={(props) => <IconButton {...props} icon="more" variant="secondary" label={t("Autres actions")} />}
            items={moreItems}
          />
        </div>
      </header>
      <StageProgress
        project={project}
        job={job}
        exportState={exportState}
        status={
          <span className={cx("live-status", `is-${streamState}`)} title={refreshedAt && t("Mis à jour à {time}", { time: refreshedAt })}>
            <span className="live-dot" aria-hidden="true" />
            {streamState === "connected"
              ? t("Suivi connecté")
              : streamState === "reconnecting"
                ? t("Reconnexion du suivi…")
                : t("Connexion au suivi…")}
            {liveDetail && ` · ${liveDetail}`}
          </span>
        }
        counters={
        <span className="workspace-counters">
          {t("{validated} validés · {flagged} ouverts à une relecture facultative · {errors} erreurs · mémoire {memory}", {
            validated: stats.validated,
            flagged: stats.flagged,
            errors: stats.errors,
            memory: project.context_backend,
          })}
          {stats.retained_source > 0 &&
            ` · ${tp(stats.retained_source, "{count} conservé en original", "{count} conservés en original")}`}
        </span>
        }
      />
      <div className="workspace-notices">
        <QueueHint projectId={id} job={job} refresh={tick} />
        <AutopilotStatus
          project={project}
          job={job}
          view={autopilot}
          onOpen={() => void openTab("autopilot")}
          download={downloadButton("secondary", "sm")}
        />
        {!project.provider_id && (
          <Callout
            tone="warning"
            title={t("Aucun provider n’est configuré pour ce livre.")}
            actions={
              <>
                <Button size="sm" onClick={() => void openTab("config")}>
                  {t("Ouvrir les réglages")}
                </Button>
                {user.admin && (
                  <a className="btn btn-sm btn-ghost" href="#settings">
                    {t("Gérer les providers")}
                  </a>
                )}
              </>
            }
          >
            {t("Choisissez le modèle et les langues dans les réglages du livre.")}
          </Callout>
        )}
        {job?.error && job.stop_reason !== "budget_exceeded" && (
          <Callout
            tone={job.status === "waiting" ? "warning" : "danger"}
            role="alert"
            actions={
              <Button size="sm" onClick={() => void openTab("requests")}>
                {t("Voir les requêtes")}
              </Button>
            }
          >
            {job.error}
          </Callout>
        )}
        {job?.stop_reason === "content_refusal" && (
          <Callout
            tone="warning"
            role="status"
            actions={
              job.checkpoint.segment_id ? (
                <Button
                  size="sm"
                  onClick={() =>
                    void run(async () => {
                      const segment = await api<Segment>(`/segments/${job.checkpoint.segment_id}`);
                      setChapter(segment.chapter_id);
                      setFocusRefusal(segment.id);
                      setTab("editor");
                    })
                  }
                >
                  {t("Ouvrir le passage à traiter")}
                </Button>
              ) : (
                <Button size="sm" onClick={() => void openTab("bible")}>
                  {t("Compléter la Book Bible manuellement")}
                </Button>
              )
            }
          >
            {t(
              "Le provider a refusé le traitement. Le texte source est conservé ; ce passage n’est pas compté comme analysé ou traduit automatiquement.",
            )}
          </Callout>
        )}
        {job?.status === "waiting" && (
          <Callout tone="warning" role="status">
            {t("Reprise automatique prévue le {date}.", { date: date(job.next_attempt) })}{" "}
            {tp(
              job.outage_count,
              "{count} interruption consécutive. Les étapes enregistrées sont conservées ; Pause suspend les tentatives automatiques.",
              "{count} interruptions consécutives. Les étapes enregistrées sont conservées ; Pause suspend les tentatives automatiques.",
            )}
          </Callout>
        )}
        {job?.status === "paused" && job.stop_reason !== "budget_exceeded" && (
          <Callout tone="neutral">{t("Pause volontaire — utilisez Reprendre pour continuer.")}</Callout>
        )}
        {job?.status === "paused" && job.stop_reason === "budget_exceeded" && (
          <Callout
            tone="warning"
            role="status"
            title={t("Budget atteint : travail en pause")}
            actions={
              <Button onClick={() => void openTab("config")}>
                {t("Relever le budget")}
              </Button>
            }
          >
            {job.error}
          </Callout>
        )}
        {job?.status === "blocked" && job.stop_reason === "authentication_required" && user.admin && (
          <Callout tone="danger" actions={<a className="btn btn-sm btn-secondary" href="#settings">{t("Ouvrir les paramètres de connexion du provider")}</a>} />
        )}
      </div>
      <div className="workspace-tabs">
        <Tabs
          items={tabs}
          value={tab}
          onChange={(next) => void openTab(next)}
          label={t("Navigation du livre")}
          idPrefix="workspace"
        />
      </div>
      <TabPanel idPrefix="workspace" value={tab} className="workspace-panel">
        {tab === "editor" ? (
          <Editor
            project={project}
            chapters={chapters}
            chapterId={chapter}
            onChapter={async (next) => {
              if (next !== chapter && (await confirmLeave())) setChapter(next);
            }}
            tick={tick}
            run={run}
            refresh={refresh}
            focusRefusal={focusRefusal}
            focus={focus}
            residuals={residuals}
          />
        ) : tab === "characters" ? (
          <Suspense fallback={<LoadingBlock label={t("Chargement du graphe…")} />}>
            <CharacterGraph pid={id} tick={tick} run={run} />
          </Suspense>
        ) : tab === "bible" ? (
          <Bible project={project} run={run} refresh={refresh} tick={tick} />
        ) : tab === "glossary" ? (
          <Glossary project={project} run={run} tick={tick} />
        ) : tab === "quality" ? (
          <Quality project={project} run={run} tick={tick} onOpenPassage={openPassage} />
        ) : tab === "completion" ? (
          <CompletionPanel project={project} run={run} refresh={refresh} tick={tick} />
        ) : tab === "autopilot" ? (
          <AutopilotPanel
            project={project}
            chapters={chapters}
            job={job}
            run={run}
            tick={tick}
            download={downloadButton("primary")}
            onOpenPassage={openPassage}
            onSettings={() => void openTab("config")}
          />
        ) : tab === "validations" ? (
          <ValidationPanel
            project={project}
            chapters={chapters}
            run={run}
            refresh={refresh}
            tick={tick}
            automatic={automatic}
            onOpenPassage={openPassage}
          />
        ) : tab === "requests" ? (
          <Observability project={project} run={run} tick={tick} />
        ) : (
          <div className="stack">
            <BookBudget project={project} user={user} run={run} tick={tick} />
            <ProjectSettings project={project} user={user} run={run} refresh={refresh} />
          </div>
        )}
      </TabPanel>
    </Page>
  );
}
