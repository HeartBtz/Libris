import { useCallback, useEffect, useRef, useState } from "react";
import type { DragEvent } from "react";
import { api, labels, send } from "../api";
import { formatDateTime, formatPercent, registerTranslations, useI18n } from "../i18n";
import type { Project, Run, Series, User } from "../types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Icon,
  IconButton,
  Menu,
  Page,
  PageHeader,
  ProgressBar,
  SearchInput,
  SegmentedControl,
  Select,
  Skeleton,
  StatusPill,
  Table,
  cx,
  useDialogs,
  useMediaQuery,
} from "../ui";
import { BatchActions } from "./BatchActions";
import { BookProgress } from "./BookProgress";
import { ImportWizard } from "./ImportWizard";
import type { WizardStart } from "./ImportWizard";

registerTranslations({
  Bibliothèque: "Library",
  "Ajouter du contenu": "Add content",
  "Filtrer les livres": "Filter books",
  "Tous les livres": "All books",
  "En cours": "In progress",
  "À examiner": "Needs attention",
  "Traduction complète": "Translation complete",
  Archives: "Archives",
  "Rechercher un livre": "Search for a book",
  "Série, titre ou auteur…": "Series, title, or author…",
  "Trier par": "Sort by",
  "Trier par statut": "Sort by status",
  "Dernière activité": "Recent activity",
  Titre: "Title",
  "{count} livre affiché": "{count} book shown",
  "{count} livres affichés": "{count} books shown",
  "{count} série affichée": "{count} series shown",
  "{count} séries affichées": "{count} series shown",
  "Sélectionner les livres affichés": "Select displayed books",
  "Sélectionner tous les livres actifs": "Select all active books",
  "Livre / auteur": "Book / author",
  Langues: "Languages",
  Avancement: "Progress",
  Statut: "Status",
  Modèle: "Model",
  Modifié: "Modified",
  "Sélectionner {title}": "Select {title}",
  "Auteur non renseigné": "Unknown author",
  "volume {volume}": "volume {volume}",
  Archivé: "Archived",
  Ouvrir: "Open",
  Restaurer: "Restore",
  Archiver: "Archive",
  Supprimer: "Delete",
  "Actions pour {title}": "Actions for {title}",
  "Supprimer {title} ?": "Delete {title}?",
  "Le projet local, ses traductions et ses travaux seront supprimés définitivement. La mémoire OpenViking distante reste séparée.":
    "The local project, its translations and its jobs will be permanently deleted. Remote OpenViking memory remains separate.",
  "Supprimer définitivement": "Delete permanently",
  "Aucun livre ne correspond.": "No books match.",
  "Essayez un autre titre ou affichez tous vos livres.": "Try another title or show all your books.",
  "Effacer les filtres": "Clear filters",
  "Votre premier livre commence ici.": "Your first book starts here.",
  "Ajoutez des EPUB ou les chapitres TXT d’une webnovel : l’assistant les examine avant de créer quoi que ce soit.":
    "Add EPUBs or the TXT chapters of a webnovel: the assistant examines them before creating anything.",
  "Glissez-déposez des fichiers ici ou utilisez le bouton Ajouter du contenu.":
    "Drag and drop files here or use the Add content button.",
  "EPUB 2 et 3 · chapitres TXT · archives Libris · API JSON d’automatisation":
    "EPUB 2 and 3 · TXT chapters · Libris archives · JSON automation API",
  "{count} série": "{count} series",
  "{count} séries": "{count} series",
  "{count} volume unique": "{count} standalone volume",
  "{count} volumes uniques": "{count} standalone volumes",
  "{count} archivé": "{count} archived",
  "{count} archivés": "{count} archived",
  "{count} passage traduit": "{count} passage translated",
  "{count} passages traduits": "{count} passages translated",
  "Chargement de la bibliothèque…": "Loading library…",
  Affichage: "View",
  Tableau: "Table",
  Cartes: "Cards",
  "Déposez vos fichiers pour les ajouter": "Drop your files to add them",
  Séries: "Series",
  "Volumes uniques": "Standalone volumes",
  "Des livres sans série. Rattachez-les depuis la page d’une série.":
    "Books without a series. Attach them from a series page.",
  Partagée: "Shared",
  Webnovel: "Webnovel",
  Livres: "Books",
  "{count} volume": "{count} volume",
  "{count} volumes": "{count} volumes",
  "{count} chapitre": "{count} chapter",
  "{count} chapitres": "{count} chapters",
  "flux continu": "continuous flow",
  "Traduction de {name}": "Translation of {name}",
  "{percent} traduit · {validated}/{total} validés": "{percent} translated · {validated}/{total} validated",
  "{count} en cours": "{count} running",
  "{count} à vérifier": "{count} to review",
  "{count} erreur": "{count} error",
  "{count} erreurs": "{count} errors",
  "{count} chapitre à revoir": "{count} chapter to recheck",
  "{count} chapitres à revoir": "{count} chapters to recheck",
  "Volumes manquants : {volumes}": "Missing volumes: {volumes}",
  "Volumes en double : {volumes}": "Duplicate volumes: {volumes}",
  "Mémoire : {backends}": "Memory: {backends}",
  "{count} en attente": "{count} pending",
  "{count} en échec": "{count} failed",
  "Aucun provider": "No provider",
  "Activité : {date}": "Activity: {date}",
});

type Filter = "all" | "active" | "attention" | "complete" | "archived";

const ACTIVE = ["pending", "analyzing", "translating", "reviewing", "syncing"];

function useLibraryPreference<T extends string>(key: string, fallback: T) {
  const [value, setValue] = useState<T>(() => (localStorage.getItem(key) as T) || fallback);
  useEffect(() => localStorage.setItem(key, value), [key, value]);
  return [value, setValue] as const;
}

const seriesAttention = (s: Series) =>
  !!(s.issues.flagged || s.issues.errors || s.issues.context_stale || s.memory.failed) ||
  s.missing_volumes.length > 0 ||
  s.duplicate_volumes.length > 0;
const seriesComplete = (s: Series) => s.progress.total > 0 && s.progress.translated === s.progress.total;
export const seriesStatus = (s: Series) =>
  s.archived_at
    ? "archived"
    : s.progress.running
      ? "running"
      : seriesAttention(s)
        ? "check"
        : seriesComplete(s)
          ? "completed"
          : "ready";

export function Library({ run, user }: { run: Run; user: User }) {
  const { locale, t, tp } = useI18n();
  const { confirm } = useDialogs();
  const [books, setBooks] = useState<Project[] | null>(null);
  const [series, setSeries] = useState<Series[] | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState("recent");
  const [view, setView] = useLibraryPreference<"table" | "cards">("library-view", "table");
  const [dragging, setDragging] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [wizard, setWizard] = useState<WizardStart | null>(null);
  const dragDepth = useRef(0);
  const narrow = useMediaQuery("(max-width: 720px)");
  const layout = narrow ? "cards" : view;
  const all = books || [];
  const known = new Set((series || []).map((s) => s.id));
  // A volume whose series is not visible (not yet listed, or a series of another owner) stays reachable.
  const standalone = all.filter((p) => !p.series_id || !known.has(p.series_id));
  const active = (p: Project) => ACTIVE.includes(p.status);
  const attention = (p: Project) =>
    !!(p.stats.flagged || p.stats.errors || p.stats.refused) || ["failed", "blocked", "waiting"].includes(p.status);
  const complete = (p: Project) => p.stats.total > 0 && p.stats.translated === p.stats.total;
  const normalize = (value: string) =>
    value
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLocaleLowerCase(locale);
  const compareText = (a: string, b: string) => a.localeCompare(b, locale, { numeric: true, sensitivity: "base" });
  const compareOptionalText = (a?: string | null, b?: string | null) => (a ? (b ? compareText(a, b) : -1) : b ? 1 : 0);
  const matches = (p: Project) => normalize(`${p.title} ${p.author} ${p.series_name}`).includes(normalize(query));
  const availableBooks = standalone.filter((project) => !project.archived_at);
  const archivedBooks = standalone.filter((project) => !!project.archived_at);
  const availableSeries = (series || []).filter((s) => !s.archived_at);
  const archivedSeries = (series || []).filter((s) => !!s.archived_at);
  const visibleBooks = standalone
    .filter(
      (p) =>
        matches(p) &&
        ((filter === "archived" && !!p.archived_at) ||
          (!p.archived_at &&
            (filter === "all" ||
              (filter === "active" && active(p)) ||
              (filter === "attention" && attention(p)) ||
              (filter === "complete" && complete(p))))),
    )
    .sort((a, b) =>
      sort === "title"
        ? compareText(a.title, b.title)
        : sort === "status"
          ? compareText(labels[a.archived_at ? "archived" : a.status], labels[b.archived_at ? "archived" : b.status]) ||
            compareText(a.title, b.title)
          : sort === "model"
            ? compareOptionalText(a.progress?.model, b.progress?.model) || compareText(a.title, b.title)
            : b.updated_at - a.updated_at,
    );
  const seriesMatches = (s: Series) =>
    normalize(`${s.name} ${s.authors.join(" ")}`).includes(normalize(query)) ||
    all.some((p) => p.series_id === s.id && matches(p));
  const visibleSeries = (series || [])
    .filter(
      (s) =>
        seriesMatches(s) &&
        ((filter === "archived" && !!s.archived_at) ||
          (!s.archived_at &&
            (filter === "all" ||
              (filter === "active" && s.progress.running > 0) ||
              (filter === "attention" && seriesAttention(s)) ||
              (filter === "complete" && seriesComplete(s))))),
    )
    .sort((a, b) =>
      sort === "title"
        ? compareText(a.name, b.name)
        : sort === "status"
          ? compareText(labels[seriesStatus(a)], labels[seriesStatus(b)]) || compareText(a.name, b.name)
          : sort === "model"
            ? compareOptionalText(a.providers[0]?.model, b.providers[0]?.model) || compareText(a.name, b.name)
            : b.activity - a.activity,
    );
  const selectableBooks = visibleBooks.filter((project) => !project.archived_at);
  const load = useCallback(async () => {
    const [projects, list] = await Promise.all([
      api<Project[]>("/projects?include_archived=true"),
      api<Series[]>("/series?include_archived=true"),
    ]);
    setBooks(projects);
    setSeries(list);
  }, []);
  useEffect(() => {
    void run.background(load);
    const timer = setInterval(() => {
      void run.background(load);
    }, 5000);
    return () => clearInterval(timer);
  }, [run, load]);
  const unselect = (id: string) =>
    setSelected((previous) => {
      const next = new Set(previous);
      next.delete(id);
      return next;
    });
  async function archive(p: Project) {
    await run(async () => {
      await send(`/projects/${p.id}/${p.archived_at ? "restore" : "archive"}`);
      unselect(p.id);
      await load();
    });
  }
  async function remove(p: Project) {
    const accepted = await confirm({
      title: t("Supprimer {title} ?", { title: p.title }),
      message: t(
        "Le projet local, ses traductions et ses travaux seront supprimés définitivement. La mémoire OpenViking distante reste séparée.",
      ),
      confirmLabel: t("Supprimer définitivement"),
      tone: "danger",
    });
    if (!accepted) return;
    await run(async () => {
      await api(`/projects/${p.id}?stop_jobs=true`, { method: "DELETE" });
      unselect(p.id);
      await load();
    });
  }
  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    if (wizard) return;
    const files = Array.from(event.dataTransfer.files);
    if (files.length) setWizard({ files });
  };
  const toggle = (p: Project, checked: boolean) =>
    setSelected((previous) => {
      const next = new Set(previous);
      if (checked) next.add(p.id);
      else next.delete(p.id);
      return next;
    });
  const bookActions = (p: Project) => (
    <Menu
      label={t("Actions pour {title}", { title: p.title })}
      trigger={(props) => (
        <IconButton {...props} icon="more" size="sm" label={t("Actions pour {title}", { title: p.title })} tooltip={false} />
      )}
      items={[
        { label: t("Ouvrir"), icon: "arrowRight", href: `#project/${p.id}` },
        ...(p.owner_id === user.id
          ? [
              {
                label: p.archived_at ? t("Restaurer") : t("Archiver"),
                icon: "archive" as const,
                onSelect: () => void archive(p),
              },
              ...(p.archived_at
                ? [
                    { kind: "separator" as const },
                    {
                      label: t("Supprimer"),
                      icon: "trash" as const,
                      danger: true,
                      onSelect: () => void remove(p),
                    },
                  ]
                : []),
            ]
          : []),
      ]}
    />
  );
  const translatedTotal = all.filter((p) => !p.archived_at).reduce((n, p) => n + p.stats.translated, 0);
  const archivedCount = archivedBooks.length + archivedSeries.length;
  const summary = [
    tp(availableSeries.length, "{count} série", "{count} séries"),
    tp(availableBooks.length, "{count} volume unique", "{count} volumes uniques"),
    archivedCount ? tp(archivedCount, "{count} archivé", "{count} archivés") : "",
    tp(translatedTotal, "{count} passage traduit", "{count} passages traduits"),
  ]
    .filter(Boolean)
    .join(" · ");
  const count = (bookTest: (p: Project) => boolean, seriesTest: (s: Series) => boolean) =>
    availableBooks.filter(bookTest).length + availableSeries.filter(seriesTest).length;
  const filters: { value: Filter; label: string; count: number }[] = [
    { value: "all", label: t("Tous les livres"), count: availableBooks.length + availableSeries.length },
    { value: "active", label: t("En cours"), count: count(active, (s) => s.progress.running > 0) },
    { value: "attention", label: t("À examiner"), count: count(attention, seriesAttention) },
    { value: "complete", label: t("Traduction complète"), count: count(complete, seriesComplete) },
    { value: "archived", label: t("Archives"), count: archivedCount },
  ];
  const loading = books === null || series === null;
  const empty = !loading && !all.length && !(series || []).length;
  return (
    <Page className={cx("library", dragging && "is-dragging")}>
      <div
        className="library-dropzone"
        onDragEnter={(event) => {
          if (!event.dataTransfer.types.includes("Files") || wizard) return;
          dragDepth.current += 1;
          setDragging(true);
        }}
        onDragOver={(event) => {
          if (event.dataTransfer.types.includes("Files") && !wizard) event.preventDefault();
        }}
        onDragLeave={() => {
          dragDepth.current = Math.max(0, dragDepth.current - 1);
          if (!dragDepth.current) setDragging(false);
        }}
        onDrop={onDrop}
      >
        <PageHeader
          title={t("Bibliothèque")}
          description={loading ? <Skeleton width={220} height={14} /> : summary}
          actions={
            <Button variant="primary" icon="plus" onClick={() => setWizard({})}>
              {t("Ajouter du contenu")}
            </Button>
          }
        />
        <div className="library-toolbar">
          <div className="library-toolbar-row">
            <SegmentedControl
              label={t("Filtrer les livres")}
              value={filter}
              onChange={setFilter}
              options={filters.map((item) => ({ value: item.value, label: item.label, count: item.count }))}
            />
            {!narrow && (
              <SegmentedControl
                label={t("Affichage")}
                value={view}
                onChange={setView}
                options={[
                  { value: "table", label: "", icon: "list", ariaLabel: t("Tableau") },
                  { value: "cards", label: "", icon: "grid", ariaLabel: t("Cartes") },
                ]}
              />
            )}
          </div>
          <div className="library-filters">
            <label className="library-search">
              <span className="sr-only">{t("Rechercher un livre")}</span>
              <SearchInput
                placeholder={t("Série, titre ou auteur…")}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <label className="toolbar-select">
              <span className="sr-only">{t("Trier par")}</span>
              <Select value={sort} onChange={(e) => setSort(e.target.value)}>
                <option value="recent">{t("Dernière activité")}</option>
                <option value="title">{t("Titre")}</option>
                <option value="status">{t("Statut")}</option>
                <option value="model">{t("Modèle")}</option>
              </Select>
            </label>
          </div>
        </div>
        <p className="sr-only" role="status">
          {tp(visibleSeries.length, "{count} série affichée", "{count} séries affichées")} ·{" "}
          {tp(visibleBooks.length, "{count} livre affiché", "{count} livres affichés")}
        </p>
        {loading ? (
          <LibrarySkeleton label={t("Chargement de la bibliothèque…")} />
        ) : empty ? (
          <div className="library-empty">
            <EmptyState
              icon="upload"
              title={t("Votre premier livre commence ici.")}
              description={
                <>
                  {t(
                    "Ajoutez des EPUB ou les chapitres TXT d’une webnovel : l’assistant les examine avant de créer quoi que ce soit.",
                  )}{" "}
                  {t("Glissez-déposez des fichiers ici ou utilisez le bouton Ajouter du contenu.")}
                </>
              }
              action={
                <Button variant="primary" icon="plus" onClick={() => setWizard({})}>
                  {t("Ajouter du contenu")}
                </Button>
              }
            />
            <p className="subtle">{t("EPUB 2 et 3 · chapitres TXT · archives Libris · API JSON d’automatisation")}</p>
          </div>
        ) : !visibleBooks.length && !visibleSeries.length ? (
          <Card>
            <EmptyState
              icon="search"
              title={t("Aucun livre ne correspond.")}
              description={t("Essayez un autre titre ou affichez tous vos livres.")}
              action={
                <Button
                  onClick={() => {
                    setQuery("");
                    setFilter("all");
                  }}
                >
                  {t("Effacer les filtres")}
                </Button>
              }
            />
          </Card>
        ) : (
          <>
            {visibleSeries.length > 0 && (
              <section className="library-section" aria-labelledby="library-series-title">
                <h2 id="library-series-title" className="section-title">
                  {t("Séries")} <span className="section-count tabular">{visibleSeries.length}</span>
                </h2>
                <ul className={cx("series-grid", layout === "table" && "is-dense")}>
                  {visibleSeries.map((s) => (
                    <SeriesCard key={s.id} series={s} />
                  ))}
                </ul>
              </section>
            )}
            {visibleBooks.length > 0 && (
              <section className="library-section" aria-labelledby="library-standalone-title">
                <div className="section-heading">
                  <h2 id="library-standalone-title" className="section-title">
                    {t("Volumes uniques")} <span className="section-count tabular">{visibleBooks.length}</span>
                  </h2>
                  <p className="subtle">{t("Des livres sans série. Rattachez-les depuis la page d’une série.")}</p>
                </div>
                {layout === "table" ? (
                  <Card padded={false} className="library-table-card">
                    <Table className="library-table">
                      <thead>
                        <tr>
                          <th className="cell-tight">
                            <input
                              type="checkbox"
                              aria-label={
                                query || filter !== "all"
                                  ? t("Sélectionner les livres affichés")
                                  : t("Sélectionner tous les livres actifs")
                              }
                              checked={!!selectableBooks.length && selectableBooks.every((p) => selected.has(p.id))}
                              onChange={(e) =>
                                setSelected(e.target.checked ? new Set(selectableBooks.map((p) => p.id)) : new Set())
                              }
                            />
                          </th>
                          <th>{t("Livre / auteur")}</th>
                          <th className="col-languages">{t("Langues")}</th>
                          <th className="col-progress">{t("Avancement")}</th>
                          <th aria-sort={sort === "status" ? "ascending" : "none"}>
                            <button
                              className="table-sort"
                              type="button"
                              aria-label={t("Trier par statut")}
                              aria-pressed={sort === "status"}
                              onClick={() => setSort("status")}
                            >
                              {t("Statut")}
                              <Icon name="chevronDown" size={12} />
                            </button>
                          </th>
                          <th className="col-model">{t("Modèle")}</th>
                          <th className="col-modified">{t("Modifié")}</th>
                          <th className="cell-tight">
                            <span className="sr-only">{t("Actions pour {title}", { title: "" })}</span>
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {visibleBooks.map((p) => (
                          <tr key={p.id} className={p.archived_at ? "is-archived" : undefined}>
                            <td className="cell-tight">
                              <input
                                type="checkbox"
                                aria-label={t("Sélectionner {title}", { title: p.title })}
                                disabled={!!p.archived_at}
                                checked={selected.has(p.id)}
                                onChange={(e) => toggle(p, e.target.checked)}
                              />
                            </td>
                            <td>
                              <BookTitle project={p} />
                            </td>
                            <td className="col-languages">
                              <Languages project={p} />
                            </td>
                            <td className="col-progress">
                              <BookProgress project={p} compact />
                            </td>
                            <td>
                              <BookStatus project={p} />
                            </td>
                            <td className="col-model muted">{p.progress?.model || "—"}</td>
                            <td className="col-modified muted tabular">
                              {formatDateTime(p.updated_at, { dateStyle: "medium" })}
                            </td>
                            <td className="cell-tight">{bookActions(p)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </Table>
                  </Card>
                ) : (
                  <ul className="book-grid">
                    {visibleBooks.map((p) => (
                      <li
                        key={p.id}
                        className={cx("book-card", !!p.archived_at && "is-archived", selected.has(p.id) && "is-selected")}
                      >
                        <div className="book-card-top">
                          <input
                            type="checkbox"
                            aria-label={t("Sélectionner {title}", { title: p.title })}
                            disabled={!!p.archived_at}
                            checked={selected.has(p.id)}
                            onChange={(e) => toggle(p, e.target.checked)}
                          />
                          <BookStatus project={p} />
                          <span className="grow" />
                          {bookActions(p)}
                        </div>
                        <BookTitle project={p} />
                        <BookProgress project={p} compact />
                        <div className="book-card-footer">
                          <Languages project={p} />
                          <span className="subtle tabular">{formatDateTime(p.updated_at, { dateStyle: "medium" })}</span>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            )}
          </>
        )}
        {selected.size > 0 && (
          <BatchActions
            books={all.filter((p) => selected.has(p.id))}
            run={run}
            refresh={load}
            onClear={() => setSelected(new Set())}
            onDeleted={(ids) =>
              setSelected((previous) => {
                const next = new Set(previous);
                ids.forEach((id) => next.delete(id));
                return next;
              })
            }
          />
        )}
        {dragging && (
          <div className="drop-overlay" aria-hidden="true">
            <Icon name="upload" size={28} />
            <span>{t("Déposez vos fichiers pour les ajouter")}</span>
          </div>
        )}
      </div>
      {wizard && (
        <ImportWizard
          run={run}
          start={wizard}
          admin={user.admin}
          onClose={() => setWizard(null)}
          onImported={() => void run.background(load)}
        />
      )}
    </Page>
  );
}

export function SeriesCard({ series }: { series: Series }) {
  const { t, tp } = useI18n();
  const s = series;
  const counts = [
    tp(s.volumes, "{count} volume", "{count} volumes"),
    tp(s.chapters, "{count} chapitre", "{count} chapitres"),
    s.serial ? t("flux continu") : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const provider = s.providers.map((p) => `${p.name} · ${p.model}`).join(", ");
  return (
    <li className={cx("series-card", !!s.archived_at && "is-archived")}>
      <div className="series-card-head">
        <div className="series-card-heading">
          <a className="series-card-title" href={`#series/${s.id}`}>
            {s.name}
          </a>
          <span className="book-meta">
            {s.authors.length ? s.authors.join(", ") : t("Auteur non renseigné")} · {counts}
          </span>
        </div>
        <div className="series-card-badges">
          <Badge tone={s.kind === "webnovel" ? "info" : "neutral"}>{s.kind === "webnovel" ? t("Webnovel") : t("Livres")}</Badge>
          {s.formats.map((format) => (
            <Badge key={format}>{format.toUpperCase()}</Badge>
          ))}
          {s.shared && <Badge tone="accent">{t("Partagée")}</Badge>}
          {s.archived_at ? <Badge>{t("Archivé")}</Badge> : <StatusPill status={seriesStatus(s)} />}
        </div>
      </div>
      <div className="series-card-progress">
        <div className="book-progress-label">
          <span>
            {t("{percent} traduit · {validated}/{total} validés", {
              percent: formatPercent(s.progress.percent),
              validated: s.progress.validated,
              total: s.progress.total,
            })}
          </span>
          {s.progress.running > 0 && <strong>{t("{count} en cours", { count: s.progress.running })}</strong>}
        </div>
        <ProgressBar
          size="sm"
          value={s.progress.percent}
          tone={seriesComplete(s) ? "success" : "accent"}
          label={t("Traduction de {name}", { name: s.name })}
        />
      </div>
      {(seriesAttention(s) || s.memory.pending > 0) && (
        <div className="series-card-issues">
          {s.issues.flagged > 0 && <Badge tone="warning">{t("{count} à vérifier", { count: s.issues.flagged })}</Badge>}
          {s.issues.errors > 0 && (
            <Badge tone="danger">{tp(s.issues.errors, "{count} erreur", "{count} erreurs")}</Badge>
          )}
          {s.issues.context_stale > 0 && (
            <Badge tone="warning">
              {tp(s.issues.context_stale, "{count} chapitre à revoir", "{count} chapitres à revoir")}
            </Badge>
          )}
          {s.missing_volumes.length > 0 && (
            <Badge tone="warning">{t("Volumes manquants : {volumes}", { volumes: s.missing_volumes.join(", ") })}</Badge>
          )}
          {s.duplicate_volumes.length > 0 && (
            <Badge tone="danger">{t("Volumes en double : {volumes}", { volumes: s.duplicate_volumes.join(", ") })}</Badge>
          )}
          {s.memory.pending > 0 && <Badge>{t("{count} en attente", { count: s.memory.pending })}</Badge>}
          {s.memory.failed > 0 && <Badge tone="danger">{t("{count} en échec", { count: s.memory.failed })}</Badge>}
        </div>
      )}
      <div className="series-card-footer">
        <span className="series-card-provider" title={provider}>
          <Icon name="sparkles" size={12} /> {provider || t("Aucun provider")}
        </span>
        {s.memory.backends.length > 0 && (
          <span>{t("Mémoire : {backends}", { backends: s.memory.backends.join(", ") })}</span>
        )}
        <span className="subtle tabular">
          {t("Activité : {date}", { date: formatDateTime(s.activity, { dateStyle: "medium" }) })}
        </span>
      </div>
    </li>
  );
}

export function BookTitle({ project }: { project: Project }) {
  const { t } = useI18n();
  return (
    <div className="book-title-cell">
      <a className="book-title" href={`#project/${project.id}`}>
        {project.title}
      </a>
      <span className="book-meta">
        {project.author || t("Auteur non renseigné")}
        {project.series_name && (
          <>
            {" · "}
            <span className="book-series">
              {project.series_name}
              {project.volume_number && ` · ${t("volume {volume}", { volume: project.volume_number })}`}
            </span>
          </>
        )}
      </span>
    </div>
  );
}

export function Languages({ project }: { project: Project }) {
  return (
    <span className="language-pair">
      <span>{project.source_language.toUpperCase()}</span>
      <Icon name="arrowRight" size={12} />
      <span>{project.target_language.toUpperCase()}</span>
    </span>
  );
}

export function BookStatus({ project }: { project: Project }) {
  const { t } = useI18n();
  return project.archived_at ? <Badge>{t("Archivé")}</Badge> : <StatusPill status={project.status} />;
}

function LibrarySkeleton({ label }: { label: string }) {
  return (
    <Card padded={false} className="library-skeleton" role="status" aria-label={label}>
      {Array.from({ length: 4 }, (_, index) => (
        <div key={index} className="library-skeleton-row">
          <Skeleton width={16} height={16} />
          <div className="stack-sm grow">
            <Skeleton width="38%" height={14} />
            <Skeleton width="22%" height={11} />
          </div>
          <Skeleton width={140} height={8} />
          <Skeleton width={72} height={20} />
        </div>
      ))}
    </Card>
  );
}
