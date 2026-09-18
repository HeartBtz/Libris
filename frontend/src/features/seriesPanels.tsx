import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api, ApiError, send } from "../api";
import { formatDateTime, formatNumber, formatPercent, registerTranslations, useI18n } from "../i18n";
import type {
  AuditEntry,
  ContextBackend,
  EntityCategory,
  GlossaryOverride,
  ProviderSummary,
  Quality,
  Run,
  SeriesDetail,
  SeriesEntity,
  SeriesMemory as SeriesMemoryView,
  SeriesRelation,
  SeriesTerm,
} from "../types";
import {
  Badge,
  Button,
  Callout,
  Card,
  Checkbox,
  Dialog,
  EmptyState,
  Field,
  FormGrid,
  IconButton,
  Input,
  LoadingBlock,
  SearchInput,
  SegmentedControl,
  Select,
  Stat,
  TextArea,
  useDialogs,
  useToast,
} from "../ui";

registerTranslations({
  "Dérivée des volumes": "Derived from the volumes",
  "Version validée par une personne": "Version validated by a person",
  "Mise à jour : {date}": "Updated: {date}",
  "Recalculer depuis les volumes": "Recompute from the volumes",
  "Tant qu’une version validée existe, le recalcul met à jour identités et glossaire sans la remplacer.":
    "While a validated version exists, recomputing updates identities and glossary without replacing it.",
  "Modifier (JSON avancé)": "Edit (advanced JSON)",
  "Rendre aux volumes": "Hand back to the volumes",
  "Rendre la Series Bible aux volumes ?": "Hand the Series Bible back to the volumes?",
  "Votre version cesse de faire autorité : la Series Bible est de nouveau recalculée depuis les volumes.":
    "Your version stops being authoritative: the Series Bible is recomputed from the volumes again.",
  "Series Bible recalculée.": "Series Bible recomputed.",
  "Series Bible enregistrée et validée.": "Series Bible saved and validated.",
  "JSON de la Series Bible": "Series Bible JSON",
  "JSON invalide : {error}": "Invalid JSON: {error}",
  "La Series Bible doit être un objet JSON.": "The Series Bible must be a JSON object.",
  "Enregistrer et valider": "Save and validate",
  Annuler: "Cancel",
  Univers: "Universe",
  Lieux: "Places",
  Organisations: "Organizations",
  "Objets importants": "Important objects",
  Conventions: "Conventions",
  Chronologie: "Chronology",
  Personnages: "Characters",
  Relations: "Relations",
  Genre: "Genre",
  Ton: "Tone",
  "Style narratif": "Narrative style",
  "Point de vue": "Point of view",
  Temps: "Tense",
  "Public visé": "Target audience",
  "Conventions typographiques": "Formatting conventions",
  "Titres honorifiques": "Honorifics",
  "Consignes de traduction": "Translation guidelines",
  "Jeux de mots connus": "Known wordplay",
  "vol. {volume}": "vol. {volume}",
  "{count} relation entre identités : voir l’onglet Relations.": "{count} relation between identities: see the Relations tab.",
  "{count} relations entre identités : voir l’onglet Relations.": "{count} relations between identities: see the Relations tab.",
  "La Series Bible est vide : elle se remplit à l’analyse des volumes.": "The Series Bible is empty: it fills in as volumes are analyzed.",
  "Chargement…": "Loading…",
  Validée: "Validated",
  "Rechercher un terme…": "Search for a term…",
  "Rechercher dans le glossaire": "Search the glossary",
  "Glossaire de série": "Series glossary",
  "Les termes acceptés s’imposent aux volumes suivants, sauf dérogation explicite d’un volume.":
    "Accepted terms apply to later volumes, unless a volume explicitly departs from them.",
  "Aucun terme pour l’instant.": "No term yet.",
  "Aucun terme ne correspond.": "No term matches.",
  "Décision humaine": "Human decision",
  "Issu du volume {volume}": "From volume {volume}",
  "Issu des volumes": "From the volumes",
  Traduction: "Translation",
  Catégorie: "Category",
  Verrouillé: "Locked",
  Accepté: "Accepted",
  "Traduction de {term}": "Translation of {term}",
  "Catégorie de {term}": "Category of {term}",
  "Verrouiller {term}": "Lock {term}",
  "Accepter {term}": "Accept {term}",
  "Enregistrer {term}": "Save {term}",
  "Supprimer {term}": "Delete {term}",
  "Supprimer le terme « {term} » de la série ?": "Delete the term “{term}” from the series?",
  "Les volumes suivants ne recevront plus ce choix.": "Later volumes will no longer receive this choice.",
  Supprimer: "Delete",
  "Terme enregistré.": "Term saved.",
  "Ajouter un terme de série": "Add a series term",
  "Expression source": "Source expression",
  "Verrouiller ce terme": "Lock this term",
  Ajouter: "Add",
  "Dérogations des volumes": "Volume overrides",
  "Ces volumes gardent leur propre traduction d’un terme de la série.": "These volumes keep their own translation of a series term.",
  "Aucune dérogation.": "No override.",
  "Vol. {volume} · {title}": "Vol. {volume} · {title}",
  Objets: "Objects",
  "Catégorie d’identité": "Identity category",
  "Rechercher une identité": "Search for an identity",
  "Nom ou alias…": "Name or alias…",
  "Aucune identité pour l’instant : elles apparaissent à l’analyse des volumes.":
    "No identity yet: they appear as volumes are analyzed.",
  "Proposée automatiquement": "Proposed automatically",
  "Première apparition : volume {volume}": "First appearance: volume {volume}",
  "Alias : {aliases}": "Aliases: {aliases}",
  Lié: "Linked",
  Proposé: "Proposed",
  Rejeté: "Rejected",
  "confiance {value}": "confidence {value}",
  "Confirmer le lien": "Confirm the link",
  "Rejeter le lien": "Reject the link",
  "Confirmer le lien de {name}": "Confirm the link of {name}",
  "Rejeter le lien de {name}": "Reject the link of {name}",
  Modifier: "Edit",
  "Fusionner…": "Merge…",
  "Séparer…": "Split…",
  "Modifier {name}": "Edit {name}",
  Nom: "Name",
  "Alias (un par ligne)": "Aliases (one per line)",
  Enregistrer: "Save",
  "Fusionner dans « {name} »": "Merge into “{name}”",
  "Les identités cochées rejoignent « {name} » : leurs liens et leurs noms deviennent des alias. Seule une personne décide d’une fusion.":
    "The ticked identities join “{name}”: their links and names become aliases. Only a person decides a merge.",
  "Aucune autre identité de cette nature.": "No other identity of this kind.",
  Fusionner: "Merge",
  "Fusionner {count} identité dans « {name} » ?": "Merge {count} identity into “{name}”?",
  "Fusionner {count} identités dans « {name} » ?": "Merge {count} identities into “{name}”?",
  "La décision est enregistrée dans le journal de la série.": "The decision is recorded in the series log.",
  "Séparer « {name} »": "Split “{name}”",
  "Les volumes cochés reçoivent une nouvelle identité, distincte de « {name} ».":
    "The ticked volumes get a new identity, distinct from “{name}”.",
  "Nom de la nouvelle identité": "Name of the new identity",
  Séparer: "Split",
  "Séparer {count} lien de « {name} » ?": "Split {count} link from “{name}”?",
  "Séparer {count} liens de « {name} » ?": "Split {count} links from “{name}”?",
  "Aucune relation pour l’instant : elles apparaissent à l’analyse des volumes.":
    "No relation yet: they appear as volumes are analyzed.",
  "Mémoire OpenViking de la série": "Series OpenViking memory",
  "La mémoire de série n’est pas disponible sur ce serveur.": "Series memory is not available on this server.",
  "Résumé des volumes : sources {backends} · {pending} en attente · {failed} en échec.":
    "Volume summary: sources {backends} · {pending} pending · {failed} failed.",
  Configurée: "Configured",
  "Non configurée": "Not configured",
  Envoyés: "Sent",
  "En attente": "Pending",
  "En échec": "Failed",
  Sources: "Sources",
  Racine: "Root",
  Resynchroniser: "Resynchronize",
  Reconstruire: "Rebuild",
  Réindexer: "Reindex",
  "Reconstruire la mémoire de la série ?": "Rebuild the series memory?",
  "Les souvenirs de la série sont renvoyés à OpenViking depuis la base ; les traductions ne changent pas.":
    "The series memories are sent to OpenViking again from the database; translations do not change.",
  "Demande enregistrée.": "Request recorded.",
  "Aucun volume.": "No volume.",
  Interne: "Internal",
  Hybride: "Hybrid",
  Série: "Series",
  Type: "Type",
  "Livres (volumes EPUB)": "Books (EPUB volumes)",
  "Webnovel (chapitres)": "Webnovel (chapters)",
  "Langue source": "Source language",
  "Langue cible": "Target language",
  Provider: "Provider",
  Aucun: "None",
  Qualité: "Quality",
  "Non définie": "Not set",
  Rapide: "Fast",
  Normale: "Normal",
  Élevée: "High",
  Maximale: "Maximum",
  "Source de mémoire": "Memory source",
  "Instructions de la série": "Series instructions",
  "Valeurs proposées aux nouveaux volumes et chapitres importés ; les volumes existants gardent leurs réglages.":
    "Values offered to newly imported volumes and chapters; existing volumes keep their settings.",
  "Paramètres enregistrés.": "Settings saved.",
  Archiver: "Archive",
  Restaurer: "Restore",
  "Archiver la série": "Archive the series",
  "La série et ses volumes quittent la bibliothèque active ; rien n’est supprimé.":
    "The series and its volumes leave the active library; nothing is deleted.",
  "Restaurer la série": "Restore the series",
  "La série et les volumes archivés avec elle reviennent dans la bibliothèque.":
    "The series and the volumes archived with it return to the library.",
  "Supprimer la série": "Delete the series",
  "Seule une série sans volume peut être supprimée : détachez ou supprimez d’abord ses volumes.":
    "Only a series without volumes can be deleted: detach or delete its volumes first.",
  "Supprimer la série « {name} » ?": "Delete the series “{name}”?",
  "Sa Series Bible, son glossaire et ses identités sont supprimés définitivement.":
    "Its Series Bible, glossary and identities are permanently deleted.",
  "Supprimer définitivement": "Delete permanently",
  "Journal de la série": "Series log",
  "Les décisions qui engagent la série : fusions, séparations, glossaire, dérogations.":
    "Decisions that commit the series: merges, splits, glossary, overrides.",
  "Aucune décision enregistrée.": "No decision recorded.",
  "Series Bible modifiée": "Series Bible edited",
  "Terme de série ajouté": "Series term added",
  "Terme de série modifié": "Series term edited",
  "Terme de série supprimé": "Series term deleted",
  "Identité modifiée": "Identity edited",
  "Identités fusionnées": "Identities merged",
  "Identité séparée": "Identity split",
  "Lien d’identité confirmé": "Identity link confirmed",
  "Lien d’identité rejeté": "Identity link rejected",
  "Dérogation d’un volume au glossaire": "Volume glossary override",
  "Dérogation retirée": "Override removed",
});

const WORLD: [string, string][] = [
  ["locations", "Lieux"],
  ["organizations", "Organisations"],
  ["important_objects", "Objets importants"],
];
const UNIVERSE: Record<string, string> = {
  genre: "Genre",
  tone: "Ton",
  narrative_style: "Style narratif",
  narrative_point_of_view: "Point de vue",
  tense: "Temps",
  target_audience: "Public visé",
};
const CONVENTIONS: Record<string, string> = {
  formatting_conventions: "Conventions typographiques",
  honorifics: "Titres honorifiques",
  translation_guidelines: "Consignes de traduction",
  known_wordplay: "Jeux de mots connus",
};
const AUDIT: Record<string, string> = {
  "series.bible_edited": "Series Bible modifiée",
  "series.term_added": "Terme de série ajouté",
  "series.term_edited": "Terme de série modifié",
  "series.term_deleted": "Terme de série supprimé",
  "series.identity_edited": "Identité modifiée",
  "series.identities_merged": "Identités fusionnées",
  "series.identity_split": "Identité séparée",
  "series.link_linked": "Lien d’identité confirmé",
  "series.link_rejected": "Lien d’identité rejeté",
  "glossary.series_override": "Dérogation d’un volume au glossaire",
  "glossary.series_override_removed": "Dérogation retirée",
};

/** Bible values come from models and people: show strings as they are, anything else compactly. */
function show(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}
const asList = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
const asRecord = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};

function BibleSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="bible-section">
      <h3 className="bible-section-title">{title}</h3>
      {children}
    </section>
  );
}

export function SeriesBible({
  series,
  owner,
  run,
  refresh,
}: {
  series: SeriesDetail;
  owner: boolean;
  run: Run;
  refresh: () => Promise<void>;
}) {
  const { t, tp } = useI18n();
  const { confirm } = useDialogs();
  const toast = useToast();
  const [data, setData] = useState<{ bible: Record<string, unknown>; validated: boolean; updated_at: number } | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [invalid, setInvalid] = useState("");
  const load = useCallback(async () => setData(await api(`/series/${series.id}/bible`)), [series.id]);
  useEffect(() => {
    void run.background(load);
  }, [run, load]);
  if (!data) return <LoadingBlock label={t("Chargement…")} />;
  const bible = data.bible || {};
  const universe = asRecord(bible.universe);
  const conventions = asRecord(bible.conventions);
  const chronology = asList(bible.chronology).map(asRecord);
  const characters = asList(bible.characters).map(asRecord);
  const relations = typeof bible.relations === "number" ? bible.relations : 0;
  const volume = (value: unknown) => (value === null || value === undefined ? "" : t("vol. {volume}", { volume: show(value) }));
  const empty =
    !Object.keys(universe).length &&
    !characters.length &&
    !chronology.length &&
    WORLD.every(([key]) => !asList(bible[key]).length);
  async function save() {
    let parsed: unknown;
    try {
      parsed = JSON.parse(draft || "");
    } catch (e) {
      setInvalid(t("JSON invalide : {error}", { error: e instanceof Error ? e.message : String(e) }));
      return;
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      setInvalid(t("La Series Bible doit être un objet JSON."));
      return;
    }
    await run(async () => {
      setData(await send(`/series/${series.id}/bible`, { bible: parsed, validated: true }, "PUT"));
      setDraft(null);
      toast(t("Series Bible enregistrée et validée."));
      await refresh();
    });
  }
  async function handBack() {
    const accepted = await confirm({
      title: t("Rendre la Series Bible aux volumes ?"),
      message: t("Votre version cesse de faire autorité : la Series Bible est de nouveau recalculée depuis les volumes."),
      confirmLabel: t("Rendre aux volumes"),
    });
    if (!accepted) return;
    await run(async () => {
      setData(await send(`/series/${series.id}/bible`, { bible, validated: false }, "PUT"));
      await refresh();
    });
  }
  return (
    <div className="stack">
      <div className="panel-header">
        <div className="row wrap">
          <Badge tone={data.validated ? "success" : "neutral"}>
            {data.validated ? t("Version validée par une personne") : t("Dérivée des volumes")}
          </Badge>
          <span className="subtle">{t("Mise à jour : {date}", { date: formatDateTime(data.updated_at) })}</span>
        </div>
        {owner && draft === null && (
          <div className="panel-actions">
            <Button
              icon="refresh"
              onClick={() =>
                void run(async () => {
                  setData(await send(`/series/${series.id}/refresh`));
                  toast(t("Series Bible recalculée."));
                  await refresh();
                })
              }
            >
              {t("Recalculer depuis les volumes")}
            </Button>
            <Button
              icon="edit"
              onClick={() => {
                setInvalid("");
                setDraft(JSON.stringify(bible, null, 2));
              }}
            >
              {t("Modifier (JSON avancé)")}
            </Button>
            {data.validated && <Button onClick={() => void handBack()}>{t("Rendre aux volumes")}</Button>}
          </div>
        )}
      </div>
      {data.validated && owner && (
        <p className="field-hint">
          {t("Tant qu’une version validée existe, le recalcul met à jour identités et glossaire sans la remplacer.")}
        </p>
      )}
      {draft !== null ? (
        <Card>
          <div className="stack">
            <Field label={t("JSON de la Series Bible")} error={invalid || undefined}>
              <TextArea
                className="code-editor"
                rows={18}
                spellCheck={false}
                value={draft}
                onChange={(e) => {
                  setInvalid("");
                  setDraft(e.target.value);
                }}
              />
            </Field>
            <div className="row wrap">
              <Button variant="primary" icon="check" onClick={() => void save()}>
                {t("Enregistrer et valider")}
              </Button>
              <Button onClick={() => setDraft(null)}>{t("Annuler")}</Button>
            </div>
          </div>
        </Card>
      ) : empty ? (
        <Card>
          <EmptyState compact icon="book" title={t("La Series Bible est vide : elle se remplit à l’analyse des volumes.")} />
        </Card>
      ) : (
        <Card className="bible-view">
          {Object.keys(universe).length > 0 && (
            <BibleSection title={t("Univers")}>
              <dl className="summary-list">
                {Object.entries(universe).map(([key, value]) => (
                  <div key={key} className="summary-pair">
                    <dt>{UNIVERSE[key] ? t(UNIVERSE[key]) : key}</dt>
                    <dd>{show(value)}</dd>
                  </div>
                ))}
              </dl>
            </BibleSection>
          )}
          {WORLD.map(([key, label]) =>
            asList(bible[key]).length ? (
              <BibleSection key={key} title={t(label)}>
                <ul className="chip-list">
                  {asList(bible[key]).map((item, index) => {
                    const entry = asRecord(item);
                    return (
                      <li key={index} className="chip">
                        {show(entry.name ?? item)}
                        {entry.first_volume !== undefined && <span className="subtle"> {volume(entry.first_volume)}</span>}
                      </li>
                    );
                  })}
                </ul>
              </BibleSection>
            ) : null,
          )}
          {Object.entries(conventions).some(([, value]) => asList(value).length) && (
            <BibleSection title={t("Conventions")}>
              {Object.entries(conventions).map(([key, value]) =>
                asList(value).length ? (
                  <div key={key} className="stack-sm">
                    <strong className="bible-subtitle">{CONVENTIONS[key] ? t(CONVENTIONS[key]) : key}</strong>
                    <ul className="bible-list">
                      {asList(value).map((item, index) => (
                        <li key={index}>{show(item)}</li>
                      ))}
                    </ul>
                  </div>
                ) : null,
              )}
            </BibleSection>
          )}
          {chronology.length > 0 && (
            <BibleSection title={t("Chronologie")}>
              <ol className="bible-list">
                {chronology.map((entry, index) => (
                  <li key={index}>
                    <strong>
                      {volume(entry.volume)} {show(entry.title)}
                    </strong>
                    {entry.summary ? <p className="muted">{show(entry.summary)}</p> : null}
                  </li>
                ))}
              </ol>
            </BibleSection>
          )}
          {characters.length > 0 && (
            <BibleSection title={t("Personnages")}>
              <ul className="bible-cards">
                {characters.map((entry, index) => (
                  <li key={show(entry.id) || index} className="bible-card">
                    <div className="row wrap">
                      <strong>{show(entry.name)}</strong>
                      {entry.first_volume !== null && entry.first_volume !== undefined && (
                        <span className="subtle">{volume(entry.first_volume)}</span>
                      )}
                      {entry.validated === true && <Badge tone="success">{t("Validée")}</Badge>}
                    </div>
                    {asList(entry.aliases).length > 0 && (
                      <span className="subtle">{t("Alias : {aliases}", { aliases: asList(entry.aliases).map(show).join(", ") })}</span>
                    )}
                    {entry.role ? <span>{show(entry.role)}</span> : null}
                    {entry.description ? <p className="muted">{show(entry.description)}</p> : null}
                  </li>
                ))}
              </ul>
            </BibleSection>
          )}
          {relations > 0 && (
            <BibleSection title={t("Relations")}>
              <p className="muted">
                {tp(
                  relations,
                  "{count} relation entre identités : voir l’onglet Relations.",
                  "{count} relations entre identités : voir l’onglet Relations.",
                )}
              </p>
            </BibleSection>
          )}
        </Card>
      )}
    </div>
  );
}

export function SeriesGlossary({ series, owner, run }: { series: SeriesDetail; owner: boolean; run: Run }) {
  const { t } = useI18n();
  const { confirm } = useDialogs();
  const toast = useToast();
  const [terms, setTerms] = useState<SeriesTerm[] | null>(null);
  const [overrides, setOverrides] = useState<GlossaryOverride[]>([]);
  const [search, setSearch] = useState("");
  const [source, setSource] = useState("");
  const [translation, setTranslation] = useState("");
  const [category, setCategory] = useState("");
  const [locked, setLocked] = useState(true);
  const load = useCallback(async () => {
    const data = await api<{ terms: SeriesTerm[]; overrides: GlossaryOverride[] }>(`/series/${series.id}/glossary`);
    setTerms(data.terms);
    setOverrides(data.overrides);
  }, [series.id]);
  useEffect(() => {
    void run.background(load);
  }, [run, load]);
  const update = (id: string, change: Partial<SeriesTerm>) =>
    setTerms((all) => (all || []).map((term) => (term.id === id ? { ...term, ...change } : term)));
  const payload = (term: Pick<SeriesTerm, "source" | "translation" | "category" | "description" | "locked" | "accepted">) => ({
    source: term.source,
    translation: term.translation,
    category: term.category || "autre",
    description: term.description,
    locked: term.locked,
    accepted: term.accepted,
  });
  const list = terms || [];
  const needle = search.toLocaleLowerCase();
  const filtered = list.filter((term) => `${term.source} ${term.translation}`.toLocaleLowerCase().includes(needle));
  return (
    <div className="stack">
      <Card
        title={t("Glossaire de série")}
        description={t("Les termes acceptés s’imposent aux volumes suivants, sauf dérogation explicite d’un volume.")}
      >
        <div className="series-toolbar">
          <label className="library-search">
            <span className="sr-only">{t("Rechercher dans le glossaire")}</span>
            <SearchInput placeholder={t("Rechercher un terme…")} value={search} onChange={(e) => setSearch(e.target.value)} />
          </label>
        </div>
        {terms === null ? (
          <LoadingBlock label={t("Chargement…")} />
        ) : !list.length ? (
          <EmptyState compact icon="book" title={t("Aucun terme pour l’instant.")} />
        ) : !filtered.length ? (
          <EmptyState compact icon="search" title={t("Aucun terme ne correspond.")} />
        ) : (
          <ul className="series-list term-list">
            {filtered.map((term) => (
              <li key={term.id} className="series-list-item">
                <div className="series-list-main">
                  <strong>{term.source}</strong>
                  <span className="subtle">
                    {term.origin === "human"
                      ? t("Décision humaine")
                      : term.first_volume_number !== null
                        ? t("Issu du volume {volume}", { volume: term.first_volume_number })
                        : t("Issu des volumes")}
                  </span>
                </div>
                <div className="term-fields">
                  <Input
                    aria-label={t("Traduction de {term}", { term: term.source })}
                    value={term.translation}
                    disabled={!owner}
                    onChange={(e) => update(term.id, { translation: e.target.value })}
                  />
                  <Input
                    aria-label={t("Catégorie de {term}", { term: term.source })}
                    value={term.category}
                    disabled={!owner}
                    onChange={(e) => update(term.id, { category: e.target.value })}
                  />
                </div>
                <div className="series-list-actions">
                  <Checkbox
                    label={t("Verrouillé")}
                    aria-label={t("Verrouiller {term}", { term: term.source })}
                    checked={term.locked}
                    disabled={!owner}
                    onChange={(e) => update(term.id, { locked: e.target.checked })}
                  />
                  <Checkbox
                    label={t("Accepté")}
                    aria-label={t("Accepter {term}", { term: term.source })}
                    checked={term.accepted}
                    disabled={!owner}
                    onChange={(e) => update(term.id, { accepted: e.target.checked })}
                  />
                  {owner && (
                    <>
                      <IconButton
                        icon="check"
                        size="sm"
                        label={t("Enregistrer {term}", { term: term.source })}
                        onClick={() =>
                          void run(async () => {
                            await send(`/series/${series.id}/glossary/${term.id}`, payload(term), "PUT");
                            toast(t("Terme enregistré."));
                            await load();
                          })
                        }
                      />
                      <IconButton
                        icon="trash"
                        size="sm"
                        label={t("Supprimer {term}", { term: term.source })}
                        onClick={() =>
                          void (async () => {
                            const accepted = await confirm({
                              title: t("Supprimer le terme « {term} » de la série ?", { term: term.source }),
                              message: t("Les volumes suivants ne recevront plus ce choix."),
                              confirmLabel: t("Supprimer"),
                              tone: "danger",
                            });
                            if (!accepted) return;
                            await run(async () => {
                              await api(`/series/${series.id}/glossary/${term.id}`, { method: "DELETE" });
                              await load();
                            });
                          })()
                        }
                      />
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
      {owner && (
        <Card title={t("Ajouter un terme de série")}>
          <form
            className="inline-form"
            onSubmit={(event) => {
              event.preventDefault();
              void run(async () => {
                await send(
                  `/series/${series.id}/glossary`,
                  payload({ source, translation, category, description: "", locked, accepted: true }),
                );
                setSource("");
                setTranslation("");
                setCategory("");
                await load();
              });
            }}
          >
            <Field label={t("Expression source")}>
              <Input value={source} onChange={(e) => setSource(e.target.value)} required maxLength={300} />
            </Field>
            <Field label={t("Traduction")}>
              <Input value={translation} onChange={(e) => setTranslation(e.target.value)} required maxLength={300} />
            </Field>
            <Field label={t("Catégorie")}>
              <Input value={category} onChange={(e) => setCategory(e.target.value)} maxLength={50} />
            </Field>
            <Checkbox label={t("Verrouiller ce terme")} checked={locked} onChange={(e) => setLocked(e.target.checked)} />
            <Button type="submit" variant="primary" icon="plus">
              {t("Ajouter")}
            </Button>
          </form>
        </Card>
      )}
      <Card title={t("Dérogations des volumes")} description={t("Ces volumes gardent leur propre traduction d’un terme de la série.")}>
        {overrides.length ? (
          <ul className="series-list">
            {overrides.map((item, index) => (
              <li key={`${item.project_id}-${item.source}-${index}`} className="series-list-item">
                <div className="series-list-main">
                  <strong>
                    {item.source} → {item.translation}
                  </strong>
                  <a className="subtle" href={`#project/${item.project_id}`}>
                    {t("Vol. {volume} · {title}", { volume: item.volume_number ?? "—", title: item.volume_title })}
                  </a>
                </div>
                {item.locked && <Badge tone="accent">{t("Verrouillé")}</Badge>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">{t("Aucune dérogation.")}</p>
        )}
      </Card>
    </div>
  );
}

const CATEGORIES: [EntityCategory, string][] = [
  ["character", "Personnages"],
  ["location", "Lieux"],
  ["organization", "Organisations"],
  ["object", "Objets"],
];

export function SeriesIdentities({ series, owner, run }: { series: SeriesDetail; owner: boolean; run: Run }) {
  const { t, tp } = useI18n();
  const { confirm } = useDialogs();
  const [category, setCategory] = useState<EntityCategory>("character");
  const [entities, setEntities] = useState<SeriesEntity[] | null>(null);
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<{ entity: SeriesEntity; name: string; aliases: string } | null>(null);
  const [merging, setMerging] = useState<{ entity: SeriesEntity; chosen: Set<string> } | null>(null);
  const [splitting, setSplitting] = useState<{ entity: SeriesEntity; chosen: Set<string>; name: string } | null>(null);
  const load = useCallback(
    async () => setEntities(await api<SeriesEntity[]>(`/series/${series.id}/entities?category=${category}`)),
    [series.id, category],
  );
  useEffect(() => {
    setEntities(null);
    void run.background(load);
  }, [run, load]);
  // Merged identities live on inside their target: only the canonical ones are shown.
  const visible = (entities || []).filter((entity) => !entity.merged_into_id);
  const needle = search.toLocaleLowerCase();
  const filtered = visible.filter((entity) =>
    [entity.name, ...entity.aliases].some((name) => name.toLocaleLowerCase().includes(needle)),
  );
  const statusLabel = { linked: t("Lié"), proposed: t("Proposé"), rejected: t("Rejeté") };
  const statusTone = { linked: "success", proposed: "warning", rejected: "neutral" } as const;
  const decide = (linkId: string, status: "linked" | "rejected") =>
    void run(async () => {
      await send(`/series/${series.id}/links/${linkId}`, { status }, "PUT");
      await load();
    });
  const toggle = (set: Set<string>, id: string, checked: boolean) => {
    const next = new Set(set);
    if (checked) next.add(id);
    else next.delete(id);
    return next;
  };
  async function merge() {
    if (!merging) return;
    const { entity, chosen } = merging;
    const accepted = await confirm({
      title: tp(chosen.size, "Fusionner {count} identité dans « {name} » ?", "Fusionner {count} identités dans « {name} » ?", {
        name: entity.name,
      }),
      message: t("La décision est enregistrée dans le journal de la série."),
      confirmLabel: t("Fusionner"),
      tone: "danger",
    });
    if (!accepted) return;
    await run(async () => {
      await send(`/series/${series.id}/entities/merge`, { target_id: entity.id, source_ids: Array.from(chosen) });
      setMerging(null);
      await load();
    });
  }
  async function split() {
    if (!splitting) return;
    const { entity, chosen, name } = splitting;
    const accepted = await confirm({
      title: tp(chosen.size, "Séparer {count} lien de « {name} » ?", "Séparer {count} liens de « {name} » ?", {
        name: entity.name,
      }),
      message: t("La décision est enregistrée dans le journal de la série."),
      confirmLabel: t("Séparer"),
      tone: "danger",
    });
    if (!accepted) return;
    await run(async () => {
      await send(`/series/${series.id}/entities/${entity.id}/split`, { link_ids: Array.from(chosen), name: name.trim() });
      setSplitting(null);
      await load();
    });
  }
  return (
    <div className="stack">
      <div className="panel-header">
        <SegmentedControl
          label={t("Catégorie d’identité")}
          value={category}
          onChange={setCategory}
          options={CATEGORIES.map(([value, label]) => ({ value, label: t(label) }))}
        />
        <label className="library-search">
          <span className="sr-only">{t("Rechercher une identité")}</span>
          <SearchInput placeholder={t("Nom ou alias…")} value={search} onChange={(e) => setSearch(e.target.value)} />
        </label>
      </div>
      {entities === null ? (
        <LoadingBlock label={t("Chargement…")} />
      ) : !filtered.length ? (
        <Card>
          <EmptyState
            compact
            icon="users"
            title={t("Aucune identité pour l’instant : elles apparaissent à l’analyse des volumes.")}
          />
        </Card>
      ) : (
        <ul className="identity-list">
          {filtered.map((entity) => (
            <li key={entity.id} className="card card-padded identity-card">
              <div className="identity-head">
                <div className="stack-sm">
                  <strong className="identity-name">{entity.name}</strong>
                  <span className="subtle">
                    {entity.first_volume_number !== null &&
                      t("Première apparition : volume {volume}", { volume: entity.first_volume_number })}
                  </span>
                  {entity.aliases.length > 0 && (
                    <span className="muted">{t("Alias : {aliases}", { aliases: entity.aliases.join(", ") })}</span>
                  )}
                </div>
                <Badge tone={entity.validated ? "success" : "neutral"}>
                  {entity.validated ? t("Validée") : t("Proposée automatiquement")}
                </Badge>
              </div>
              {entity.links.length > 0 && (
                <ul className="identity-links">
                  {entity.links.map((link) => (
                    <li key={link.id}>
                      <div className="stack-sm grow">
                        <span>
                          {t("Vol. {volume} · {title}", { volume: link.volume_number ?? "—", title: link.volume_title })}
                          {" — "}
                          <strong>{link.local_name}</strong>
                          {link.local_aliases.length > 0 && <span className="subtle"> ({link.local_aliases.join(", ")})</span>}
                        </span>
                        <span className="subtle">
                          {t("confiance {value}", { value: formatPercent(link.confidence * 100) })}
                          {link.reason ? ` · ${link.reason}` : ""}
                        </span>
                      </div>
                      <Badge tone={statusTone[link.status]}>{statusLabel[link.status]}</Badge>
                      {owner && link.status !== "linked" && (
                        <Button size="sm" icon="check" aria-label={t("Confirmer le lien de {name}", { name: link.local_name })} onClick={() => decide(link.id, "linked")}>
                          {t("Confirmer le lien")}
                        </Button>
                      )}
                      {owner && link.status !== "rejected" && (
                        <Button size="sm" icon="x" aria-label={t("Rejeter le lien de {name}", { name: link.local_name })} onClick={() => decide(link.id, "rejected")}>
                          {t("Rejeter le lien")}
                        </Button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              {owner && (
                <div className="row wrap">
                  <Button
                    size="sm"
                    icon="edit"
                    onClick={() => setEditing({ entity, name: entity.name, aliases: entity.aliases.join("\n") })}
                  >
                    {t("Modifier")}
                  </Button>
                  <Button size="sm" onClick={() => setMerging({ entity, chosen: new Set() })}>
                    {t("Fusionner…")}
                  </Button>
                  {entity.links.length > 1 && (
                    <Button size="sm" onClick={() => setSplitting({ entity, chosen: new Set(), name: "" })}>
                      {t("Séparer…")}
                    </Button>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      <Dialog
        open={!!editing}
        onClose={() => setEditing(null)}
        title={editing ? t("Modifier {name}", { name: editing.entity.name }) : ""}
        footer={
          <>
            <Button onClick={() => setEditing(null)}>{t("Annuler")}</Button>
            <Button
              variant="primary"
              disabled={!editing?.name.trim()}
              onClick={() =>
                editing &&
                void run(async () => {
                  await send(
                    `/series/${series.id}/entities/${editing.entity.id}`,
                    {
                      name: editing.name.trim(),
                      aliases: editing.aliases.split("\n").map((alias) => alias.trim()).filter(Boolean),
                      validated: true,
                    },
                    "PUT",
                  );
                  setEditing(null);
                  await load();
                })
              }
            >
              {t("Enregistrer")}
            </Button>
          </>
        }
      >
        {editing && (
          <div className="stack">
            <Field label={t("Nom")}>
              <Input value={editing.name} maxLength={300} onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
            </Field>
            <Field label={t("Alias (un par ligne)")}>
              <TextArea rows={5} value={editing.aliases} onChange={(e) => setEditing({ ...editing, aliases: e.target.value })} />
            </Field>
          </div>
        )}
      </Dialog>
      <Dialog
        open={!!merging}
        onClose={() => setMerging(null)}
        title={merging ? t("Fusionner dans « {name} »", { name: merging.entity.name }) : ""}
        description={
          merging
            ? t(
                "Les identités cochées rejoignent « {name} » : leurs liens et leurs noms deviennent des alias. Seule une personne décide d’une fusion.",
                { name: merging.entity.name },
              )
            : undefined
        }
        footer={
          <>
            <Button onClick={() => setMerging(null)}>{t("Annuler")}</Button>
            <Button variant="danger" disabled={!merging?.chosen.size} onClick={() => void merge()}>
              {t("Fusionner")}
            </Button>
          </>
        }
      >
        {merging &&
          (visible.length > 1 ? (
            <div className="stack-sm">
              {visible
                .filter((entity) => entity.id !== merging.entity.id)
                .map((entity) => (
                  <Checkbox
                    key={entity.id}
                    label={entity.name}
                    description={entity.aliases.join(", ") || undefined}
                    checked={merging.chosen.has(entity.id)}
                    onChange={(e) => setMerging({ ...merging, chosen: toggle(merging.chosen, entity.id, e.target.checked) })}
                  />
                ))}
            </div>
          ) : (
            <p className="muted">{t("Aucune autre identité de cette nature.")}</p>
          ))}
      </Dialog>
      <Dialog
        open={!!splitting}
        onClose={() => setSplitting(null)}
        title={splitting ? t("Séparer « {name} »", { name: splitting.entity.name }) : ""}
        description={
          splitting
            ? t("Les volumes cochés reçoivent une nouvelle identité, distincte de « {name} ».", { name: splitting.entity.name })
            : undefined
        }
        footer={
          <>
            <Button onClick={() => setSplitting(null)}>{t("Annuler")}</Button>
            <Button
              variant="danger"
              disabled={!splitting?.chosen.size || !splitting.name.trim()}
              onClick={() => void split()}
            >
              {t("Séparer")}
            </Button>
          </>
        }
      >
        {splitting && (
          <div className="stack">
            <div className="stack-sm">
              {splitting.entity.links.map((link) => (
                <Checkbox
                  key={link.id}
                  label={`${t("Vol. {volume} · {title}", { volume: link.volume_number ?? "—", title: link.volume_title })} — ${link.local_name}`}
                  checked={splitting.chosen.has(link.id)}
                  onChange={(e) => setSplitting({ ...splitting, chosen: toggle(splitting.chosen, link.id, e.target.checked) })}
                />
              ))}
            </div>
            <Field label={t("Nom de la nouvelle identité")}>
              <Input value={splitting.name} maxLength={300} onChange={(e) => setSplitting({ ...splitting, name: e.target.value })} />
            </Field>
          </div>
        )}
      </Dialog>
    </div>
  );
}

export function SeriesRelations({ series, run }: { series: SeriesDetail; run: Run }) {
  const { t } = useI18n();
  const [relations, setRelations] = useState<SeriesRelation[] | null>(null);
  useEffect(() => {
    void run.background(async () => setRelations(await api(`/series/${series.id}/relations`)));
  }, [run, series.id]);
  if (!relations) return <LoadingBlock label={t("Chargement…")} />;
  if (!relations.length)
    return (
      <Card>
        <EmptyState compact icon="users" title={t("Aucune relation pour l’instant : elles apparaissent à l’analyse des volumes.")} />
      </Card>
    );
  return (
    <Card padded={false}>
      <ul className="series-list">
        {relations.map((relation) => (
          <li key={relation.id} className="series-list-item">
            <div className="series-list-main">
              <strong>
                {relation.source} — {relation.relation_type} → {relation.target}
              </strong>
              {relation.description && <span className="muted">{relation.description}</span>}
              {relation.evidence && <span className="subtle">« {relation.evidence} »</span>}
            </div>
            <div className="series-card-badges">
              {relation.first_volume_number !== null && (
                <Badge>{t("vol. {volume}", { volume: relation.first_volume_number })}</Badge>
              )}
              {relation.validated && <Badge tone="success">{t("Validée")}</Badge>}
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function SeriesMemory({ series, owner, run }: { series: SeriesDetail; owner: boolean; run: Run }) {
  const { t } = useI18n();
  const { confirm } = useDialogs();
  const toast = useToast();
  const [memory, setMemory] = useState<SeriesMemoryView | null>(null);
  const [failure, setFailure] = useState<"unavailable" | string | null>(null);
  const load = useCallback(async () => {
    try {
      setMemory(await api<SeriesMemoryView>(`/series/${series.id}/memory`));
      setFailure(null);
    } catch (e) {
      setFailure(e instanceof ApiError && e.status === 404 ? "unavailable" : e instanceof Error ? e.message : String(e));
    }
  }, [series.id]);
  useEffect(() => {
    void load();
  }, [load]);
  async function act(action: "resync" | "rebuild" | "reindex") {
    if (action === "rebuild") {
      const accepted = await confirm({
        title: t("Reconstruire la mémoire de la série ?"),
        message: t("Les souvenirs de la série sont renvoyés à OpenViking depuis la base ; les traductions ne changent pas."),
        confirmLabel: t("Reconstruire"),
      });
      if (!accepted) return;
    }
    await run(async () => {
      await send(`/series/${series.id}/memory/${action}`);
      toast(t("Demande enregistrée."));
      await load();
    });
  }
  const backend = (value?: string) =>
    value === "internal" ? t("Interne") : value === "hybrid" ? t("Hybride") : value === "openviking" ? "OpenViking" : value || "";
  return (
    <Card
      title={t("Mémoire OpenViking de la série")}
      actions={
        owner &&
        memory && (
          <>
            <Button size="sm" icon="refresh" onClick={() => void act("resync")}>
              {t("Resynchroniser")}
            </Button>
            <Button size="sm" onClick={() => void act("reindex")}>
              {t("Réindexer")}
            </Button>
            <Button size="sm" onClick={() => void act("rebuild")}>
              {t("Reconstruire")}
            </Button>
          </>
        )
      }
    >
      {failure ? (
        <div className="stack">
          <Callout tone={failure === "unavailable" ? "info" : "danger"} role={failure === "unavailable" ? "status" : "alert"}>
            {failure === "unavailable" ? t("La mémoire de série n’est pas disponible sur ce serveur.") : failure}
          </Callout>
          <p className="muted">
            {t("Résumé des volumes : sources {backends} · {pending} en attente · {failed} en échec.", {
              backends: series.memory.backends.map(backend).join(", ") || "—",
              pending: series.memory.pending,
              failed: series.memory.failed,
            })}
          </p>
        </div>
      ) : !memory ? (
        <LoadingBlock label={t("Chargement…")} lines={2} />
      ) : (
        <div className="stack">
          <div className="row wrap">
            <Badge tone={memory.configured ? "success" : "neutral"}>{memory.configured ? t("Configurée") : t("Non configurée")}</Badge>
            <span className="muted">
              {t("Sources")} : {memory.backends.map(backend).join(", ") || "—"}
            </span>
            {memory.root_uri && (
              <span className="muted">
                {t("Racine")} : <code>{memory.root_uri}</code>
              </span>
            )}
          </div>
          <div className="stat-grid">
            <Stat label={t("Envoyés")} value={formatNumber(memory.sent)} />
            <Stat label={t("En attente")} value={formatNumber(memory.pending)} tone={memory.pending ? "warning" : undefined} />
            <Stat label={t("En échec")} value={formatNumber(memory.failed)} tone={memory.failed ? "danger" : undefined} />
          </div>
          {memory.volumes.length ? (
            <ul className="series-list">
              {memory.volumes.map((volume) => (
                <li key={volume.project_id} className="series-list-item">
                  <div className="series-list-main">
                    <a href={`#project/${volume.project_id}`}>
                      {t("Vol. {volume} · {title}", { volume: volume.volume_number ?? "—", title: volume.title })}
                    </a>
                    {volume.context_backend && <span className="subtle">{backend(volume.context_backend)}</span>}
                  </div>
                  <div className="series-card-badges">
                    <Badge>{`${t("Envoyés")} ${formatNumber(volume.sent)}`}</Badge>
                    {volume.pending > 0 && <Badge tone="warning">{`${t("En attente")} ${formatNumber(volume.pending)}`}</Badge>}
                    {volume.failed > 0 && <Badge tone="danger">{`${t("En échec")} ${formatNumber(volume.failed)}`}</Badge>}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">{t("Aucun volume.")}</p>
          )}
        </div>
      )}
    </Card>
  );
}

export function SeriesSettings({ series, run, refresh }: { series: SeriesDetail; run: Run; refresh: () => Promise<void> }) {
  const { t } = useI18n();
  const toast = useToast();
  const [providers, setProviders] = useState<ProviderSummary[]>([]);
  const [value, setValue] = useState({
    name: series.name,
    kind: series.kind,
    source_language: series.source_language || "",
    target_language: series.target_language || "",
    provider_id: series.provider_id || "",
    quality: (series.quality || "") as "" | Quality,
    context_backend: (series.context_backend || "") as "" | ContextBackend,
    instructions: series.instructions,
  });
  useEffect(() => {
    void run.background(async () => setProviders(await api("/providers")));
  }, [run]);
  const field = <K extends keyof typeof value>(key: K, next: (typeof value)[K]) => setValue((current) => ({ ...current, [key]: next }));
  return (
    <form
      className="stack"
      onSubmit={(event) => {
        event.preventDefault();
        void run(async () => {
          await send(
            `/series/${series.id}`,
            {
              name: value.name.trim(),
              kind: value.kind,
              source_language: value.source_language.trim() || null,
              target_language: value.target_language.trim() || null,
              provider_id: value.provider_id || null,
              quality: value.quality || null,
              context_backend: value.context_backend || null,
              instructions: value.instructions,
            },
            "PUT",
          );
          toast(t("Paramètres enregistrés."));
          await refresh();
        });
      }}
    >
      <Card
        title={t("Paramètres par défaut")}
        description={t("Valeurs proposées aux nouveaux volumes et chapitres importés ; les volumes existants gardent leurs réglages.")}
      >
        <FormGrid>
          <Field label={t("Nom de la série")}>
            <Input value={value.name} required maxLength={500} onChange={(e) => field("name", e.target.value)} />
          </Field>
          <Field label={t("Type")}>
            <Select value={value.kind} onChange={(e) => field("kind", e.target.value as typeof value.kind)}>
              <option value="books">{t("Livres (volumes EPUB)")}</option>
              <option value="webnovel">{t("Webnovel (chapitres)")}</option>
            </Select>
          </Field>
          <Field label={t("Langue source")}>
            <Input value={value.source_language} placeholder="en" onChange={(e) => field("source_language", e.target.value)} />
          </Field>
          <Field label={t("Langue cible")}>
            <Input value={value.target_language} placeholder="fr" onChange={(e) => field("target_language", e.target.value)} />
          </Field>
          <Field label={t("Provider")}>
            <Select value={value.provider_id} onChange={(e) => field("provider_id", e.target.value)}>
              <option value="">{t("Aucun")}</option>
              {providers.map((provider) => (
                <option key={provider.id} value={provider.id}>
                  {provider.name} · {provider.model}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t("Qualité")}>
            <Select value={value.quality} onChange={(e) => field("quality", e.target.value as "" | Quality)}>
              <option value="">{t("Non définie")}</option>
              <option value="fast">{t("Rapide")}</option>
              <option value="normal">{t("Normale")}</option>
              <option value="high">{t("Élevée")}</option>
              <option value="maximum">{t("Maximale")}</option>
            </Select>
          </Field>
          <Field label={t("Source de mémoire")}>
            <Select value={value.context_backend} onChange={(e) => field("context_backend", e.target.value as "" | ContextBackend)}>
              <option value="">{t("Non définie")}</option>
              <option value="internal">{t("Interne")}</option>
              <option value="openviking">OpenViking</option>
              <option value="hybrid">{t("Hybride")}</option>
            </Select>
          </Field>
        </FormGrid>
        <Field label={t("Instructions de la série")}>
          <TextArea rows={5} maxLength={20000} value={value.instructions} onChange={(e) => field("instructions", e.target.value)} />
        </Field>
      </Card>
      <div>
        <Button type="submit" variant="primary" icon="check">
          {t("Enregistrer")}
        </Button>
      </div>
    </form>
  );
}

export function SeriesManagement({ series, run, refresh }: { series: SeriesDetail; run: Run; refresh: () => Promise<void> }) {
  const { t } = useI18n();
  const { confirm } = useDialogs();
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  useEffect(() => {
    void run.background(async () => setEntries(await api(`/series/${series.id}/audit`)));
  }, [run, series.id]);
  const archived = !!series.archived_at;
  async function remove() {
    const accepted = await confirm({
      title: t("Supprimer la série « {name} » ?", { name: series.name }),
      message: t("Sa Series Bible, son glossaire et ses identités sont supprimés définitivement."),
      confirmLabel: t("Supprimer définitivement"),
      tone: "danger",
    });
    if (!accepted) return;
    await run(async () => {
      await api(`/series/${series.id}`, { method: "DELETE" });
      location.hash = "library";
    });
  }
  const detail = (entry: AuditEntry) =>
    Object.entries(entry.detail)
      .filter(([key, value]) => !key.endsWith("_id") && key !== "previous" && value !== null && typeof value !== "object")
      .map(([, value]) => String(value))
      .join(" · ");
  return (
    <div className="stack">
      <Card
        title={archived ? t("Restaurer la série") : t("Archiver la série")}
        description={
          archived
            ? t("La série et les volumes archivés avec elle reviennent dans la bibliothèque.")
            : t("La série et ses volumes quittent la bibliothèque active ; rien n’est supprimé.")
        }
      >
        <Button
          icon="archive"
          onClick={() =>
            void run(async () => {
              await send(`/series/${series.id}/${archived ? "restore" : "archive"}`);
              await refresh();
            })
          }
        >
          {archived ? t("Restaurer") : t("Archiver")}
        </Button>
      </Card>
      <Card
        title={t("Supprimer la série")}
        description={t("Seule une série sans volume peut être supprimée : détachez ou supprimez d’abord ses volumes.")}
      >
        <Button variant="danger" icon="trash" disabled={series.volume_list.length > 0} onClick={() => void remove()}>
          {t("Supprimer la série")}
        </Button>
      </Card>
      <Card
        title={t("Journal de la série")}
        description={t("Les décisions qui engagent la série : fusions, séparations, glossaire, dérogations.")}
      >
        {entries === null ? (
          <LoadingBlock label={t("Chargement…")} lines={2} />
        ) : entries.length ? (
          <ol className="series-list audit-list">
            {entries.map((entry) => (
              <li key={entry.id} className="series-list-item">
                <div className="series-list-main">
                  <strong>{AUDIT[entry.action] ? t(AUDIT[entry.action]) : entry.action}</strong>
                  {detail(entry) && <span className="muted">{detail(entry)}</span>}
                </div>
                <span className="subtle tabular">{formatDateTime(entry.created_at)}</span>
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted">{t("Aucune décision enregistrée.")}</p>
        )}
      </Card>
    </div>
  );
}
