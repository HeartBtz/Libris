import { useEffect, useState } from "react";
import { api, downloadApi, send } from "../api";
import { registerTranslations, useI18n } from "../i18n";
import type { Job, Project, Provider, Run } from "../types";

const translations: Record<string, string> = {
  "Supprimer définitivement les {count} projets sélectionnés et arrêter leurs travaux ? La mémoire OpenViking distante reste séparée.": "Permanently delete the {count} selected projects and stop their work? Remote OpenViking memory remains separate.",
  "Retirer les {count} livres sélectionnés de leur série ?": "Remove the {count} selected books from their series?",
  "{count} livre(s) retiré(s) de leur série.": "{count} book(s) removed from their series.",
  "Série {series} appliquée à {count} livre(s), numéros conservés.": "Series {series} applied to {count} book(s), numbers retained.",
  "{count} livre(s) numéroté(s) dans {series}.": "{count} book(s) numbered in {series}.",
  "supprimé.": "deleted.", "archivé.": "archived.", "en pause": "paused", "reprise planifiée": "resumption scheduled", "travail annulé": "work cancelled", "aucun travail concerné": "no relevant work", "configuré": "configured", "travail ajouté à la file": "work queued",
  "Actions sur plusieurs livres": "Actions for multiple books", "livre(s) sélectionné(s)": "selected book(s)", "Série commune": "Common series", "Premier volume": "First volume", "Appliquer sans renuméroter": "Apply without renumbering", "Numéroter par titre": "Number by title", "Retirer de la série": "Remove from series", "Provider commun": "Common provider", "Conserver les providers individuels": "Keep individual providers", "Source mémoire": "Memory source", "Conserver les sources individuelles": "Keep individual sources", "Mémoire interne": "Internal memory", "Langue cible": "Target language", "Qualité": "Quality", "Conserver les qualités individuelles": "Keep individual qualities", "Rapide": "Fast", "Haute qualité": "High quality", "Instructions communes": "Common instructions", "Conserver si vide": "Keep if empty", "Configurer la sélection": "Configure selection", "Analyser la sélection": "Analyze selection", "Traduire la sélection": "Translate selection", "Mettre la sélection en pause": "Pause selection", "Reprendre la sélection": "Resume selection", "Annuler les analyses": "Cancel analyses", "Annuler les traductions": "Cancel translations", "Archiver la sélection": "Archive selection", "Supprimer la sélection": "Delete selection",
  "Les livres avancent en parallèle selon les limites du worker et de chaque provider. Les passages d’un même livre gardent leur ordre narratif.": "Books progress in parallel according to worker and provider limits. Segments in the same book retain their narrative order.",
  "Exporter les EPUB": "Export EPUBs",
  "Archive de {count} EPUB téléchargée.": "Archive containing {count} EPUBs downloaded.",
};
registerTranslations(translations);

export function BatchActions({
  books,
  run,
  refresh,
  onDeleted,
  scopeLabel,
}: {
  books: Project[];
  run: Run;
  refresh: () => Promise<void>;
  onDeleted: (ids: string[]) => void;
  scopeLabel?: string;
}) {
  const { t } = useI18n();
  const [providers, setProviders] = useState<Provider[]>([]);
  const [provider, setProvider] = useState("");
  const [memoryBackend, setMemoryBackend] = useState<
    "" | Project["context_backend"]
  >("");
  // Empty means "keep each book's own value", like the provider, memory and instruction fields.
  const [language, setLanguage] = useState("");
  const [quality, setQuality] = useState<"" | Project["quality"]>("");
  const [seriesName, setSeriesName] = useState(books[0]?.series_name || "");
  const [firstVolume, setFirstVolume] = useState(1);
  const [commonInstructions, setCommonInstructions] = useState("");
  const [results, setResults] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const selectionKey = books
    .map((book) => book.id)
    .sort()
    .join(":");
  useEffect(() => {
    void run.background(async () => setProviders(await api("/providers")));
  }, [run]);
  useEffect(() => {
    const commonSeries = books.every(
      (book) => book.series_name === books[0]?.series_name,
    )
      ? books[0]?.series_name || ""
      : "";
    setSeriesName(commonSeries);
    setFirstVolume(
      Math.min(...books.map((book) => book.volume_number || 1)),
    );
  }, [selectionKey, scopeLabel]);
  async function apply(
    action:
      | "configure"
      | "analyze"
      | "translate"
      | "pause"
      | "resume"
      | "cancel_analysis"
      | "cancel_translation"
      | "preserve_series"
      | "number_series"
      | "clear_series"
      | "archive"
      | "delete",
  ) {
    if (
      action === "delete" &&
      !confirm(
        t("Supprimer définitivement les {count} projets sélectionnés et arrêter leurs travaux ? La mémoire OpenViking distante reste séparée.").replace("{count}", String(books.length)),
      )
    )
      return;
    setBusy(true);
    setResults([]);
    if (["preserve_series", "number_series", "clear_series"].includes(action)) {
      if (
        action === "clear_series" &&
        !confirm(t("Retirer les {count} livres sélectionnés de leur série ?").replace("{count}", String(books.length)))
      ) {
        setBusy(false);
        return;
      }
      const ordered =
        action === "number_series"
          ? [...books].sort((a, b) =>
              a.title.localeCompare(b.title, "fr", { numeric: true }),
            )
          : books;
      const mode =
        action === "number_series"
          ? "sequential"
          : action === "clear_series"
            ? "clear"
            : "preserve";
      try {
        await send(
          "/projects/batch/series",
          {
            project_ids: ordered.map((book) => book.id),
            series_name: seriesName.trim(),
            first_volume: firstVolume,
            mode,
          },
          "PUT",
        );
        setResults([
          mode === "clear"
            ? t("{count} livre(s) retiré(s) de leur série.").replace("{count}", String(ordered.length))
            : mode === "preserve"
              ? t("Série {series} appliquée à {count} livre(s), numéros conservés.").replace("{series}", seriesName.trim()).replace("{count}", String(ordered.length))
              : t("{count} livre(s) numéroté(s) dans {series}.").replace("{count}", String(ordered.length)).replace("{series}", seriesName.trim()),
        ]);
        await run(refresh);
      } catch (error) {
        setResults([error instanceof Error ? error.message : String(error)]);
      } finally {
        setBusy(false);
      }
      return;
    }
    const removed: string[] = [];
    const outcomes = await Promise.all(
      books.map(async (p) => {
        try {
          if (action === "delete") {
            await api(`/projects/${p.id}?stop_jobs=true`, { method: "DELETE" });
            removed.push(p.id);
            return `${p.title} : ${t("supprimé.")}`;
          }
          if (action === "archive") {
            await send(`/projects/${p.id}/archive`);
            removed.push(p.id);
            return `${p.title} : ${t("archivé.")}`;
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
                (j) => !["paused", "cancelled", "failed"].includes(j.status),
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
            return `${p.title} : ${relevant.length ? (operation === "pause" ? t("en pause") : operation === "resume" ? t("reprise planifiée") : t("travail annulé")) : t("aucun travail concerné")}.`;
          }
          if (action === "configure") {
            await send(
              `/projects/${p.id}`,
              {
                title: p.title,
                author: p.author,
                series_name: p.series_name,
                volume_number: p.volume_number,
                source_language: p.source_language,
                target_language: language.trim() || p.target_language,
                provider_id: provider || p.provider_id,
                quality: quality || p.quality,
                context_backend: memoryBackend || p.context_backend,
                instructions: commonInstructions || p.instructions,
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
          return `${p.title} : ${action === "configure" ? t("configuré") : t("travail ajouté à la file")}.`;
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
  async function exportEpubs() {
    setBusy(true);
    setResults([]);
    try {
      await downloadApi("/exports/epub", "libris-epubs.zip", {
        project_ids: books.map((book) => book.id),
      });
      setResults([
        t("Archive de {count} EPUB téléchargée.").replace("{count}", String(books.length)),
      ]);
    } catch (error) {
      setResults([error instanceof Error ? error.message : String(error)]);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="notice" aria-label={t("Actions sur plusieurs livres")}>
      <h3>
        {scopeLabel ? `${scopeLabel} · ` : ""}
        {books.length} {t("livre(s) sélectionné(s)")}
      </h3>
      <div className="actions">
        <label>
          {t("Série commune")}
          <input
            value={seriesName}
            onChange={(event) => setSeriesName(event.target.value)}
          />
        </label>
        <label>
          {t("Premier volume")}
          <input
            type="number"
            min="1"
            value={firstVolume}
            onChange={(event) =>
              setFirstVolume(Number(event.target.value) || 1)
            }
          />
        </label>
        <button
          disabled={busy || !seriesName.trim()}
          onClick={() => void apply("preserve_series")}
        >
          {t("Appliquer sans renuméroter")}
        </button>
        <button
          disabled={busy || !seriesName.trim()}
          onClick={() => void apply("number_series")}
        >
          {t("Numéroter par titre")}
        </button>
        <button
          disabled={busy || !books.some((book) => book.series_name)}
          onClick={() => void apply("clear_series")}
        >
          {t("Retirer de la série")}
        </button>
      </div>
      <div className="actions">
        <label>
          {t("Provider commun")}
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
          >
            <option value="">{t("Conserver les providers individuels")}</option>
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("Langue cible")}
          <input
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            placeholder={t("Conserver si vide")}
          />
        </label>
        <label>
          {t("Source mémoire")}
          <select
            value={memoryBackend}
            onChange={(event) =>
              setMemoryBackend(
                event.target.value as "" | Project["context_backend"],
              )
            }
          >
            <option value="">{t("Conserver les sources individuelles")}</option>
            <option value="internal">{t("Mémoire interne")}</option>
            <option value="hybrid">Hybrid</option>
            <option value="openviking">OpenViking</option>
          </select>
        </label>
        <label>
          {t("Qualité")}
          <select
            value={quality}
            onChange={(e) =>
              setQuality(e.target.value as "" | Project["quality"])
            }
          >
            <option value="">{t("Conserver les qualités individuelles")}</option>
            <option value="fast">{t("Rapide")}</option>
            <option value="normal">Normal</option>
            <option value="high">{t("Haute qualité")}</option>
            <option value="maximum">Maximum</option>
          </select>
        </label>
        <label>
          {t("Instructions communes")}
          <input
            value={commonInstructions}
            onChange={(event) => setCommonInstructions(event.target.value)}
            placeholder={t("Conserver si vide")}
          />
        </label>
        <button disabled={busy} onClick={() => void apply("configure")}>
          {t("Configurer la sélection")}
        </button>
      </div>
      <div className="actions">
        <button disabled={busy} className="primary" onClick={() => void exportEpubs()}>
          {t("Exporter les EPUB")}
        </button>
        <button disabled={busy} onClick={() => void apply("analyze")}>
          {t("Analyser la sélection")}
        </button>
        <button
          disabled={busy}
          className="primary"
          onClick={() => void apply("translate")}
        >
          {t("Traduire la sélection")}
        </button>
        <button disabled={busy} onClick={() => void apply("pause")}>
          {t("Mettre la sélection en pause")}
        </button>
        <button disabled={busy} onClick={() => void apply("resume")}>
          {t("Reprendre la sélection")}
        </button>
        <button disabled={busy} onClick={() => void apply("cancel_analysis")}>
          {t("Annuler les analyses")}
        </button>
        <button
          disabled={busy}
          onClick={() => void apply("cancel_translation")}
        >
          {t("Annuler les traductions")}
        </button>
        <button disabled={busy} onClick={() => void apply("archive")}>
          {t("Archiver la sélection")}
        </button>
        <button
          disabled={busy}
          className="danger"
          onClick={() => void apply("delete")}
        >
          {t("Supprimer la sélection")}
        </button>
      </div>
      <p className="muted">
        {t("Les livres avancent en parallèle selon les limites du worker et de chaque provider. Les passages d’un même livre gardent leur ordre narratif.")}
      </p>
      {!!results.length && (
        <ul role="status">
          {results.map((r, i) => (
            <li key={i} className="error-text">{r}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
