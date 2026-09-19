import { useEffect, useState } from "react";
import { api, downloadApi, send } from "../api";
import { getLocale, registerTranslations, useI18n } from "../i18n";
import type { Job, Project, ProviderSummary, Run } from "../types";
import {
  Button,
  Dialog,
  Field,
  FormGrid,
  IconButton,
  Input,
  Menu,
  Select,
  useDialogs,
} from "../ui";

registerTranslations({
  "Supprimer les livres sélectionnés ?": "Delete the selected books?",
  "Les {count} projets sélectionnés et leurs travaux seront supprimés définitivement. La mémoire OpenViking distante n’est effacée que si un administrateur a activé son nettoyage.":
    "The {count} selected projects and their jobs will be permanently deleted. Remote OpenViking memory is removed only if an administrator switched its cleanup on.",
  "Retirer les livres de leur série ?": "Remove the books from their series?",
  "Les {count} livres sélectionnés ne feront plus partie d’une série.": "The {count} selected books will no longer belong to a series.",
  "{count} livre retiré de sa série.": "{count} book removed from its series.",
  "{count} livres retirés de leur série.": "{count} books removed from their series.",
  "Série {series} appliquée à {count} livre, numéro conservé.": "Series {series} applied to {count} book, number kept.",
  "Série {series} appliquée à {count} livres, numéros conservés.": "Series {series} applied to {count} books, numbers kept.",
  "{count} livre numéroté dans {series}.": "{count} book numbered in {series}.",
  "{count} livres numérotés dans {series}.": "{count} books numbered in {series}.",
  "supprimé.": "deleted.",
  "archivé.": "archived.",
  "en pause": "paused",
  "reprise planifiée": "resumption scheduled",
  "travail annulé": "work cancelled",
  "aucun travail concerné": "no relevant work",
  configuré: "configured",
  "travail ajouté à la file": "work queued",
  "Actions sur plusieurs livres": "Actions for multiple books",
  "{count} livre sélectionné": "{count} book selected",
  "{count} livres sélectionnés": "{count} books selected",
  "Série commune": "Common series",
  "Premier volume": "First volume",
  "Appliquer sans renuméroter": "Apply without renumbering",
  "Numéroter par titre": "Number by title",
  "Retirer de la série": "Remove from series",
  "Provider commun": "Common provider",
  "Conserver les providers individuels": "Keep individual providers",
  "Source mémoire": "Memory source",
  "Conserver les sources individuelles": "Keep individual sources",
  "Mémoire interne": "Internal memory",
  "Langue cible": "Target language",
  Qualité: "Quality",
  "Conserver les qualités individuelles": "Keep individual qualities",
  Rapide: "Fast",
  "Haute qualité": "High quality",
  "Instructions communes": "Common instructions",
  "Conserver si vide": "Keep if empty",
  "Configurer…": "Configure…",
  "Configurer la sélection": "Configure selection",
  "Configurer les livres sélectionnés": "Configure the selected books",
  "Les champs laissés sur « Conserver » ne modifient pas les livres.":
    "Fields left on “Keep” do not change the books.",
  Analyser: "Analyze",
  Traduire: "Translate",
  "Analyser la sélection": "Analyze selection",
  "Traduire la sélection": "Translate selection",
  "Mettre la sélection en pause": "Pause selection",
  "Reprendre la sélection": "Resume selection",
  "Annuler les analyses": "Cancel analyses",
  "Annuler les traductions": "Cancel translations",
  "Archiver la sélection": "Archive selection",
  "Supprimer la sélection": "Delete selection",
  "Plus d’actions": "More actions",
  "Désélectionner tout": "Clear selection",
  Série: "Series",
  Réglages: "Settings",
  "Les livres avancent en parallèle selon les limites du worker et de chaque provider. Les passages d’un même livre gardent leur ordre narratif.":
    "Books progress in parallel according to worker and provider limits. Passages in the same book keep their narrative order.",
  "Exporter les EPUB": "Export EPUBs",
  "Archive de {count} EPUB téléchargée.": "Archive of {count} EPUB downloaded.",
  "Archive de {count} EPUB téléchargées.": "Archive of {count} EPUBs downloaded.",
  "Supprimer définitivement": "Delete permanently",
  Retirer: "Remove",
});

type Action =
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
  | "delete";

const HELD = ["pending", "waiting", "blocked", "analyzing", "translating", "reviewing", "syncing", "paused"];

export function BatchActions({
  books,
  run,
  refresh,
  onDeleted,
  onClear,
  scopeLabel,
}: {
  books: Project[];
  run: Run;
  refresh: () => Promise<void>;
  onDeleted: (ids: string[]) => void;
  onClear: () => void;
  scopeLabel?: string;
}) {
  const { t, tp } = useI18n();
  const { confirm } = useDialogs();
  const [providers, setProviders] = useState<ProviderSummary[]>([]);
  const [configuring, setConfiguring] = useState(false);
  const [provider, setProvider] = useState("");
  const [memoryBackend, setMemoryBackend] = useState<"" | Project["context_backend"]>("");
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
    const commonSeries = books.every((book) => book.series_name === books[0]?.series_name)
      ? books[0]?.series_name || ""
      : "";
    setSeriesName(commonSeries);
    setFirstVolume(Math.min(...books.map((book) => book.volume_number || 1)));
  }, [selectionKey, scopeLabel]);
  async function apply(action: Action) {
    if (
      action === "delete" &&
      !(await confirm({
        title: t("Supprimer les livres sélectionnés ?"),
        message: t(
          "Les {count} projets sélectionnés et leurs travaux seront supprimés définitivement. La mémoire OpenViking distante n’est effacée que si un administrateur a activé son nettoyage.",
          { count: books.length },
        ),
        confirmLabel: t("Supprimer définitivement"),
        tone: "danger",
      }))
    )
      return;
    if (
      action === "clear_series" &&
      !(await confirm({
        title: t("Retirer les livres de leur série ?"),
        message: t("Les {count} livres sélectionnés ne feront plus partie d’une série.", { count: books.length }),
        confirmLabel: t("Retirer"),
      }))
    )
      return;
    setBusy(true);
    setResults([]);
    if (["preserve_series", "number_series", "clear_series"].includes(action)) {
      const ordered =
        action === "number_series"
          ? [...books].sort((a, b) => a.title.localeCompare(b.title, getLocale(), { numeric: true }))
          : books;
      const mode = action === "number_series" ? "sequential" : action === "clear_series" ? "clear" : "preserve";
      const series = seriesName.trim();
      try {
        await send(
          "/projects/batch/series",
          { project_ids: ordered.map((book) => book.id), series_name: series, first_volume: firstVolume, mode },
          "PUT",
        );
        setResults([
          mode === "clear"
            ? tp(ordered.length, "{count} livre retiré de sa série.", "{count} livres retirés de leur série.")
            : mode === "preserve"
              ? tp(
                  ordered.length,
                  "Série {series} appliquée à {count} livre, numéro conservé.",
                  "Série {series} appliquée à {count} livres, numéros conservés.",
                  { series },
                )
              : tp(ordered.length, "{count} livre numéroté dans {series}.", "{count} livres numérotés dans {series}.", {
                  series,
                }),
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
          if (["pause", "resume", "cancel_analysis", "cancel_translation"].includes(action)) {
            const jobs = await api<Job[]>(`/projects/${p.id}/jobs`);
            let relevant = jobs.filter((j) => HELD.includes(j.status));
            if (!relevant.length && ["failed", "cancelled"].includes(p.status))
              relevant = jobs.filter((j) => j.status === p.status).slice(0, 1);
            if (action === "cancel_analysis") relevant = relevant.filter((j) => j.operation === "analyze");
            if (action === "cancel_translation")
              relevant = relevant.filter((j) => ["translate", "review", "consistency"].includes(j.operation));
            if (action === "pause")
              relevant = relevant.filter((j) => !["paused", "cancelled", "failed"].includes(j.status));
            if (action === "resume")
              relevant = relevant.filter((j) => ["paused", "waiting", "blocked", "failed", "cancelled"].includes(j.status));
            const operation = action.startsWith("cancel_") ? "cancel" : action;
            for (const job of relevant) {
              if (job.status !== "cancelled" || operation === "resume")
                await send(`/projects/${p.id}/jobs/${job.id}/${operation}`);
            }
            return `${p.title} : ${
              relevant.length
                ? operation === "pause"
                  ? t("en pause")
                  : operation === "resume"
                    ? t("reprise planifiée")
                    : t("travail annulé")
                : t("aucun travail concerné")
            }.`;
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
            const result = await send<{ message?: string }>(`/projects/${p.id}/jobs`, { operation: action });
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
      await downloadApi("/exports/epub", "libris-epubs.zip", { project_ids: books.map((book) => book.id) });
      setResults([tp(books.length, "Archive de {count} EPUB téléchargée.", "Archive de {count} EPUB téléchargées.")]);
    } catch (error) {
      setResults([error instanceof Error ? error.message : String(error)]);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="batch-bar" aria-label={t("Actions sur plusieurs livres")}>
      <div className="batch-bar-main">
        <IconButton icon="x" size="sm" label={t("Désélectionner tout")} onClick={onClear} />
        <strong className="batch-count">
          {scopeLabel ? `${scopeLabel} · ` : ""}
          {tp(books.length, "{count} livre sélectionné", "{count} livres sélectionnés")}
        </strong>
        <div className="batch-actions">
          <Button size="sm" disabled={busy} icon="settings" onClick={() => setConfiguring(true)}>
            {t("Configurer…")}
          </Button>
          <Button size="sm" disabled={busy} icon="sparkles" onClick={() => void apply("analyze")} aria-label={t("Analyser la sélection")}>
            {t("Analyser")}
          </Button>
          <Button size="sm" disabled={busy} icon="download" onClick={() => void exportEpubs()}>
            {t("Exporter les EPUB")}
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={busy}
            icon="languages"
            onClick={() => void apply("translate")}
            aria-label={t("Traduire la sélection")}
          >
            {t("Traduire")}
          </Button>
          <Menu
            label={t("Plus d’actions")}
            placement="up"
            trigger={(props) => <IconButton {...props} icon="more" size="sm" variant="secondary" label={t("Plus d’actions")} />}
            items={[
              { label: t("Mettre la sélection en pause"), icon: "pause", disabled: busy, onSelect: () => void apply("pause") },
              { label: t("Reprendre la sélection"), icon: "play", disabled: busy, onSelect: () => void apply("resume") },
              { label: t("Annuler les analyses"), icon: "stop", disabled: busy, onSelect: () => void apply("cancel_analysis") },
              {
                label: t("Annuler les traductions"),
                icon: "stop",
                disabled: busy,
                onSelect: () => void apply("cancel_translation"),
              },
              { kind: "separator" },
              { label: t("Archiver la sélection"), icon: "archive", disabled: busy, onSelect: () => void apply("archive") },
              {
                label: t("Supprimer la sélection"),
                icon: "trash",
                danger: true,
                disabled: busy,
                onSelect: () => void apply("delete"),
              },
            ]}
          />
        </div>
      </div>
      {!!results.length && (
        <ul role="status" className="batch-results">
          {results.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
      <Dialog
        open={configuring}
        onClose={() => setConfiguring(false)}
        title={t("Configurer les livres sélectionnés")}
        description={t("Les champs laissés sur « Conserver » ne modifient pas les livres.")}
        size="lg"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfiguring(false)}>
              {t("Annuler")}
            </Button>
            <Button variant="primary" disabled={busy} onClick={() => void apply("configure")}>
              {t("Configurer la sélection")}
            </Button>
          </>
        }
      >
        <div className="stack">
          <FormGrid>
            <Field label={t("Provider commun")}>
              <Select value={provider} onChange={(e) => setProvider(e.target.value)}>
                <option value="">{t("Conserver les providers individuels")}</option>
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={t("Langue cible")}>
              <Input value={language} onChange={(e) => setLanguage(e.target.value)} placeholder={t("Conserver si vide")} />
            </Field>
            <Field label={t("Source mémoire")}>
              <Select
                value={memoryBackend}
                onChange={(event) => setMemoryBackend(event.target.value as "" | Project["context_backend"])}
              >
                <option value="">{t("Conserver les sources individuelles")}</option>
                <option value="internal">{t("Mémoire interne")}</option>
                <option value="hybrid">Hybrid</option>
                <option value="openviking">OpenViking</option>
              </Select>
            </Field>
            <Field label={t("Qualité")}>
              <Select value={quality} onChange={(e) => setQuality(e.target.value as "" | Project["quality"])}>
                <option value="">{t("Conserver les qualités individuelles")}</option>
                <option value="fast">{t("Rapide")}</option>
                <option value="normal">Normal</option>
                <option value="high">{t("Haute qualité")}</option>
                <option value="maximum">Maximum</option>
              </Select>
            </Field>
            <Field label={t("Instructions communes")} className="span-all">
              <Input
                value={commonInstructions}
                onChange={(event) => setCommonInstructions(event.target.value)}
                placeholder={t("Conserver si vide")}
              />
            </Field>
          </FormGrid>
          <fieldset className="fieldset">
            <legend>{t("Série")}</legend>
            <FormGrid>
              <Field label={t("Série commune")}>
                <Input value={seriesName} onChange={(event) => setSeriesName(event.target.value)} />
              </Field>
              <Field label={t("Premier volume")}>
                <Input
                  type="number"
                  min="1"
                  value={firstVolume}
                  onChange={(event) => setFirstVolume(Number(event.target.value) || 1)}
                />
              </Field>
            </FormGrid>
            <div className="form-actions">
              <Button size="sm" disabled={busy || !seriesName.trim()} onClick={() => void apply("preserve_series")}>
                {t("Appliquer sans renuméroter")}
              </Button>
              <Button size="sm" disabled={busy || !seriesName.trim()} onClick={() => void apply("number_series")}>
                {t("Numéroter par titre")}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={busy || !books.some((book) => book.series_name)}
                onClick={() => void apply("clear_series")}
              >
                {t("Retirer de la série")}
              </Button>
            </div>
          </fieldset>
          <p className="subtle">
            {t(
              "Les livres avancent en parallèle selon les limites du worker et de chaque provider. Les passages d’un même livre gardent leur ordre narratif.",
            )}
          </p>
          {!!results.length && (
            <ul className="batch-results batch-results-dialog">
              {results.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          )}
        </div>
      </Dialog>
    </section>
  );
}
