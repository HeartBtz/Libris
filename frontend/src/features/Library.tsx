import { useCallback, useEffect, useRef, useState } from "react";
import type { DragEvent, KeyboardEvent, ReactNode } from "react";
import { api, labels, send } from "../api";
import { formatDateTime, registerTranslations, useI18n } from "../i18n";
import type { Project, Run, User } from "../types";
import {
  Badge,
  Button,
  Callout,
  Card,
  EmptyState,
  Icon,
  IconButton,
  Menu,
  Page,
  PageHeader,
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

registerTranslations({
  "En attente": "Pending",
  "Import / validation…": "Importing / validating…",
  Importé: "Imported",
  Bibliothèque: "Library",
  "Réimporter un projet (.zip)": "Reimport a project (.zip)",
  "Import en cours…": "Importing…",
  "Importer des EPUB": "Import EPUBs",
  "Autres imports": "Other imports",
  "Filtrer les livres": "Filter books",
  "Tous les livres": "All books",
  "En cours": "In progress",
  "À examiner": "Needs attention",
  "Traduction complète": "Translation complete",
  Archives: "Archives",
  "Rechercher un livre": "Search for a book",
  "Titre, auteur ou série…": "Title, author, or series…",
  "Trier par": "Sort by",
  "Trier par statut": "Sort by status",
  "Dernière activité": "Recent activity",
  Titre: "Title",
  "Série et volume": "Series and volume",
  Série: "Series",
  "Toutes les séries": "All series",
  "{count} livre affiché": "{count} book shown",
  "{count} livres affichés": "{count} books shown",
  "Série {series}": "Series {series}",
  Collection: "Collection",
  "{count} volume, classé dans l’ordre de lecture.": "{count} volume, in reading order.",
  "{count} volumes, classés dans l’ordre de lecture.": "{count} volumes, in reading order.",
  "Les conventions acceptées et les décisions humaines des volumes antérieurs alimentent les volumes suivants, sans importer leur narration.":
    "Approved conventions and human decisions from earlier volumes inform later volumes without importing their narrative.",
  "Volumes manquants dans cette bibliothèque : {volumes}.": "Missing volumes in this library: {volumes}.",
  "Numéros dupliqués : {volumes}.": "Duplicate numbers: {volumes}.",
  "Sélectionner toute la série": "Select the entire series",
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
  "Importez un EPUB pour examiner sa structure, préparer sa mémoire et traduire avec continuité.":
    "Import an EPUB to inspect its structure, prepare its memory, and translate consistently.",
  "EPUB 2 et 3 · images et balises préservées · endpoint compatible OpenAI":
    "EPUB 2 and 3 · images and tags preserved · OpenAI-compatible endpoint",
  "{count} projet": "{count} project",
  "{count} projets": "{count} projects",
  "{count} archivé": "{count} archived",
  "{count} archivés": "{count} archived",
  "{count} passage traduit": "{count} passage translated",
  "{count} passages traduits": "{count} passages translated",
  "Chargement de la bibliothèque…": "Loading library…",
  "Affichage": "View",
  Tableau: "Table",
  Cartes: "Cards",
  "Déposez vos fichiers EPUB pour les importer": "Drop your EPUB files to import them",
  "Imports": "Imports",
  "Masquer": "Hide",
  "Glissez-déposez des EPUB ici ou utilisez le bouton d’import.": "Drag and drop EPUBs here or use the import button.",
});

type Filter = "all" | "active" | "attention" | "complete" | "archived";

const ACTIVE = ["pending", "analyzing", "translating", "reviewing", "syncing"];

function useLibraryPreference<T extends string>(key: string, fallback: T) {
  const [value, setValue] = useState<T>(() => (localStorage.getItem(key) as T) || fallback);
  useEffect(() => localStorage.setItem(key, value), [key, value]);
  return [value, setValue] as const;
}

export function Library({ run, user }: { run: Run; user: User }) {
  const { locale, t, tp } = useI18n();
  const { confirm } = useDialogs();
  const [books, setBooks] = useState<Project[] | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState("recent");
  const [seriesFilter, setSeriesFilter] = useState("all");
  const [view, setView] = useLibraryPreference<"table" | "cards">("library-view", "table");
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [imports, setImports] = useState<{ name: string; state: string; tone: "neutral" | "success" | "danger" }[]>([]);
  const dragDepth = useRef(0);
  const narrow = useMediaQuery("(max-width: 720px)");
  const layout = narrow ? "cards" : view;
  const all = books || [];
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
  const availableBooks = all.filter((project) => !project.archived_at);
  const archivedBooks = all.filter((project) => !!project.archived_at);
  const seriesNames = Array.from(new Set(all.map((project) => project.series_name).filter(Boolean))).sort((a, b) =>
    a.localeCompare(b, locale, { numeric: true }),
  );
  const visibleBooks = all
    .filter(
      (p) =>
        normalize(`${p.title} ${p.author} ${p.series_name}`).includes(normalize(query)) &&
        (seriesFilter === "all" || p.series_name === seriesFilter) &&
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
        : sort === "series"
          ? compareText(a.series_name || a.title, b.series_name || b.title) ||
            (a.volume_number ?? Number.MAX_SAFE_INTEGER) - (b.volume_number ?? Number.MAX_SAFE_INTEGER) ||
            compareText(a.title, b.title)
          : sort === "status"
            ? compareText(labels[a.archived_at ? "archived" : a.status], labels[b.archived_at ? "archived" : b.status]) ||
              compareText(a.title, b.title)
            : sort === "model"
              ? compareOptionalText(a.progress?.model, b.progress?.model) || compareText(a.title, b.title)
              : b.updated_at - a.updated_at,
    );
  const selectableBooks = visibleBooks.filter((project) => !project.archived_at);
  const seriesBooks =
    seriesFilter === "all"
      ? []
      : all
          .filter((project) => project.series_name === seriesFilter)
          .sort(
            (a, b) =>
              (a.volume_number ?? Number.MAX_SAFE_INTEGER) - (b.volume_number ?? Number.MAX_SAFE_INTEGER) ||
              a.title.localeCompare(b.title, locale, { numeric: true }),
          );
  const load = useCallback(async () => setBooks(await api<Project[]>("/projects?include_archived=true")), []);
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
  async function upload(files: File[], restore = false) {
    setBusy(true);
    setImports(files.map((f) => ({ name: f.name, state: t("En attente"), tone: "neutral" })));
    let cursor = 0;
    const created: string[] = [];
    const update = (index: number, state: string, tone: "neutral" | "success" | "danger") =>
      setImports((values) => values.map((v, i) => (i === index ? { ...v, state, tone } : v)));
    const workers = Array.from({ length: Math.min(2, files.length) }, async () => {
      while (cursor < files.length) {
        const index = cursor++;
        update(index, t("Import / validation…"), "neutral");
        const form = new FormData();
        form.append("file", files[index]);
        try {
          const p = await api<Project>(restore ? "/projects/import" : "/projects", { method: "POST", body: form });
          created.push(p.id);
          update(index, t("Importé"), "success");
        } catch (e) {
          update(index, e instanceof Error ? e.message : String(e), "danger");
        }
      }
    });
    await Promise.all(workers);
    await run(load);
    setSelected(new Set(created));
    setBusy(false);
    if (files.length === 1 && created.length === 1) location.hash = `project/${created[0]}`;
  }
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
    if (busy) return;
    const files = Array.from(event.dataTransfer.files).filter((file) => file.name.toLowerCase().endsWith(".epub"));
    if (files.length) void upload(files);
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
  const translatedTotal = availableBooks.reduce((n, p) => n + p.stats.translated, 0);
  const summary = [
    tp(availableBooks.length, "{count} projet", "{count} projets"),
    archivedBooks.length ? tp(archivedBooks.length, "{count} archivé", "{count} archivés") : "",
    tp(translatedTotal, "{count} passage traduit", "{count} passages traduits"),
  ]
    .filter(Boolean)
    .join(" · ");
  const filters: { value: Filter; label: string; count: number }[] = [
    { value: "all", label: t("Tous les livres"), count: availableBooks.length },
    { value: "active", label: t("En cours"), count: availableBooks.filter(active).length },
    { value: "attention", label: t("À examiner"), count: availableBooks.filter(attention).length },
    { value: "complete", label: t("Traduction complète"), count: availableBooks.filter(complete).length },
    { value: "archived", label: t("Archives"), count: archivedBooks.length },
  ];
  return (
    <Page className={cx("library", dragging && "is-dragging")}>
      <div
        className="library-dropzone"
        onDragEnter={(event) => {
          if (!event.dataTransfer.types.includes("Files")) return;
          dragDepth.current += 1;
          setDragging(true);
        }}
        onDragOver={(event) => {
          if (event.dataTransfer.types.includes("Files")) event.preventDefault();
        }}
        onDragLeave={() => {
          dragDepth.current = Math.max(0, dragDepth.current - 1);
          if (!dragDepth.current) setDragging(false);
        }}
        onDrop={onDrop}
      >
        <PageHeader
          title={t("Bibliothèque")}
          description={books ? summary : <Skeleton width={220} height={14} />}
          actions={
            <>
              <FileButton
                label={busy ? t("Import en cours…") : t("Importer des EPUB")}
                accept=".epub"
                multiple
                disabled={busy}
                primary
                inputId="epub-input"
                onFiles={(files) => void upload(files)}
              />
              <Menu
                label={t("Autres imports")}
                trigger={(props) => (
                  <IconButton {...props} icon="more" variant="secondary" label={t("Autres imports")} />
                )}
                items={[
                  {
                    label: t("Réimporter un projet (.zip)"),
                    icon: "upload",
                    disabled: busy,
                    onSelect: () => document.getElementById("restore-input")?.click(),
                  },
                ]}
              />
              <input
                id="restore-input"
                type="file"
                accept=".zip"
                hidden
                disabled={busy}
                onChange={(e) => {
                  const files = Array.from(e.target.files || []);
                  e.target.value = ""; // Let the same file be chosen again after a failed import.
                  if (files[0]) void upload([files[0]], true);
                }}
              />
            </>
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
            {!narrow && <SegmentedControl
              label={t("Affichage")}
              value={view}
              onChange={setView}
              options={[
                { value: "table", label: "", icon: "list", ariaLabel: t("Tableau") },
                { value: "cards", label: "", icon: "grid", ariaLabel: t("Cartes") },
              ]}
            />}
          </div>
          <div className="library-filters">
            <label className="library-search">
              <span className="sr-only">{t("Rechercher un livre")}</span>
              <SearchInput
                placeholder={t("Titre, auteur ou série…")}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <label className="toolbar-select">
              <span className="sr-only">{t("Série")}</span>
              <Select value={seriesFilter} onChange={(e) => setSeriesFilter(e.target.value)}>
                <option value="all">{t("Toutes les séries")}</option>
                {seriesNames.map((series) => (
                  <option key={series} value={series}>
                    {series}
                  </option>
                ))}
              </Select>
            </label>
            <label className="toolbar-select">
              <span className="sr-only">{t("Trier par")}</span>
              <Select value={sort} onChange={(e) => setSort(e.target.value)}>
                <option value="recent">{t("Dernière activité")}</option>
                <option value="title">{t("Titre")}</option>
                <option value="series">{t("Série et volume")}</option>
                <option value="status">{t("Statut")}</option>
                <option value="model">{t("Modèle")}</option>
              </Select>
            </label>
          </div>
        </div>
        <p className="sr-only" role="status">
          {tp(visibleBooks.length, "{count} livre affiché", "{count} livres affichés")}
        </p>
        {!!imports.length && (
          <Card
            className="import-progress"
            title={t("Imports")}
            actions={
              !busy && (
                <Button size="sm" variant="ghost" onClick={() => setImports([])}>
                  {t("Masquer")}
                </Button>
              )
            }
          >
            <ul role="status" className="import-list">
              {imports.map((item, i) => (
                <li key={i}>
                  <Icon
                    name={item.tone === "success" ? "check" : item.tone === "danger" ? "alert" : "upload"}
                    className={`tone-text-${item.tone}`}
                  />
                  <span className="import-name">{item.name}</span>
                  <span className={cx("import-state", `tone-text-${item.tone}`)}>{item.state}</span>
                </li>
              ))}
            </ul>
          </Card>
        )}
        {!!seriesBooks.length && (
          <SeriesOverview
            series={seriesFilter}
            books={seriesBooks}
            onSelectAll={() =>
              setSelected(new Set(seriesBooks.filter((project) => !project.archived_at).map((project) => project.id)))
            }
          />
        )}
        {books === null ? (
          <LibrarySkeleton label={t("Chargement de la bibliothèque…")} />
        ) : !all.length ? (
          <div className="library-empty">
            <EmptyState
              icon="upload"
              title={t("Votre premier livre commence ici.")}
              description={
                <>
                  {t(
                    "Importez un EPUB pour examiner sa structure, préparer sa mémoire et traduire avec continuité.",
                  )}{" "}
                  {t("Glissez-déposez des EPUB ici ou utilisez le bouton d’import.")}
                </>
              }
              action={
                <Button
                  variant="primary"
                  icon="upload"
                  disabled={busy}
                  onClick={() => document.getElementById("epub-input")?.click()}
                >
                  {t("Importer des EPUB")}
                </Button>
              }
            />
            <p className="subtle">{t("EPUB 2 et 3 · images et balises préservées · endpoint compatible OpenAI")}</p>
          </div>
        ) : !visibleBooks.length ? (
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
                    setSeriesFilter("all");
                  }}
                >
                  {t("Effacer les filtres")}
                </Button>
              }
            />
          </Card>
        ) : layout === "table" ? (
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
              <li key={p.id} className={cx("book-card", !!p.archived_at && "is-archived", selected.has(p.id) && "is-selected")}>
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
            scopeLabel={seriesFilter !== "all" ? t("Série {series}", { series: seriesFilter }) : undefined}
          />
        )}
        {dragging && (
          <div className="drop-overlay" aria-hidden="true">
            <Icon name="upload" size={28} />
            <span>{t("Déposez vos fichiers EPUB pour les importer")}</span>
          </div>
        )}
      </div>
    </Page>
  );
}

function BookTitle({ project }: { project: Project }) {
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

function Languages({ project }: { project: Project }) {
  return (
    <span className="language-pair">
      <span>{project.source_language.toUpperCase()}</span>
      <Icon name="arrowRight" size={12} />
      <span>{project.target_language.toUpperCase()}</span>
    </span>
  );
}

function BookStatus({ project }: { project: Project }) {
  const { t } = useI18n();
  return project.archived_at ? <Badge>{t("Archivé")}</Badge> : <StatusPill status={project.status} />;
}

function SeriesOverview({
  series,
  books,
  onSelectAll,
}: {
  series: string;
  books: Project[];
  onSelectAll: () => void;
}) {
  const { t, tp } = useI18n();
  const numbered = books.map((project) => project.volume_number).filter((value): value is number => value !== null);
  const duplicates = Array.from(new Set(numbered.filter((volume, index) => numbered.indexOf(volume) !== index)));
  const missing = numbered.length
    ? Array.from({ length: Math.max(...numbered) }, (_, index) => index + 1).filter((volume) => !numbered.includes(volume))
    : [];
  return (
    <Card
      className="series-overview"
      aria-label={t("Série {series}", { series })}
      title={
        <span className="series-title">
          <span className="subtle">{t("Collection")}</span>
          {series}
        </span>
      }
      description={
        <>
          {tp(books.length, "{count} volume, classé dans l’ordre de lecture.", "{count} volumes, classés dans l’ordre de lecture.")}{" "}
          {t(
            "Les conventions acceptées et les décisions humaines des volumes antérieurs alimentent les volumes suivants, sans importer leur narration.",
          )}
        </>
      }
      actions={
        <Button size="sm" onClick={onSelectAll} disabled={!books.some((project) => !project.archived_at)}>
          {t("Sélectionner toute la série")}
        </Button>
      }
    >
      <ol className="series-volumes">
        {books.map((project) => (
          <li key={project.id}>
            <span className="series-number tabular">{project.volume_number ?? "?"}</span>
            <a href={`#project/${project.id}`}>{project.title}</a>
            {project.archived_at && <Badge>{t("Archivé")}</Badge>}
          </li>
        ))}
      </ol>
      {(missing.length > 0 || duplicates.length > 0) && (
        <Callout tone="warning" role="status">
          {missing.length > 0 && t("Volumes manquants dans cette bibliothèque : {volumes}.", { volumes: missing.join(", ") })}{" "}
          {duplicates.length > 0 && t("Numéros dupliqués : {volumes}.", { volumes: duplicates.join(", ") })}
        </Callout>
      )}
    </Card>
  );
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

/** A label styled as a button so the native file picker stays keyboard-accessible. */
export function FileButton({
  label,
  accept,
  multiple = false,
  disabled = false,
  primary = false,
  icon = "upload",
  onFiles,
  inputId,
}: {
  label: ReactNode;
  accept: string;
  multiple?: boolean;
  disabled?: boolean;
  primary?: boolean;
  icon?: "upload" | "plus";
  onFiles: (files: File[]) => void;
  inputId?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <label
      className={cx("btn", "btn-md", primary ? "btn-primary" : "btn-secondary")}
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-disabled={disabled}
      onKeyDown={(e: KeyboardEvent<HTMLLabelElement>) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          if (!disabled) input.current?.click();
        }
      }}
    >
      <Icon name={icon} />
      <span className="btn-label">{label}</span>
      <input
        ref={input}
        type="file"
        accept={accept}
        multiple={multiple}
        hidden
        disabled={disabled}
        id={inputId}
        onChange={(e) => {
          const files = Array.from(e.target.files || []);
          e.target.value = ""; // Let the same files be chosen again after a failed import.
          if (files.length) onFiles(files);
        }}
      />
    </label>
  );
}
