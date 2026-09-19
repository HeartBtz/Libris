import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, ApiError, send } from "../api";
import { formatNumber, formatPercent, registerTranslations, useI18n } from "../i18n";
import type { Project, Run, SeriesChapter, SeriesDetail, User } from "../types";
import {
  Badge,
  Button,
  Callout,
  Card,
  EmptyState,
  Icon,
  IconButton,
  Input,
  LoadingBlock,
  Menu,
  Page,
  PageHeader,
  Select,
  Stat,
  StatusPill,
  TabPanel,
  Tabs,
  cx,
  useDialogs,
  useToast,
} from "../ui";
import { BatchActions } from "./BatchActions";
import { BookProgress } from "./BookProgress";
import { ImportWizard } from "./ImportWizard";
import { seriesStatus } from "./Library";
import {
  SeriesBible,
  SeriesGlossary,
  SeriesIdentities,
  SeriesManagement,
  SeriesMemory,
  SeriesRelations,
  SeriesSettings,
} from "./seriesPanels";

registerTranslations({
  Bibliothèque: "Library",
  "Ouverture de la série…": "Opening series…",
  "Série introuvable.": "Series not found.",
  "Elle a peut-être été supprimée, ou vous n’y avez plus accès.": "It may have been deleted, or you no longer have access.",
  "Retour à la bibliothèque": "Back to the library",
  "Navigation de la série": "Series navigation",
  "Tableau de bord": "Dashboard",
  Volumes: "Volumes",
  Chapitres: "Chapters",
  Importer: "Import",
  "Series Bible": "Series Bible",
  Glossaire: "Glossary",
  Identités: "Identities",
  Relations: "Relations",
  Mémoire: "Memory",
  "Paramètres par défaut": "Defaults",
  Gestion: "Management",
  "Ajouter du contenu": "Add content",
  Webnovel: "Webnovel",
  Livres: "Books",
  Partagée: "Shared",
  Archivé: "Archived",
  "{count} volume": "{count} volume",
  "{count} volumes": "{count} volumes",
  "{count} chapitre": "{count} chapter",
  "{count} chapitres": "{count} chapters",
  "Série partagée avec vous : lecture seule, seuls les volumes partagés sont visibles.":
    "Series shared with you: read-only, only the shared volumes are visible.",
  "Cette série est archivée.": "This series is archived.",
  Traduits: "Translated",
  Validés: "Validated",
  "Travaux en cours": "Running jobs",
  "Relecture facultative": "Optional review",
  Erreurs: "Errors",
  "Prochaines actions": "Next actions",
  "Rien à signaler : chaque volume a un provider et aucune alerte n’attend.":
    "Nothing to report: every volume has a provider and no alert is waiting.",
  "Ajoutez un premier volume ou des chapitres.": "Add a first volume or chapters.",
  "Aucun provider pour « {title} ».": "No provider for “{title}”.",
  "« {title} » n’est pas encore analysé.": "“{title}” is not analyzed yet.",
  "{count} passage ouvert à une relecture facultative dans « {title} ».":
    "{count} passage open to optional review in “{title}”.",
  "{count} passages ouverts à une relecture facultative dans « {title} ».":
    "{count} passages open to optional review in “{title}”.",
  "Volumes manquants : {volumes}.": "Missing volumes: {volumes}.",
  "Numéros de volume en double : {volumes}.": "Duplicate volume numbers: {volumes}.",
  "{count} envoi de mémoire en échec.": "{count} memory upload failed.",
  "{count} envois de mémoire en échec.": "{count} memory uploads failed.",
  "Ouvrir le volume": "Open the volume",
  "Voir les volumes": "See the volumes",
  "Voir la mémoire": "See the memory",
  "Chapitres à revoir": "Chapters to recheck",
  "Le contexte de ces chapitres a changé depuis leur traduction (volume antérieur, glossaire ou identité modifiés).":
    "The context of these chapters changed since their translation (earlier volume, glossary or identity changed).",
  "Marquer comme vérifié": "Mark as checked",
  "Relire le chapitre": "Review the chapter",
  "Relire « {title} » ?": "Review “{title}”?",
  "L’IA relit les passages traduits de ce chapitre avec le contexte à jour. Cette opération appelle le modèle.":
    "The AI reviews this chapter's translated passages with the current context. This operation calls the model.",
  Lancer: "Start",
  "Relecture lancée pour « {title} ».": "Review started for “{title}”.",
  "« {title} » marqué comme vérifié.": "“{title}” marked as checked.",
  "Ordre de lecture": "Reading order",
  "Les numéros fixent l’ordre de lecture : un volume ne reçoit la mémoire que des volumes précédents.":
    "Numbers set the reading order: a volume only receives the memory of earlier volumes.",
  "Numéro de {title}": "Number of {title}",
  "Renuméroter dans cet ordre": "Renumber in this order",
  "Enregistrer la numérotation": "Save numbering",
  "Numérotation enregistrée.": "Numbering saved.",
  "Numéros en double : {numbers}.": "Duplicate numbers: {numbers}.",
  "Monter {title}": "Move {title} up",
  "Descendre {title}": "Move {title} down",
  "Sélectionner {title}": "Select {title}",
  "Actions pour {title}": "Actions for {title}",
  Ouvrir: "Open",
  "Détacher de la série": "Detach from the series",
  "Détacher « {title} » ?": "Detach “{title}”?",
  "Le volume redevient un volume unique ; la mémoire de la série est recalculée sans lui.":
    "The volume becomes a standalone volume again; the series memory is recomputed without it.",
  Détacher: "Detach",
  "Flux continu": "Continuous flow",
  "Aucun volume dans cette série.": "No volume in this series.",
  "Rattacher un volume unique": "Attach a standalone volume",
  "Un EPUB sans série rejoint celle-ci avec le numéro choisi.": "An EPUB without a series joins this one with the chosen number.",
  "Volume unique": "Standalone volume",
  "Choisissez un volume…": "Choose a volume…",
  "Numéro du volume": "Volume number",
  Rattacher: "Attach",
  "Aucun volume unique à rattacher.": "No standalone volume to attach.",
  "Série {series}": "Series {series}",
  "Tous les volumes": "All volumes",
  "Filtrer par volume": "Filter by volume",
  "Aucun chapitre texte dans cette série.": "No text chapter in this series.",
  "Chargement des chapitres…": "Loading chapters…",
  "{translated}/{total} passages traduits · {validated} validés": "{translated}/{total} passages translated · {validated} validated",
  "Progression de {title}": "Progress of {title}",
  Analysé: "Analyzed",
  "Non analysé": "Not analyzed",
  "Contexte modifié": "Context changed",
  "{count} ouverts": "{count} open",
  "Ajouter du contenu à cette série": "Add content to this series",
  "EPUB : de nouveaux volumes numérotés. TXT : des chapitres dans le flux continu, un volume existant ou un nouveau volume. Rien n’est créé avant la confirmation.":
    "EPUB: new numbered volumes. TXT: chapters in the continuous flow, an existing volume or a new volume. Nothing is created before the confirmation.",
  "Les scripts peuvent aussi envoyer des chapitres par l’API JSON avec un jeton d’API.":
    "Scripts can also send chapters through the JSON API with an API token.",
});

type TabId =
  | "dashboard"
  | "volumes"
  | "chapters"
  | "import"
  | "bible"
  | "glossary"
  | "identities"
  | "relations"
  | "memory"
  | "settings"
  | "manage";

export function SeriesPage({ id, user, run }: { id: string; user: User; run: Run }) {
  const { t, tp } = useI18n();
  const [series, setSeries] = useState<SeriesDetail | null>(null);
  const [missing, setMissing] = useState(false);
  const [tab, setTab] = useState<TabId>("dashboard");
  const [wizard, setWizard] = useState(false);
  const load = useCallback(async () => {
    try {
      setSeries(await api<SeriesDetail>(`/series/${id}`));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setMissing(true);
      else throw e;
    }
  }, [id]);
  useEffect(() => {
    void run.background(load);
    const timer = setInterval(() => void run.background(load), 10000);
    return () => clearInterval(timer);
  }, [run, load]);
  useEffect(() => {
    if (series) document.title = `${series.name} · Libris`;
  }, [series?.name]);
  if (missing)
    return (
      <Page>
        <Card>
          <EmptyState
            icon="search"
            title={t("Série introuvable.")}
            description={t("Elle a peut-être été supprimée, ou vous n’y avez plus accès.")}
            action={
              <Button onClick={() => (location.hash = "library")} icon="chevronLeft">
                {t("Retour à la bibliothèque")}
              </Button>
            }
          />
        </Card>
      </Page>
    );
  if (!series)
    return (
      <Page>
        <LoadingBlock label={t("Ouverture de la série…")} />
      </Page>
    );
  const owner = !series.shared && series.owner_id === user.id;
  const textual = series.kind === "webnovel" || series.formats.some((format) => format !== "epub");
  const tabs: { id: TabId; label: string; count?: number; groupStart?: boolean }[] = [
    { id: "dashboard", label: t("Tableau de bord") },
    { id: "volumes", label: t("Volumes"), count: series.volume_list.length },
    ...(textual ? [{ id: "chapters" as const, label: t("Chapitres"), count: series.chapters }] : []),
    ...(owner ? [{ id: "import" as const, label: t("Importer") }] : []),
    { id: "bible", label: t("Series Bible"), groupStart: true },
    { id: "glossary", label: t("Glossaire") },
    { id: "identities", label: t("Identités") },
    { id: "relations", label: t("Relations") },
    { id: "memory", label: t("Mémoire") },
    ...(owner
      ? [
          { id: "settings" as const, label: t("Paramètres par défaut"), groupStart: true },
          { id: "manage" as const, label: t("Gestion") },
        ]
      : []),
  ];
  const refresh = () => run.background(load);
  return (
    <Page className="series-page">
      <PageHeader
        breadcrumb={
          <>
            <a href="#library">{t("Bibliothèque")}</a>
            <Icon name="chevronRight" size={12} />
            <span className="breadcrumb-current">{series.name}</span>
          </>
        }
        title={series.name}
        description={[
          series.authors.join(", "),
          tp(series.volume_list.length, "{count} volume", "{count} volumes"),
          tp(series.chapters, "{count} chapitre", "{count} chapitres"),
          series.source_language && series.target_language
            ? `${series.source_language.toUpperCase()} → ${series.target_language.toUpperCase()}`
            : "",
        ]
          .filter(Boolean)
          .join(" · ")}
        meta={
          <div className="series-card-badges">
            <Badge tone={series.kind === "webnovel" ? "info" : "neutral"}>
              {series.kind === "webnovel" ? t("Webnovel") : t("Livres")}
            </Badge>
            {series.formats.map((format) => (
              <Badge key={format}>{format.toUpperCase()}</Badge>
            ))}
            {series.shared && <Badge tone="accent">{t("Partagée")}</Badge>}
            {series.archived_at ? <Badge>{t("Archivé")}</Badge> : <StatusPill status={seriesStatus(series)} />}
          </div>
        }
        actions={
          owner &&
          !series.archived_at && (
            <Button variant="primary" icon="plus" onClick={() => setWizard(true)}>
              {t("Ajouter du contenu")}
            </Button>
          )
        }
      />
      {series.shared && (
        <Callout tone="info">{t("Série partagée avec vous : lecture seule, seuls les volumes partagés sont visibles.")}</Callout>
      )}
      {series.archived_at && <Callout tone="neutral">{t("Cette série est archivée.")}</Callout>}
      <Tabs
        items={tabs}
        value={tab}
        onChange={(value) => setTab(value as TabId)}
        label={t("Navigation de la série")}
        idPrefix="series"
      />
      <TabPanel idPrefix="series" value={tab} className="series-panel">
        {tab === "dashboard" ? (
          <SeriesDashboard series={series} run={run} refresh={refresh} goTo={setTab} />
        ) : tab === "volumes" ? (
          <SeriesVolumes series={series} owner={owner} user={user} run={run} refresh={load} />
        ) : tab === "chapters" ? (
          <SeriesChapters series={series} run={run} />
        ) : tab === "import" ? (
          <Card title={t("Ajouter du contenu à cette série")}>
            <div className="stack">
              <p className="muted">
                {t(
                  "EPUB : de nouveaux volumes numérotés. TXT : des chapitres dans le flux continu, un volume existant ou un nouveau volume. Rien n’est créé avant la confirmation.",
                )}
              </p>
              <p className="subtle">{t("Les scripts peuvent aussi envoyer des chapitres par l’API JSON avec un jeton d’API.")}</p>
              <div>
                <Button variant="primary" icon="plus" disabled={!!series.archived_at} onClick={() => setWizard(true)}>
                  {t("Ajouter du contenu")}
                </Button>
              </div>
            </div>
          </Card>
        ) : tab === "bible" ? (
          <SeriesBible series={series} owner={owner} run={run} refresh={refresh} />
        ) : tab === "glossary" ? (
          <SeriesGlossary series={series} owner={owner} run={run} />
        ) : tab === "identities" ? (
          <SeriesIdentities series={series} owner={owner} run={run} />
        ) : tab === "relations" ? (
          <SeriesRelations series={series} run={run} />
        ) : tab === "memory" ? (
          <SeriesMemory series={series} owner={owner} run={run} />
        ) : tab === "settings" ? (
          <SeriesSettings series={series} run={run} refresh={refresh} />
        ) : (
          <SeriesManagement series={series} run={run} refresh={refresh} />
        )}
      </TabPanel>
      {wizard && (
        <ImportWizard
          run={run}
          start={{ seriesId: series.id }}
          admin={user.admin}
          onClose={() => setWizard(false)}
          onImported={() => void refresh()}
        />
      )}
    </Page>
  );
}

function SeriesDashboard({
  series,
  run,
  refresh,
  goTo,
}: {
  series: SeriesDetail;
  run: Run;
  refresh: () => Promise<void>;
  goTo: (tab: TabId) => void;
}) {
  const { t, tp } = useI18n();
  const { confirm } = useDialogs();
  const toast = useToast();
  const [stale, setStale] = useState<SeriesChapter[] | null>(null);
  const staleCount = series.issues.context_stale;
  const loadStale = useCallback(async () => {
    const chapters = await api<SeriesChapter[]>(`/series/${series.id}/chapters`);
    setStale(chapters.filter((chapter) => chapter.context_stale));
  }, [series.id]);
  useEffect(() => {
    if (staleCount) void run.background(loadStale);
    else setStale([]);
  }, [staleCount, run, loadStale]);
  const volumes = series.volume_list.filter((project) => !project.archived_at);
  const actions: { key: string; tone: "warning" | "danger" | "info"; text: string; action?: ReactNode }[] = [];
  if (!volumes.length) actions.push({ key: "empty", tone: "info", text: t("Ajoutez un premier volume ou des chapitres.") });
  for (const project of volumes) {
    const open = (
      <a className="btn btn-sm btn-secondary" href={`#project/${project.id}`}>
        <span className="btn-label">{t("Ouvrir le volume")}</span>
      </a>
    );
    if (!project.provider_id)
      actions.push({ key: `provider-${project.id}`, tone: "warning", text: t("Aucun provider pour « {title} ».", { title: project.title }), action: open });
    else if (project.stats.total > 0 && project.stats.analyzed_segments === 0 && project.status !== "analyzing")
      actions.push({ key: `analyze-${project.id}`, tone: "info", text: t("« {title} » n’est pas encore analysé.", { title: project.title }), action: open });
    if (project.stats.flagged > 0)
      actions.push({
        key: `flagged-${project.id}`,
        tone: "info",
        text: tp(
          project.stats.flagged,
          "{count} passage ouvert à une relecture facultative dans « {title} ».",
          "{count} passages ouverts à une relecture facultative dans « {title} ».",
          { title: project.title },
        ),
        action: open,
      });
  }
  const seeVolumes = (
    <Button size="sm" onClick={() => goTo("volumes")}>
      {t("Voir les volumes")}
    </Button>
  );
  if (series.missing_volumes.length)
    actions.push({ key: "missing", tone: "warning", text: t("Volumes manquants : {volumes}.", { volumes: series.missing_volumes.join(", ") }), action: seeVolumes });
  if (series.duplicate_volumes.length)
    actions.push({
      key: "duplicates",
      tone: "danger",
      text: t("Numéros de volume en double : {volumes}.", { volumes: series.duplicate_volumes.join(", ") }),
      action: seeVolumes,
    });
  if (series.memory.failed)
    actions.push({
      key: "memory",
      tone: "danger",
      text: tp(series.memory.failed, "{count} envoi de mémoire en échec.", "{count} envois de mémoire en échec."),
      action: (
        <Button size="sm" onClick={() => goTo("memory")}>
          {t("Voir la mémoire")}
        </Button>
      ),
    });
  async function review(chapter: SeriesChapter) {
    const accepted = await confirm({
      title: t("Relire « {title} » ?", { title: chapter.title }),
      message: t("L’IA relit les passages traduits de ce chapitre avec le contexte à jour. Cette opération appelle le modèle."),
      confirmLabel: t("Lancer"),
    });
    if (!accepted) return;
    await run(async () => {
      await send(`/projects/${chapter.project_id}/jobs`, { operation: "review", chapter_id: chapter.id });
      toast(t("Relecture lancée pour « {title} ».", { title: chapter.title }));
    });
  }
  async function checked(chapter: SeriesChapter) {
    await run(async () => {
      await send(`/projects/${chapter.project_id}/chapters/${chapter.id}/stale`, { stale: false }, "PUT");
      toast(t("« {title} » marqué comme vérifié.", { title: chapter.title }));
      await Promise.all([loadStale(), refresh()]);
    });
  }
  return (
    <div className="stack">
      <div className="stat-grid">
        <Stat label={t("Volumes")} value={formatNumber(series.volume_list.length)} />
        <Stat label={t("Chapitres")} value={formatNumber(series.chapters)} />
        <Stat
          label={t("Traduits")}
          value={formatPercent(series.progress.percent)}
          hint={`${formatNumber(series.progress.translated)}/${formatNumber(series.progress.total)}`}
        />
        <Stat label={t("Validés")} value={formatNumber(series.progress.validated)} />
        <Stat label={t("Travaux en cours")} value={formatNumber(series.progress.running)} />
        <Stat label={t("Relecture facultative")} value={formatNumber(series.issues.flagged)} />
        <Stat label={t("Erreurs")} value={formatNumber(series.issues.errors)} tone={series.issues.errors ? "danger" : undefined} />
      </div>
      <Card title={t("Prochaines actions")}>
        {actions.length ? (
          <ul className="next-actions">
            {actions.map((item) => (
              <li key={item.key} className={`tone-border-${item.tone}`}>
                <Icon name={item.tone === "info" ? "info" : "alert"} className={`tone-text-${item.tone}`} />
                <span className="grow">{item.text}</span>
                {item.action}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">{t("Rien à signaler : chaque volume a un provider et aucune alerte n’attend.")}</p>
        )}
      </Card>
      {staleCount > 0 && (
        <Card
          title={t("Chapitres à revoir")}
          description={t(
            "Le contexte de ces chapitres a changé depuis leur traduction (volume antérieur, glossaire ou identité modifiés).",
          )}
        >
          {stale === null ? (
            <LoadingBlock label={t("Chargement des chapitres…")} lines={2} />
          ) : (
            <ul className="series-list">
              {stale.map((chapter) => (
                <li key={chapter.id} className="series-list-item">
                  <div className="series-list-main">
                    <strong>{chapter.title}</strong>
                    <span className="subtle">{chapter.volume_title}</span>
                  </div>
                  <div className="series-list-actions">
                    <a className="btn btn-sm btn-ghost" href={`#project/${chapter.project_id}`}>
                      <span className="btn-label">{t("Ouvrir le volume")}</span>
                    </a>
                    <Button size="sm" icon="check" onClick={() => void checked(chapter)}>
                      {t("Marquer comme vérifié")}
                    </Button>
                    <Button size="sm" icon="refresh" onClick={() => void review(chapter)}>
                      {t("Relire le chapitre")}
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  );
}

function SeriesVolumes({
  series,
  owner,
  user,
  run,
  refresh,
}: {
  series: SeriesDetail;
  owner: boolean;
  user: User;
  run: Run;
  refresh: () => Promise<void>;
}) {
  const { t } = useI18n();
  const { confirm } = useDialogs();
  const toast = useToast();
  const initial = useMemo(
    () => series.volume_list.map((project) => ({ project, number: project.volume_number === null ? "" : String(project.volume_number) })),
    [series.volume_list],
  );
  const [order, setOrder] = useState(initial);
  const [dirty, setDirty] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [standalone, setStandalone] = useState<Project[]>([]);
  const [attachId, setAttachId] = useState("");
  const [attachNumber, setAttachNumber] = useState("");
  useEffect(() => {
    // A periodic refresh must not erase numbers being edited.
    if (!dirty) setOrder(initial);
  }, [initial, dirty]);
  const loadStandalone = useCallback(async () => {
    const projects = await api<Project[]>("/projects");
    setStandalone(
      projects.filter(
        (p) => !p.series_id && p.owner_id === user.id && p.project_kind !== "serial" && !p.archived_at,
      ),
    );
  }, [user.id]);
  useEffect(() => {
    if (owner) void run.background(loadStandalone);
  }, [owner, run, loadStandalone]);
  const numbers = order
    .filter((item) => item.project.project_kind !== "serial")
    .map((item) => Number(item.number))
    .filter((n) => validNumber(n));
  const duplicates = Array.from(new Set(numbers.filter((n, i) => numbers.indexOf(n) !== i)));
  const move = (index: number, delta: number) => {
    setDirty(true);
    setOrder((list) => {
      const next = [...list];
      const to = index + delta;
      if (to < 0 || to >= next.length) return list;
      [next[index], next[to]] = [next[to], next[index]];
      return next;
    });
  };
  function renumber() {
    setDirty(true);
    let value = 1;
    setOrder((list) =>
      list.map((item) => (item.project.project_kind === "serial" ? item : { ...item, number: String(value++) })),
    );
  }
  async function save() {
    await run(async () => {
      await send(
        `/series/${series.id}/volumes`,
        {
          items: order
            .filter((item) => item.project.project_kind !== "serial")
            .map((item) => ({ project_id: item.project.id, volume_number: item.number ? Number(item.number) : null })),
        },
        "PUT",
      );
      setDirty(false);
      toast(t("Numérotation enregistrée."));
      await refresh();
    });
  }
  async function detach(project: Project) {
    const accepted = await confirm({
      title: t("Détacher « {title} » ?", { title: project.title }),
      message: t("Le volume redevient un volume unique ; la mémoire de la série est recalculée sans lui."),
      confirmLabel: t("Détacher"),
    });
    if (!accepted) return;
    await run(async () => {
      await send(`/series/${series.id}/volumes/${project.id}/detach`);
      await Promise.all([refresh(), loadStandalone()]);
    });
  }
  async function attach() {
    await run(async () => {
      await send(`/series/${series.id}/volumes`, {
        project_id: attachId,
        volume_number: attachNumber ? Number(attachNumber) : null,
      });
      setAttachId("");
      setAttachNumber("");
      await Promise.all([refresh(), loadStandalone()]);
    });
  }
  const toggle = (id: string, checked: boolean) =>
    setSelected((previous) => {
      const next = new Set(previous);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  return (
    <div className="stack">
      <Card
        title={t("Ordre de lecture")}
        description={t("Les numéros fixent l’ordre de lecture : un volume ne reçoit la mémoire que des volumes précédents.")}
        actions={
          owner &&
          order.length > 1 && (
            <>
              <Button size="sm" icon="refresh" onClick={renumber}>
                {t("Renuméroter dans cet ordre")}
              </Button>
              <Button size="sm" variant="primary" disabled={!dirty || duplicates.length > 0} onClick={() => void save()}>
                {t("Enregistrer la numérotation")}
              </Button>
            </>
          )
        }
      >
        {duplicates.length > 0 && (
          <Callout tone="danger" role="alert">
            {t("Numéros en double : {numbers}.", { numbers: duplicates.join(", ") })}
          </Callout>
        )}
        {!order.length ? (
          <EmptyState compact icon="book" title={t("Aucun volume dans cette série.")} />
        ) : (
          <ol className="series-list volume-list">
            {order.map((item, index) => {
              const project = item.project;
              const serial = project.project_kind === "serial";
              return (
                <li key={project.id} className={cx("series-list-item", !!project.archived_at && "is-archived")}>
                  <div className="volume-order">
                    {!project.archived_at && (
                      <input
                        type="checkbox"
                        aria-label={t("Sélectionner {title}", { title: project.title })}
                        checked={selected.has(project.id)}
                        onChange={(e) => toggle(project.id, e.target.checked)}
                      />
                    )}
                    {serial ? (
                      <Badge tone="info">{t("Flux continu")}</Badge>
                    ) : (
                      <Input
                        className="volume-number"
                        inputMode="numeric"
                        aria-label={t("Numéro de {title}", { title: project.title })}
                        value={item.number}
                        disabled={!owner}
                        onChange={(e) => {
                          setDirty(true);
                          setOrder((list) =>
                            list.map((entry, i) => (i === index ? { ...entry, number: e.target.value.replace(/\D/g, "") } : entry)),
                          );
                        }}
                      />
                    )}
                  </div>
                  <div className="series-list-main">
                    <a className="book-title" href={`#project/${project.id}`}>
                      {project.title}
                    </a>
                    <span className="series-card-badges">
                      {project.source_format && <Badge>{project.source_format.toUpperCase()}</Badge>}
                      {project.archived_at ? <Badge>{t("Archivé")}</Badge> : <StatusPill status={project.status} />}
                    </span>
                  </div>
                  <div className="volume-progress">
                    <BookProgress project={project} compact />
                  </div>
                  <div className="series-list-actions">
                    {owner && (
                      <>
                        <IconButton
                          icon="chevronUp"
                          size="sm"
                          label={t("Monter {title}", { title: project.title })}
                          disabled={index === 0}
                          onClick={() => move(index, -1)}
                        />
                        <IconButton
                          icon="chevronDown"
                          size="sm"
                          label={t("Descendre {title}", { title: project.title })}
                          disabled={index === order.length - 1}
                          onClick={() => move(index, 1)}
                        />
                      </>
                    )}
                    <Menu
                      label={t("Actions pour {title}", { title: project.title })}
                      trigger={(props) => (
                        <IconButton
                          {...props}
                          icon="more"
                          size="sm"
                          label={t("Actions pour {title}", { title: project.title })}
                          tooltip={false}
                        />
                      )}
                      items={[
                        { label: t("Ouvrir"), icon: "arrowRight", href: `#project/${project.id}` },
                        ...(owner && project.source_format === "epub"
                          ? [{ label: t("Détacher de la série"), icon: "link" as const, onSelect: () => void detach(project) }]
                          : []),
                      ]}
                    />
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </Card>
      {owner && (
        <Card title={t("Rattacher un volume unique")} description={t("Un EPUB sans série rejoint celle-ci avec le numéro choisi.")}>
          {standalone.length ? (
            <form
              className="inline-form"
              onSubmit={(event) => {
                event.preventDefault();
                void attach();
              }}
            >
              <label className="field">
                <span className="field-label">{t("Volume unique")}</span>
                <Select value={attachId} onChange={(e) => setAttachId(e.target.value)} required>
                  <option value="">{t("Choisissez un volume…")}</option>
                  {standalone.map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.title}
                    </option>
                  ))}
                </Select>
              </label>
              <label className="field">
                <span className="field-label">{t("Numéro du volume")}</span>
                <Input
                  type="number"
                  min="1"
                  max="10000"
                  value={attachNumber}
                  onChange={(e) => setAttachNumber(e.target.value)}
                />
              </label>
              <Button type="submit" icon="link" disabled={!attachId}>
                {t("Rattacher")}
              </Button>
            </form>
          ) : (
            <p className="muted">{t("Aucun volume unique à rattacher.")}</p>
          )}
        </Card>
      )}
      {selected.size > 0 && (
        <BatchActions
          books={series.volume_list.filter((project) => selected.has(project.id))}
          run={run}
          refresh={refresh}
          onClear={() => setSelected(new Set())}
          onDeleted={(ids) =>
            setSelected((previous) => {
              const next = new Set(previous);
              ids.forEach((id) => next.delete(id));
              return next;
            })
          }
          scopeLabel={t("Série {series}", { series: series.name })}
        />
      )}
    </div>
  );
}

function validNumber(value: number) {
  return Number.isInteger(value) && value >= 1;
}

function SeriesChapters({ series, run }: { series: SeriesDetail; run: Run }) {
  const { t } = useI18n();
  const [volume, setVolume] = useState("");
  const [chapters, setChapters] = useState<SeriesChapter[] | null>(null);
  const load = useCallback(async () => {
    setChapters(await api<SeriesChapter[]>(`/series/${series.id}/chapters${volume ? `?project_id=${volume}` : ""}`));
  }, [series.id, volume]);
  useEffect(() => {
    setChapters(null);
    void run.background(load);
  }, [run, load]);
  const textVolumes = series.volume_list.filter((project) => project.source_format !== "epub");
  return (
    <Card padded={false}>
      <div className="card-toolbar">
        <label className="toolbar-select">
          <span className="sr-only">{t("Filtrer par volume")}</span>
          <Select value={volume} onChange={(e) => setVolume(e.target.value)} aria-label={t("Filtrer par volume")}>
            <option value="">{t("Tous les volumes")}</option>
            {(textVolumes.length ? textVolumes : series.volume_list).map((project) => (
              <option key={project.id} value={project.id}>
                {project.project_kind === "serial" ? t("Flux continu") : project.title}
              </option>
            ))}
          </Select>
        </label>
      </div>
      {chapters === null ? (
        <div className="card-inset">
          <LoadingBlock label={t("Chargement des chapitres…")} />
        </div>
      ) : !chapters.length ? (
        <EmptyState compact icon="file" title={t("Aucun chapitre texte dans cette série.")} />
      ) : (
        <ol className="series-list chapter-list">
          {chapters.map((chapter) => {
            const percent = chapter.segments ? Math.round((chapter.translated / chapter.segments) * 100) : 0;
            return (
              <li key={chapter.id} className="series-list-item">
                <span className="series-number tabular">{chapter.chapter_number ?? "—"}</span>
                <div className="series-list-main">
                  <a className="book-title" href={`#project/${chapter.project_id}`}>
                    {chapter.title}
                  </a>
                  <span className="subtle">{chapter.volume_title}</span>
                </div>
                <div className="chapter-progress">
                  <span className="subtle">
                    {t("{translated}/{total} passages traduits · {validated} validés", {
                      translated: chapter.translated,
                      total: chapter.segments,
                      validated: chapter.validated,
                    })}
                  </span>
                  <progress
                    className="progress progress-sm tone-accent"
                    value={percent}
                    max={100}
                    aria-label={t("Progression de {title}", { title: chapter.title })}
                  />
                </div>
                <div className="series-card-badges">
                  <Badge tone={chapter.analyzed ? "success" : "neutral"}>
                    {chapter.analyzed ? t("Analysé") : t("Non analysé")}
                  </Badge>
                  {chapter.flagged > 0 && <Badge tone="info">{t("{count} ouverts", { count: chapter.flagged })}</Badge>}
                  {chapter.context_stale && <Badge tone="warning">{t("Contexte modifié")}</Badge>}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </Card>
  );
}
