import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { api, date, labels, number, send } from "../api";
import type { Chapter, Job, Project, Run, Segment, User } from "../types";
import { Editor } from "./Editor";
import { ValidationPanel } from "./ValidationPanel";
const CharacterGraph = lazy(() => import("./CharacterGraph"));
const stageLabels: Record<string, string> = {
  chapter_analysis: "Analyse des passages",
  book_bible: "Synthèse de la Book Bible",
  translation: "Traduction",
  consistency: "Cohérence globale",
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
  const [project, setProject] = useState<Project | null>(null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [chapter, setChapter] = useState("");
  const [focusRefusal, setFocusRefusal] = useState("");
  const [tab, setTab] = useState("editor");
  const [tick, setTick] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshedAt, setRefreshedAt] = useState("");
  const [streamState, setStreamState] = useState("Connexion au suivi…");
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
    stream.onopen = () => setStreamState("Suivi connecté");
    stream.onerror = () => setStreamState("Reconnexion du suivi…");
    stream.onmessage = () => {
      clearTimeout(pending);
      pending = setTimeout(() => setTick((v) => v + 1), 300);
    };
    return () => {
      clearTimeout(pending);
      stream.close();
    };
  }, [id]);
  const refresh = () => setTick((t) => t + 1);
  if (!project) return <main className="loading">Ouverture du livre…</main>;
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
  const analyzing = job?.operation === "analyze";
  const sourceDone = project.stats.analyzed_segments || 0;
  const bibleDone = project.stats.synthesized_chapters || 0;
  const analysisReady =
    sourceDone === project.stats.total && bibleDone === project.stats.chapters;
  async function start(operation: string, force = false) {
    await send(`/projects/${id}/jobs`, { operation, force });
    refresh();
  }
  const progress = project.stats.total
    ? Math.round((project.stats.translated / project.stats.total) * 100)
    : 0;
  return (
    <main className="workspace">
      <div className="workspace-heading">
        <div className="refresh-control">
          <button
            aria-label="Actualiser les données du livre"
            aria-busy={refreshing}
            disabled={refreshing}
            onClick={refresh}
          >
            {refreshing ? "Actualisation…" : "Actualiser"}
          </button>
          <small>{refreshedAt && `Mis à jour à ${refreshedAt}`}</small>
        </div>
        <div>
          <a className="breadcrumb" href="#library">
            Bibliothèque /
          </a>
          <h1>{project.title}</h1>
          <span className="muted">
            {project.author} · {project.source_language} →{" "}
            {project.target_language}
          </span>
        </div>
        <div className="workspace-progress">
          <strong>
            {analyzing
              ? "Analyse"
              : analysisReady && !project.stats.translated
                ? "Prêt"
                : `${progress}%`}
          </strong>
          <progress
            value={
              analyzing
                ? sourceDone < project.stats.total
                  ? sourceDone
                  : bibleDone
                : project.stats.translated
            }
            max={
              analyzing && sourceDone === project.stats.total
                ? project.stats.chapters || 1
                : project.stats.total || 1
            }
          />
          <span>
            {analyzing
              ? `Lecture ${sourceDone}/${project.stats.total} · Synthèse ${bibleDone}/${project.stats.chapters}`
              : analysisReady && !project.stats.translated
                ? "Analyse terminée · traduction à lancer"
                : `${number(project.stats.translated)} / ${number(project.stats.total)} passages traduits`}
          </span>
        </div>
      </div>
      <div className="pipeline-bar">
        <div className="pipeline-steps">
          <span className="done">1 · Import</span>
          <span className={Object.keys(project.bible).length ? "done" : ""}>
            2 · Analyse & mémoire
          </span>
          <span className={progress === 100 ? "done" : ""}>3 · Traduction</span>
          <span>4 · Relecture</span>
          <span>5 · Export</span>
        </div>
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
                    ? "Reprendre le travail annulé"
                    : job.status === "blocked" &&
                        job.stop_reason === "authentication_required"
                      ? "Reprendre après connexion"
                      : "Reprendre"}
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
                  Pause
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
                  Réessayer maintenant
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
                  Annuler
                </button>
              )}
            </>
          ) : (
            <>
              <button
                disabled={!project.provider_id || analysisReady}
                onClick={() => void run(() => start("analyze"))}
              >
                {analysisReady ? "Analyse terminée" : "Analyser le livre"}
              </button>
              {analysisReady && (
                <button
                  onClick={() => {
                    if (
                      confirm(
                        "Relancer une analyse complète des résultats automatiques ? Les analyses et décisions humaines sont conservées. Cette opération rappellera le modèle.",
                      )
                    )
                      void run(() => start("analyze", true));
                  }}
                >
                  Réanalyse complète
                </button>
              )}
              <button
                className="primary"
                disabled={
                  !project.provider_id || !Object.keys(project.bible).length
                }
                onClick={() => void run(() => start("translate"))}
              >
                Traduire
              </button>
            </>
          )}
          <details className="export-menu">
            <summary className="button">Exporter ↓</summary>
            <div>
              {[
                ["epub", "EPUB traduit"],
                ["txt", "Texte"],
                ["md", "Markdown"],
                ["bible", "Book Bible JSON"],
                ["project", "Projet complet"],
              ].map(([format, label]) => (
                <a key={format} href={`/api/projects/${id}/export/${format}`}>
                  {label}
                </a>
              ))}
              <a href={`/api/projects/${id}/export/epub?allow_source=true`}>
                EPUB partiel · originaux conservés
              </a>
              <a
                href={`/api/projects/${id}/coverage`}
                target="_blank"
                rel="noreferrer"
              >
                Rapport de couverture
              </a>
            </div>
          </details>
        </div>
      </div>
      {!project.provider_id && (
        <div className="notice">
          Configurez le provider et les langues dans{" "}
          <button className="link" onClick={() => setTab("config")}>
            Configuration
          </button>
          . {user.admin && <a href="#settings">Gérer les providers →</a>}
        </div>
      )}
      {job?.error && (
        <div
          className={job.status === "waiting" ? "notice" : "error-banner"}
          role="alert"
        >
          {job.error}{" "}
          <button onClick={() => setTab("requests")}>Voir les requêtes</button>
        </div>
      )}
      {job?.stop_reason === "content_refusal" && (
        <div className="notice" role="status">
          Le provider a refusé le traitement. Le texte source est conservé ; ce
          passage n’est pas compté comme analysé ou traduit automatiquement.
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
              Ouvrir le passage à traiter
            </button>
          ) : (
            <button onClick={() => setTab("bible")}>
              Compléter la Book Bible manuellement
            </button>
          )}
        </div>
      )}
      {job?.status === "waiting" && (
        <div className="notice" role="status">
          Reprise automatique prévue : {date(job.next_attempt)} ·{" "}
          {job.outage_count} interruption(s) consécutive(s). Les étapes
          enregistrées sont conservées. Utilisez Pause pour suspendre les
          tentatives automatiques.
        </div>
      )}
      {job?.status === "paused" && (
        <p className="muted">
          Pause volontaire — utilisez Reprendre pour continuer.
        </p>
      )}
      {job?.status === "blocked" &&
        job.stop_reason === "authentication_required" &&
        user.admin && (
          <p>
            <a href="#settings">
              Ouvrir les paramètres de connexion du provider →
            </a>
          </p>
        )}
      <div className="workspace-status">
        <span>
          {streamState}
          {job?.checkpoint.step
            ? ` · ${stageLabels[String(job.checkpoint.step)] || String(job.checkpoint.step)}`
            : ""}
          {job?.checkpoint.current
            ? ` · ${String(job.checkpoint.current)} / ${String(job.checkpoint.total)}`
            : ""}
          {job?.checkpoint.step === "book_bible" && job.checkpoint.batch_current
            ? ` · lot ${String(job.checkpoint.batch_current)}/${String(job.checkpoint.batch_total)}`
            : ""}
        </span>
        <span>
          {project.stats.validated} validés humainement ·{" "}
          {project.stats.flagged} à vérifier · {project.stats.errors} erreurs ·
          Mémoire {project.context_backend}
          {project.stats.retained_source > 0
            ? ` · ${project.stats.retained_source} passage(s) conservé(s) en original`
            : ""}
        </span>
      </div>
      <div className="tabs">
        {[
          ["editor", "Traduction"],
          [
            "validations",
            `Validations${project.stats.flagged ? ` (${project.stats.flagged})` : ""}`,
          ],
          ["bible", "Book Bible"],
          ["characters", "Personnages & liens"],
          ["glossary", "Glossaire"],
          ["quality", "Qualité"],
          ["requests", "Observabilité"],
          ["config", "Configuration"],
        ].map(([key, label]) => (
          <button
            key={key}
            className={tab === key ? "active" : ""}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "editor" ? (
        <div className="workspace-body">
          <aside className="chapter-list">
            <h3>
              Sections du livre <span>{chapters.length}</span>
            </h3>
            {chapters.map((c) => (
              <button
                key={c.id}
                className={chapter === c.id ? "active" : ""}
                onClick={() => setChapter(c.id)}
              >
                <small>{String(c.position + 1).padStart(2, "0")}</small>
                <span>{c.title}</span>
                {c.analyzed && <span className="dot" title="Analysé" />}
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
            <Suspense fallback={<p>Chargement du graphe…</p>}>
              <CharacterGraph pid={id} tick={tick} run={run} />
            </Suspense>
          ) : tab === "bible" ? (
            <Bible project={project} run={run} refresh={refresh} tick={tick} />
          ) : tab === "glossary" ? (
            <Glossary project={project} run={run} tick={tick} />
          ) : tab === "quality" ? (
            <Quality project={project} run={run} tick={tick} />
          ) : tab === "validations" ? (
            <ValidationPanel
              project={project}
              chapters={chapters}
              run={run}
              refresh={refresh}
              tick={tick}
            />
          ) : tab === "requests" ? (
            <Observability project={project} run={run} tick={tick} />
          ) : (
            <ProjectSettings project={project} run={run} refresh={refresh} />
          )}
        </div>
      )}
    </main>
  );
}
