import { useCallback, useEffect, useState } from "react";
import { api, send } from "../api";
import { registerTranslations, useI18n } from "../i18n";
import type { EffectiveTerm, Run, SharedGlossary } from "../types";
import { Badge, Button, Card, EmptyState, Field, LoadingBlock, Select, Table } from "../ui";

registerTranslations({
  "Glossaire partagé": "Shared glossary",
  "Ordre de priorité : le livre, puis la série, puis le glossaire partagé. Un terme verrouillé d’un niveau plus large l’emporte sur un terme non verrouillé proposé automatiquement ; une dérogation du volume ou une décision de série l’emporte toujours.":
    "Order of precedence: the book, then the series, then the shared glossary. A locked term of a broader level beats an unlocked term proposed automatically; a volume override or a series decision always wins.",
  "Glossaire partagé suivi": "Shared glossary followed",
  Aucun: "None",
  Enregistrer: "Save",
  "Cette série ne suit aucun glossaire partagé.": "This series follows no shared glossary.",
  "Cette série suit « {name} » : {count} terme(s), {locked} verrouillé(s).":
    "This series follows “{name}”: {count} term(s), {locked} locked.",
  "Gérer les glossaires partagés": "Manage shared glossaries",
  "Glossaire partagé enregistré.": "Shared glossary saved.",
  "Termes appliqués à ce livre": "Terms applied to this book",
  "Tous les termes que la traduction reçoit, avec leur niveau d’origine et ce qu’ils remplacent.":
    "Every term the translation receives, with the level it comes from and what it replaces.",
  "Aucun terme hérité de la série ou d’un glossaire partagé.": "No term inherited from the series or a shared glossary.",
  Source: "Source",
  "Traduction appliquée": "Translation applied",
  Niveau: "Level",
  Remplace: "Replaces",
  Livre: "Book",
  Série: "Series",
  "Vol. {volume}": "Vol. {volume}",
  "Décision de série": "Series decision",
  Dérogation: "Override",
  Verrouillé: "Locked",
  "Chargement…": "Loading…",
});

/** The shared glossary a series follows, chosen by its owner. */
export function SeriesSharedGlossary({ seriesId, owner, run }: { seriesId: string; owner: boolean; run: Run }) {
  const { t } = useI18n();
  const [current, setCurrent] = useState<SharedGlossary | null | undefined>(undefined);
  const [choices, setChoices] = useState<SharedGlossary[]>([]);
  const [chosen, setChosen] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(async () => {
    const data = await api<{ glossary: SharedGlossary | null }>(`/series/${seriesId}/shared-glossary`);
    setCurrent(data.glossary);
    setChosen(data.glossary?.id || "");
    if (owner) setChoices(await api<SharedGlossary[]>("/glossaries"));
  }, [seriesId, owner]);
  useEffect(() => {
    void run.background(load);
  }, [run, load]);
  return (
    <Card
      title={t("Glossaire partagé")}
      description={t(
        "Ordre de priorité : le livre, puis la série, puis le glossaire partagé. Un terme verrouillé d’un niveau plus large l’emporte sur un terme non verrouillé proposé automatiquement ; une dérogation du volume ou une décision de série l’emporte toujours.",
      )}
    >
      {current === undefined ? (
        <LoadingBlock label={t("Chargement…")} lines={1} />
      ) : (
        <div className="stack-sm">
          <p className="muted" role="status">
            {notice ||
              (current
                ? t("Cette série suit « {name} » : {count} terme(s), {locked} verrouillé(s).", {
                    name: current.name,
                    count: current.term_count,
                    locked: current.locked_count,
                  })
                : t("Cette série ne suit aucun glossaire partagé."))}
          </p>
          {owner && (
            <form
              className="inline-form"
              onSubmit={(event) => {
                event.preventDefault();
                void run(async () => {
                  await send(`/series/${seriesId}/shared-glossary`, { glossary_id: chosen || null }, "PUT");
                  await load();
                  setNotice(t("Glossaire partagé enregistré."));
                });
              }}
            >
              <Field label={t("Glossaire partagé suivi")}>
                <Select value={chosen} onChange={(event) => setChosen(event.target.value)}>
                  <option value="">{t("Aucun")}</option>
                  {choices.map((glossary) => (
                    <option key={glossary.id} value={glossary.id}>
                      {glossary.name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Button type="submit" variant="secondary" icon="check" disabled={chosen === (current?.id || "")}>
                {t("Enregistrer")}
              </Button>
              <a className="btn btn-md btn-ghost" href="#glossaries">
                {t("Gérer les glossaires partagés")}
              </a>
            </form>
          )}
        </div>
      )}
    </Card>
  );
}

/** What a book inherits from its series and shared glossary, and which level wins for each term. */
export function EffectiveGlossary({ projectId, run, tick }: { projectId: string; run: Run; tick: number }) {
  const { t } = useI18n();
  const [terms, setTerms] = useState<EffectiveTerm[] | null>(null);
  const load = useCallback(async () => {
    const data = await api<{ terms: EffectiveTerm[] }>(`/projects/${projectId}/glossary/effective`);
    setTerms(data.terms);
  }, [projectId]);
  useEffect(() => {
    void run.background(load);
  }, [run, load, tick]);
  const level = (entry: Pick<EffectiveTerm, "level" | "origin" | "volume">) =>
    entry.level === "book"
      ? entry.origin === "series_override"
        ? t("Dérogation")
        : t("Livre")
      : entry.level === "shared"
        ? t("Glossaire partagé")
        : entry.origin === "series_decision"
          ? t("Décision de série")
          : entry.volume
            ? t("Vol. {volume}", { volume: entry.volume })
            : t("Série");
  // The book's own terms are listed above; this card shows what comes from elsewhere or was replaced.
  const shown = (terms || []).filter((term) => term.level !== "book" || term.overridden.length);
  return (
    <Card
      padded={false}
      title={t("Termes appliqués à ce livre")}
      description={t("Tous les termes que la traduction reçoit, avec leur niveau d’origine et ce qu’ils remplacent.")}
    >
      {terms === null ? (
        <div className="card-inset">
          <LoadingBlock label={t("Chargement…")} lines={2} />
        </div>
      ) : !shown.length ? (
        <EmptyState compact icon="book" title={t("Aucun terme hérité de la série ou d’un glossaire partagé.")} />
      ) : (
        <Table className="effective-glossary" label={t("Termes appliqués à ce livre")}>
          <thead>
            <tr>
              <th>{t("Source")}</th>
              <th>{t("Traduction appliquée")}</th>
              <th>{t("Niveau")}</th>
              <th>{t("Remplace")}</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((term) => (
              <tr key={term.source}>
                <td>{term.source}</td>
                <td>
                  {term.translation}
                  {term.locked && (
                    <>
                      {" "}
                      <Badge tone="accent">{t("Verrouillé")}</Badge>
                    </>
                  )}
                </td>
                <td>
                  <Badge tone={term.level === "shared" ? "info" : term.level === "series" ? "neutral" : "success"}>
                    {level(term)}
                  </Badge>
                </td>
                <td className="muted">
                  {term.overridden.map((entry) => `${entry.translation} (${level(entry)})`).join(" · ") || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}
