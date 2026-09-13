import { useEffect, useState } from "react";
import { api, send } from "../api";
import type { Job, Project, Provider, Run } from "../types";

export function BatchActions({
  books,
  run,
  refresh,
  onDeleted,
}: {
  books: Project[];
  run: Run;
  refresh: () => Promise<void>;
  onDeleted: (ids: string[]) => void;
}) {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [provider, setProvider] = useState("");
  const [language, setLanguage] = useState("fr");
  const [quality, setQuality] = useState("high");
  const [results, setResults] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    void run(async () => setProviders(await api("/providers")));
  }, [run]);
  async function apply(
    action:
      | "configure"
      | "analyze"
      | "translate"
      | "pause"
      | "resume"
      | "cancel_analysis"
      | "cancel_translation"
      | "delete",
  ) {
    if (
      action === "delete" &&
      !confirm(
        `Supprimer définitivement les ${books.length} projets sélectionnés et arrêter leurs travaux ? La mémoire OpenViking distante reste séparée.`,
      )
    )
      return;
    setBusy(true);
    setResults([]);
    const removed: string[] = [];
    const outcomes = await Promise.all(
      books.map(async (p) => {
        try {
          if (action === "delete") {
            await api(`/projects/${p.id}?stop_jobs=true`, { method: "DELETE" });
            removed.push(p.id);
            return `${p.title} : supprimé.`;
          }
          if (
            [
              "pause",
              "resume",
              "cancel_analysis",
              "cancel_translation",
            ].includes(action)
          ) {
            const jobs = await api<Job[]>(`/projects/${p.id}/jobs`);
            let relevant = jobs.filter((j) =>
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
            if (!relevant.length && ["failed", "cancelled"].includes(p.status))
              relevant = jobs.filter((j) => j.status === p.status).slice(0, 1);
            if (action === "cancel_analysis")
              relevant = relevant.filter((j) => j.operation === "analyze");
            if (action === "cancel_translation")
              relevant = relevant.filter((j) =>
                ["translate", "review", "consistency"].includes(j.operation),
              );
            if (action === "pause")
              relevant = relevant.filter(
                (j) => !["paused", "cancelled"].includes(j.status),
              );
            if (action === "resume")
              relevant = relevant.filter((j) =>
                [
                  "paused",
                  "waiting",
                  "blocked",
                  "failed",
                  "cancelled",
                ].includes(j.status),
              );
            const operation = action.startsWith("cancel_") ? "cancel" : action;
            for (const job of relevant) {
              if (job.status !== "cancelled" || operation === "resume")
                await send(`/projects/${p.id}/jobs/${job.id}/${operation}`);
            }
            return `${p.title} : ${relevant.length ? (operation === "pause" ? "en pause" : operation === "resume" ? "reprise planifiée" : "travail annulé") : "aucun travail concerné"}.`;
          }
          if (action === "configure") {
            await send(
              `/projects/${p.id}`,
              {
                title: p.title,
                author: p.author,
                source_language: p.source_language,
                target_language: language,
                provider_id: provider || p.provider_id,
                quality,
                context_backend: p.context_backend,
                instructions: p.instructions,
              },
              "PUT",
            );
          } else {
            const result = await send<{ message?: string }>(
              `/projects/${p.id}/jobs`,
              { operation: action },
            );
            if (result.message) return `${p.title} : ${result.message}`;
          }
          return `${p.title} : ${action === "configure" ? "configuré" : "travail ajouté à la file"}.`;
        } catch (e) {
          return `${p.title} : ${e instanceof Error ? e.message : String(e)}`;
        }
      }),
    );
    setResults(outcomes);
    onDeleted(removed);
    await run(refresh);
    setBusy(false);
  }
  return (
    <section className="notice" aria-label="Actions sur plusieurs livres">
      <h3>{books.length} livre(s) sélectionné(s)</h3>
      <div className="actions">
        <label>
          Provider commun
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
          >
            <option value="">Conserver les providers individuels</option>
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Langue cible
          <input
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
          />
        </label>
        <label>
          Qualité
          <select value={quality} onChange={(e) => setQuality(e.target.value)}>
            <option value="fast">Rapide</option>
            <option value="normal">Normal</option>
            <option value="high">Haute qualité</option>
            <option value="maximum">Maximum</option>
          </select>
        </label>
        <button disabled={busy} onClick={() => void apply("configure")}>
          Configurer la sélection
        </button>
      </div>
      <div className="actions">
        <button disabled={busy} onClick={() => void apply("analyze")}>
          Analyser la sélection
        </button>
        <button
          disabled={busy}
          className="primary"
          onClick={() => void apply("translate")}
        >
          Traduire la sélection
        </button>
        <button disabled={busy} onClick={() => void apply("pause")}>
          Mettre la sélection en pause
        </button>
        <button disabled={busy} onClick={() => void apply("resume")}>
          Reprendre la sélection
        </button>
        <button disabled={busy} onClick={() => void apply("cancel_analysis")}>
          Annuler les analyses
        </button>
        <button
          disabled={busy}
          onClick={() => void apply("cancel_translation")}
        >
          Annuler les traductions
        </button>
        <button
          disabled={busy}
          className="danger"
          onClick={() => void apply("delete")}
        >
          Supprimer la sélection
        </button>
      </div>
      <p className="muted">
        Les livres avancent en parallèle selon les limites du worker et de
        chaque provider. Les passages d’un même livre gardent leur ordre
        narratif.
      </p>
      {!!results.length && (
        <ul role="status">
          {results.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
