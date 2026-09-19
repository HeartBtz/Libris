import { useCallback, useEffect, useState } from "react";
import { api, send } from "../api";
import { formatDateTime, registerTranslations, useI18n } from "../i18n";
import type { Run, SharedGlossary, SharedGlossaryTerm } from "../types";
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
  Page,
  PageHeader,
  SearchInput,
  TextArea,
  useDialogs,
  useToast,
} from "../ui";
import { GlossaryExport, GlossaryImport, importSummary } from "./GlossaryImport";

registerTranslations({
  "Glossaires partagés": "Shared glossaries",
  "Une terminologie commune à plusieurs séries d’un même univers. Chaque série peut suivre un glossaire partagé ; le livre et la série restent prioritaires.":
    "One terminology for several series of the same universe. Each series can follow a shared glossary; the book and the series still come first.",
  "Nouveau glossaire": "New glossary",
  "Aucun glossaire partagé.": "No shared glossary.",
  "Créez-en un, puis rattachez-y des séries depuis leur onglet Glossaire.":
    "Create one, then attach series to it from their Glossary tab.",
  "{count} terme(s) · {locked} verrouillé(s)": "{count} term(s) · {locked} locked",
  "Aucune série": "No series",
  "{count} série(s)": "{count} series",
  Nom: "Name",
  Description: "Description",
  "Langue source (facultatif)": "Source language (optional)",
  "Langue cible (facultatif)": "Target language (optional)",
  "Avec des langues, le glossaire ne s’applique qu’aux volumes de la même paire de langues.":
    "With languages, the glossary only applies to volumes of the same language pair.",
  Créer: "Create",
  Enregistrer: "Save",
  Annuler: "Cancel",
  "Modifier le glossaire": "Edit the glossary",
  "Supprimer le glossaire": "Delete the glossary",
  "Supprimer le glossaire « {name} » ?": "Delete the glossary “{name}”?",
  "Ses termes sont supprimés et les séries qui le suivent n’en reçoivent plus.":
    "Its terms are deleted and the series that follow it no longer receive them.",
  Supprimer: "Delete",
  "Séries qui le suivent": "Series that follow it",
  "Aucune série ne suit ce glossaire. Rattachez-le depuis l’onglet Glossaire d’une série.":
    "No series follows this glossary. Attach it from a series' Glossary tab.",
  Termes: "Terms",
  "Rechercher un terme…": "Search for a term…",
  "Rechercher dans le glossaire partagé": "Search the shared glossary",
  "Aucun terme pour l’instant.": "No term yet.",
  "Aucun terme ne correspond.": "No term matches.",
  "Traduction de {term}": "Translation of {term}",
  "Catégorie de {term}": "Category of {term}",
  "Verrouiller {term}": "Lock {term}",
  "Accepter {term}": "Accept {term}",
  "Enregistrer {term}": "Save {term}",
  "Supprimer {term}": "Delete {term}",
  Verrouillé: "Locked",
  Accepté: "Accepted",
  "Terme enregistré.": "Term saved.",
  "Ajouter un terme": "Add a term",
  "Expression source": "Source expression",
  Traduction: "Translation",
  Catégorie: "Category",
  "Verrouiller ce terme": "Lock this term",
  Ajouter: "Add",
  "Modifié le {date}": "Changed on {date}",
  "Retour à la liste": "Back to the list",
  "Glossaire : {name}": "Glossary: {name}",
});

const EMPTY = { name: "", description: "", source_language: "", target_language: "" };

/** The account's shared glossaries: one terminology for the series of a universe. */
export function SharedGlossaries({ run }: { run: Run }) {
  const { t } = useI18n();
  const [glossaries, setGlossaries] = useState<SharedGlossary[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const load = useCallback(async () => setGlossaries(await api<SharedGlossary[]>("/glossaries")), []);
  useEffect(() => {
    void run.background(load);
  }, [run, load]);
  return (
    <Page>
      <PageHeader
        title={t("Glossaires partagés")}
        description={t(
          "Une terminologie commune à plusieurs séries d’un même univers. Chaque série peut suivre un glossaire partagé ; le livre et la série restent prioritaires.",
        )}
        actions={
          <Button variant="primary" icon="plus" onClick={() => setCreating(true)}>
            {t("Nouveau glossaire")}
          </Button>
        }
      />
      {glossaries === null ? (
        <LoadingBlock label={t("Glossaires partagés")} />
      ) : selected ? (
        <GlossaryDetail
          id={selected}
          run={run}
          onBack={() => {
            setSelected(null);
            void run.background(load);
          }}
        />
      ) : !glossaries.length ? (
        <EmptyState
          icon="book"
          title={t("Aucun glossaire partagé.")}
          description={t("Créez-en un, puis rattachez-y des séries depuis leur onglet Glossaire.")}
        />
      ) : (
        <Card padded={false}>
          <ul className="series-list shared-glossary-list">
            {glossaries.map((glossary) => (
              <li key={glossary.id} className="series-list-item">
                <div className="series-list-main">
                  <button type="button" className="link-button shared-glossary-name" onClick={() => setSelected(glossary.id)}>
                    {glossary.name}
                  </button>
                  <span className="subtle">
                    {t("{count} terme(s) · {locked} verrouillé(s)", {
                      count: glossary.term_count,
                      locked: glossary.locked_count,
                    })}
                    {glossary.source_language && glossary.target_language
                      ? ` · ${glossary.source_language} → ${glossary.target_language}`
                      : ""}
                  </span>
                </div>
                <Badge tone={glossary.series.length ? "accent" : "neutral"}>
                  {glossary.series.length ? t("{count} série(s)", { count: glossary.series.length }) : t("Aucune série")}
                </Badge>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {creating && (
      <GlossaryForm
        open={creating}
        title={t("Nouveau glossaire")}
        submitLabel={t("Créer")}
        initial={EMPTY}
        onClose={() => setCreating(false)}
        onSubmit={(values) =>
          run(async () => {
            const created = await send<SharedGlossary>("/glossaries", values);
            setCreating(false);
            await load();
            setSelected(created.id);
          })
        }
      />
      )}
    </Page>
  );
}

type FormValues = typeof EMPTY;

function GlossaryForm({
  open,
  title,
  submitLabel,
  initial,
  onClose,
  onSubmit,
}: {
  open: boolean;
  title: string;
  submitLabel: string;
  initial: FormValues;
  onClose: () => void;
  onSubmit: (values: { name: string; description: string; source_language: string | null; target_language: string | null }) => void;
}) {
  const { t } = useI18n();
  // Mounted only while open: each opening starts from `initial`.
  const [values, setValues] = useState(initial);
  const formId = "shared-glossary-form";
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t("Annuler")}
          </Button>
          <Button variant="primary" type="submit" form={formId}>
            {submitLabel}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit({
            name: values.name,
            description: values.description,
            source_language: values.source_language.trim() || null,
            target_language: values.target_language.trim() || null,
          });
        }}
      >
        <Field label={t("Nom")}>
          <Input
            value={values.name}
            required
            maxLength={200}
            data-autofocus
            onChange={(e) => setValues({ ...values, name: e.target.value })}
          />
        </Field>
        <Field label={t("Description")}>
          <TextArea
            value={values.description}
            maxLength={4000}
            onChange={(e) => setValues({ ...values, description: e.target.value })}
          />
        </Field>
        <FormGrid>
          <Field label={t("Langue source (facultatif)")}>
            <Input
              value={values.source_language}
              maxLength={80}
              placeholder="en"
              onChange={(e) => setValues({ ...values, source_language: e.target.value })}
            />
          </Field>
          <Field label={t("Langue cible (facultatif)")}>
            <Input
              value={values.target_language}
              maxLength={80}
              placeholder="fr"
              onChange={(e) => setValues({ ...values, target_language: e.target.value })}
            />
          </Field>
        </FormGrid>
        <p className="field-hint">
          {t("Avec des langues, le glossaire ne s’applique qu’aux volumes de la même paire de langues.")}
        </p>
      </form>
    </Dialog>
  );
}

function GlossaryDetail({ id, run, onBack }: { id: string; run: Run; onBack: () => void }) {
  const { t } = useI18n();
  const { confirm } = useDialogs();
  const toast = useToast();
  const [glossary, setGlossary] = useState<SharedGlossary | null>(null);
  const [terms, setTerms] = useState<SharedGlossaryTerm[]>([]);
  const [editing, setEditing] = useState(false);
  const [notice, setNotice] = useState("");
  const [search, setSearch] = useState("");
  const [source, setSource] = useState("");
  const [translation, setTranslation] = useState("");
  const [category, setCategory] = useState("");
  const [locked, setLocked] = useState(true);
  const load = useCallback(async () => {
    const data = await api<SharedGlossary>(`/glossaries/${id}`);
    setGlossary(data);
    setTerms(data.terms || []);
  }, [id]);
  useEffect(() => {
    void run.background(load);
  }, [run, load]);
  if (!glossary) return <LoadingBlock label={t("Glossaires partagés")} />;
  const base = `/glossaries/${id}`;
  const update = (termId: string, change: Partial<SharedGlossaryTerm>) =>
    setTerms((all) => all.map((term) => (term.id === termId ? { ...term, ...change } : term)));
  const payload = (term: Omit<SharedGlossaryTerm, "id" | "glossary_id">) => ({
    source: term.source,
    translation: term.translation,
    category: term.category || "autre",
    description: term.description,
    locked: term.locked,
    accepted: term.accepted,
  });
  const needle = search.toLocaleLowerCase();
  const filtered = terms.filter((term) => `${term.source} ${term.translation}`.toLocaleLowerCase().includes(needle));
  return (
    <div className="stack">
      <div>
        <Button variant="ghost" icon="chevronLeft" onClick={onBack}>
          {t("Retour à la liste")}
        </Button>
      </div>
      <Card
        title={t("Glossaire : {name}", { name: glossary.name })}
        description={glossary.description || t("Modifié le {date}", { date: formatDateTime(glossary.updated_at) })}
        actions={
          <div className="row">
            <GlossaryImport
              endpoint={`${base}/import`}
              run={run}
              onImported={async (result) => {
                setNotice(importSummary(t, result));
                await load();
              }}
            />
            <GlossaryExport base={base} name="shared-glossary" run={run} />
            <IconButton icon="edit" label={t("Modifier le glossaire")} onClick={() => setEditing(true)} />
            <IconButton
              icon="trash"
              label={t("Supprimer le glossaire")}
              onClick={() =>
                void (async () => {
                  const accepted = await confirm({
                    title: t("Supprimer le glossaire « {name} » ?", { name: glossary.name }),
                    message: t("Ses termes sont supprimés et les séries qui le suivent n’en reçoivent plus."),
                    confirmLabel: t("Supprimer"),
                    tone: "danger",
                  });
                  if (!accepted) return;
                  await run(async () => {
                    await api(base, { method: "DELETE" });
                    onBack();
                  });
                })()
              }
            />
          </div>
        }
      >
        <h3 className="shared-glossary-subtitle">{t("Séries qui le suivent")}</h3>
        {glossary.series.length ? (
          <ul className="row shared-glossary-series">
            {glossary.series.map((series) => (
              <li key={series.id}>
                <a href={`#series/${series.id}`}>{series.name}</a>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">
            {t("Aucune série ne suit ce glossaire. Rattachez-le depuis l’onglet Glossaire d’une série.")}
          </p>
        )}
      </Card>
      {notice && (
        <Callout tone="success" role="status">
          {notice}
        </Callout>
      )}
      <Card title={t("Termes")} description={t("{count} terme(s) · {locked} verrouillé(s)", { count: terms.length, locked: terms.filter((term) => term.locked).length })}>
        <div className="series-toolbar">
          <label className="library-search">
            <span className="sr-only">{t("Rechercher dans le glossaire partagé")}</span>
            <SearchInput placeholder={t("Rechercher un terme…")} value={search} onChange={(e) => setSearch(e.target.value)} />
          </label>
        </div>
        {!terms.length ? (
          <EmptyState compact icon="book" title={t("Aucun terme pour l’instant.")} />
        ) : !filtered.length ? (
          <EmptyState compact icon="search" title={t("Aucun terme ne correspond.")} />
        ) : (
          <ul className="series-list term-list">
            {filtered.map((term) => (
              <li key={term.id} className="series-list-item">
                <div className="series-list-main">
                  <strong>{term.source}</strong>
                </div>
                <div className="term-fields">
                  <Input
                    aria-label={t("Traduction de {term}", { term: term.source })}
                    value={term.translation}
                    onChange={(e) => update(term.id, { translation: e.target.value })}
                  />
                  <Input
                    aria-label={t("Catégorie de {term}", { term: term.source })}
                    value={term.category}
                    onChange={(e) => update(term.id, { category: e.target.value })}
                  />
                </div>
                <div className="series-list-actions">
                  <Checkbox
                    label={t("Verrouillé")}
                    aria-label={t("Verrouiller {term}", { term: term.source })}
                    checked={term.locked}
                    onChange={(e) => update(term.id, { locked: e.target.checked })}
                  />
                  <Checkbox
                    label={t("Accepté")}
                    aria-label={t("Accepter {term}", { term: term.source })}
                    checked={term.accepted}
                    onChange={(e) => update(term.id, { accepted: e.target.checked })}
                  />
                  <IconButton
                    icon="check"
                    size="sm"
                    label={t("Enregistrer {term}", { term: term.source })}
                    onClick={() =>
                      void run(async () => {
                        await send(`${base}/terms/${term.id}`, payload(term), "PUT");
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
                      void run(async () => {
                        await api(`${base}/terms/${term.id}`, { method: "DELETE" });
                        await load();
                      })
                    }
                  />
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title={t("Ajouter un terme")}>
        <form
          className="inline-form"
          onSubmit={(event) => {
            event.preventDefault();
            void run(async () => {
              await send(
                `${base}/terms`,
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
      {editing && (
      <GlossaryForm
        open={editing}
        title={t("Modifier le glossaire")}
        submitLabel={t("Enregistrer")}
        initial={{
          name: glossary.name,
          description: glossary.description,
          source_language: glossary.source_language || "",
          target_language: glossary.target_language || "",
        }}
        onClose={() => setEditing(false)}
        onSubmit={(values) =>
          run(async () => {
            await send(base, values, "PUT");
            setEditing(false);
            await load();
          })
        }
      />
      )}
    </div>
  );
}
