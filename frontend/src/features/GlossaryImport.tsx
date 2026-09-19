import { useState } from "react";
import { api, downloadGet } from "../api";
import { formatNumber, registerTranslations, useI18n } from "../i18n";
import type { GlossaryField, GlossaryImportReport, GlossaryImportStrategy, Run } from "../types";
import { Badge, Button, Callout, Checkbox, Dialog, Field, FileButton, FormGrid, Menu, Select, Table } from "../ui";
import type { MenuEntry } from "../ui";

registerTranslations({
  "Importer un glossaire": "Import a glossary",
  "Aperçu de l’import": "Import preview",
  "Rien n’est enregistré avant « Appliquer l’import ».": "Nothing is saved before “Apply the import”.",
  "Appliquer l’import": "Apply the import",
  Annuler: "Cancel",
  "Format {format}": "{format} format",
  "encodage {encoding}": "{encoding} encoding",
  "séparateur {delimiter}": "separator {delimiter}",
  tabulation: "tab",
  "{count} terme(s) lu(s)": "{count} term(s) read",
  "{count} nouveau(x)": "{count} new",
  "{count} identique(s)": "{count} unchanged",
  "{count} conflit(s)": "{count} conflict(s)",
  "{count} doublon(s)": "{count} duplicate(s)",
  "{count} ligne(s) invalide(s)": "{count} invalid row(s)",
  Colonnes: "Columns",
  "Vérifiez quelle colonne du fichier contient chaque information.":
    "Check which column of the file holds each piece of information.",
  Séparateur: "Separator",
  Automatique: "Automatic",
  "Point-virgule (;)": "Semicolon (;)",
  "Virgule (,)": "Comma (,)",
  Tabulation: "Tab",
  "La première ligne contient les noms de colonnes": "The first row holds the column names",
  "Colonne {number} · {name}": "Column {number} · {name}",
  "Colonne {number}": "Column {number}",
  "Non importée": "Not imported",
  "Expression source": "Source expression",
  Traduction: "Translation",
  Catégorie: "Category",
  Description: "Description",
  Verrouillé: "Locked",
  Accepté: "Accepted",
  "En cas de conflit": "On conflict",
  "Garder les termes en place": "Keep the terms in place",
  "Remplacer les termes non verrouillés": "Replace unlocked terms",
  "Tout remplacer, termes verrouillés compris": "Replace everything, locked terms included",
  "Un terme verrouillé n’est remplacé que si vous choisissez « Tout remplacer ».":
    "A locked term is only replaced if you choose “Replace everything”.",
  Conflits: "Conflicts",
  "Terme en place": "Term in place",
  "Dans le fichier": "In the file",
  Décision: "Decision",
  Remplacé: "Replaced",
  Conservé: "Kept",
  "Verrouillé en place": "Locked in place",
  "Lignes invalides (ignorées)": "Invalid rows (left out)",
  "Ligne {line} : {message}": "Row {line}: {message}",
  "Doublons dans le fichier (seule la première ligne compte)": "Duplicates in the file (only the first row counts)",
  "Ligne {line} : « {source} », déjà ligne {first}": "Row {line}: “{source}”, already row {first}",
  "Nouveaux termes": "New terms",
  "… et {count} autre(s).": "… and {count} more.",
  "La liste est tronquée aux 200 premiers éléments.": "The list is cut to the first 200 items.",
  "Aucun changement à appliquer.": "No change to apply.",
  "{imported} terme(s) ajouté(s), {replaced} remplacé(s), {skipped} ignoré(s).":
    "{imported} term(s) added, {replaced} replaced, {skipped} left out.",
  Exporter: "Export",
  "Exporter le glossaire": "Export the glossary",
  "CSV (tableur, point-virgule)": "CSV (spreadsheet, semicolon)",
});

const FIELDS: [GlossaryField, string][] = [
  ["source", "Expression source"],
  ["translation", "Traduction"],
  ["category", "Catégorie"],
  ["description", "Description"],
  ["locked", "Verrouillé"],
  ["accepted", "Accepté"],
];
const DELIMITERS: [string, string][] = [
  ["", "Automatique"],
  ["semicolon", "Point-virgule (;)"],
  ["comma", "Virgule (,)"],
  ["tab", "Tabulation"],
];
const STRATEGIES: [GlossaryImportStrategy, string][] = [
  ["skip", "Garder les termes en place"],
  ["replace", "Remplacer les termes non verrouillés"],
  ["replace_all", "Tout remplacer, termes verrouillés compris"],
];

interface Choices {
  strategy: GlossaryImportStrategy;
  delimiter: string;
  mapping: Partial<Record<GlossaryField, number>> | null;
  header: boolean | null;
}

/** Summary of an applied import, for the caller's status line. */
export function importSummary(t: ReturnType<typeof useI18n>["t"], result: GlossaryImportReport) {
  return t("{imported} terme(s) ajouté(s), {replaced} remplacé(s), {skipped} ignoré(s).", {
    imported: result.imported ?? 0,
    replaced: result.replaced ?? 0,
    skipped: result.skipped ?? 0,
  });
}

/**
 * Import of a JSON, CSV or TBX glossary in two steps: a preview (detected format, columns,
 * conflicts with the terms in place, duplicates, invalid rows), then the import itself.
 * `endpoint` is the import route; its preview is `${endpoint}/preview`.
 */
export function GlossaryImport({
  endpoint,
  run,
  onImported,
  label,
}: {
  endpoint: string;
  run: Run;
  onImported: (result: GlossaryImportReport) => void | Promise<void>;
  label?: string;
}) {
  const { t } = useI18n();
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<GlossaryImportReport | null>(null);
  const [choices, setChoices] = useState<Choices>({ strategy: "skip", delimiter: "", mapping: null, header: null });

  function body(chosen: File, values: Choices, apply: boolean) {
    const data = new FormData();
    data.append("file", chosen);
    data.append("strategy", values.strategy);
    if (values.delimiter) data.append("delimiter", values.delimiter);
    if (values.mapping) data.append("mapping", JSON.stringify(values.mapping));
    if (values.header !== null) data.append("header", String(values.header));
    // The preview listed the invalid rows: applying leaves them out instead of refusing the file.
    if (apply) data.append("skip_invalid", "true");
    return data;
  }
  function preview(chosen: File, values: Choices) {
    setChoices(values);
    void run(async () => {
      setReport(
        await api<GlossaryImportReport>(`${endpoint}/preview`, { method: "POST", body: body(chosen, values, false) }),
      );
      setFile(chosen);
    });
  }
  function close() {
    setFile(null);
    setReport(null);
  }
  const counts = report?.counts;
  const changes = counts ? counts.new + counts.replaced : 0;
  const columns = report?.columns || [];
  const mapping = choices.mapping || report?.mapping || {};
  const delimiterName = (value: string | null) =>
    value === "\t" ? t("tabulation") : value ? `« ${value} »` : "";
  return (
    <>
      <FileButton
        label={label || t("Importer un glossaire")}
        accept=".json,.csv,.tbx,.xml,.tsv,.txt"
        onFiles={([chosen]) => preview(chosen, { strategy: "skip", delimiter: "", mapping: null, header: null })}
      />
      <Dialog
        open={!!(file && report)}
        onClose={close}
        size="xl"
        title={t("Aperçu de l’import")}
        description={t("Rien n’est enregistré avant « Appliquer l’import ».")}
        footer={
          <>
            <Button variant="ghost" onClick={close}>
              {t("Annuler")}
            </Button>
            <Button
              variant="primary"
              icon="check"
              disabled={!changes}
              onClick={() =>
                file &&
                void run(async () => {
                  const result = await api<GlossaryImportReport>(endpoint, {
                    method: "POST",
                    body: body(file, choices, true),
                  });
                  close();
                  await onImported(result);
                })
              }
            >
              {t("Appliquer l’import")}
            </Button>
          </>
        }
      >
        {report && counts && file && (
          <div className="stack glossary-import">
            <p className="muted">
              {[
                file.name,
                t("Format {format}", { format: report.format.toUpperCase() }),
                report.format === "csv" ? t("encodage {encoding}", { encoding: report.encoding }) : "",
                report.delimiter ? t("séparateur {delimiter}", { delimiter: delimiterName(report.delimiter) }) : "",
                t("{count} terme(s) lu(s)", { count: formatNumber(counts.terms) }),
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
            <div className="glossary-import-counts" role="list">
              <Badge tone="success">{t("{count} nouveau(x)", { count: counts.new })}</Badge>
              <Badge tone="neutral">{t("{count} identique(s)", { count: counts.unchanged })}</Badge>
              <Badge tone={counts.conflicts ? "warning" : "neutral"}>
                {t("{count} conflit(s)", { count: counts.conflicts })}
              </Badge>
              <Badge tone={counts.duplicates ? "warning" : "neutral"}>
                {t("{count} doublon(s)", { count: counts.duplicates })}
              </Badge>
              <Badge tone={counts.errors ? "danger" : "neutral"}>
                {t("{count} ligne(s) invalide(s)", { count: counts.errors })}
              </Badge>
            </div>
            {report.format === "csv" && (
              <fieldset className="fieldset glossary-import-columns">
                <legend>{t("Colonnes")}</legend>
                <p className="field-hint">{t("Vérifiez quelle colonne du fichier contient chaque information.")}</p>
                <FormGrid columns={3}>
                  {FIELDS.map(([field, name]) => (
                    <Field key={field} label={t(name)}>
                      <Select
                        value={mapping[field] ?? ""}
                        onChange={(event) => {
                          const next = { ...mapping };
                          if (event.target.value === "") delete next[field];
                          else next[field] = Number(event.target.value);
                          preview(file, { ...choices, mapping: next, header: choices.header ?? report.header });
                        }}
                      >
                        {field !== "source" && field !== "translation" && <option value="">{t("Non importée")}</option>}
                        {columns.map((column, index) => (
                          <option key={index} value={index}>
                            {column
                              ? t("Colonne {number} · {name}", { number: index + 1, name: column })
                              : t("Colonne {number}", { number: index + 1 })}
                          </option>
                        ))}
                      </Select>
                    </Field>
                  ))}
                  <Field label={t("Séparateur")}>
                    <Select
                      value={choices.delimiter}
                      onChange={(event) => preview(file, { ...choices, delimiter: event.target.value, mapping: null })}
                    >
                      {DELIMITERS.map(([value, name]) => (
                        <option key={value} value={value}>
                          {t(name)}
                        </option>
                      ))}
                    </Select>
                  </Field>
                </FormGrid>
                <Checkbox
                  label={t("La première ligne contient les noms de colonnes")}
                  checked={choices.header ?? report.header}
                  onChange={(event) =>
                    preview(file, { ...choices, mapping: { ...mapping }, header: event.target.checked })
                  }
                />
              </fieldset>
            )}
            <Field
              label={t("En cas de conflit")}
              hint={t("Un terme verrouillé n’est remplacé que si vous choisissez « Tout remplacer ».")}
            >
              <Select
                value={choices.strategy}
                onChange={(event) =>
                  preview(file, { ...choices, strategy: event.target.value as GlossaryImportStrategy })
                }
              >
                {STRATEGIES.map(([value, name]) => (
                  <option key={value} value={value}>
                    {t(name)}
                  </option>
                ))}
              </Select>
            </Field>
            {report.truncated && <Callout tone="info">{t("La liste est tronquée aux 200 premiers éléments.")}</Callout>}
            {!changes && <Callout tone="neutral">{t("Aucun changement à appliquer.")}</Callout>}
            {!!report.conflicts.length && (
              <section className="stack-sm">
                <h3>{t("Conflits")}</h3>
                <Table className="glossary-import-table" label={t("Conflits")}>
                  <thead>
                    <tr>
                      <th>{t("Expression source")}</th>
                      <th>{t("Terme en place")}</th>
                      <th>{t("Dans le fichier")}</th>
                      <th>{t("Décision")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.conflicts.map((conflict) => (
                      <tr key={conflict.line}>
                        <td>{conflict.source}</td>
                        <td>
                          {conflict.existing.translation}
                          {conflict.locked && (
                            <>
                              {" "}
                              <Badge tone="accent">{t("Verrouillé en place")}</Badge>
                            </>
                          )}
                        </td>
                        <td>{conflict.incoming.translation}</td>
                        <td>
                          <Badge tone={conflict.action === "replace" ? "warning" : "neutral"}>
                            {conflict.action === "replace" ? t("Remplacé") : t("Conservé")}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </section>
            )}
            {!!report.errors.length && (
              <section className="stack-sm">
                <h3>{t("Lignes invalides (ignorées)")}</h3>
                <ul className="glossary-import-list">
                  {report.errors.map((error) => (
                    <li key={error.line}>{t("Ligne {line} : {message}", { line: error.line, message: error.message })}</li>
                  ))}
                </ul>
              </section>
            )}
            {!!report.duplicates.length && (
              <section className="stack-sm">
                <h3>{t("Doublons dans le fichier (seule la première ligne compte)")}</h3>
                <ul className="glossary-import-list">
                  {report.duplicates.map((item) => (
                    <li key={item.line}>
                      {t("Ligne {line} : « {source} », déjà ligne {first}", {
                        line: item.line,
                        source: item.source,
                        first: item.first_line,
                      })}
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {!!report.new.length && (
              <section className="stack-sm">
                <h3>{t("Nouveaux termes")}</h3>
                <ul className="glossary-import-list">
                  {report.new.slice(0, 20).map((term) => (
                    <li key={term.line}>
                      <strong>{term.source}</strong> → {term.translation}
                      {term.locked && (
                        <>
                          {" "}
                          <Badge tone="accent">{t("Verrouillé")}</Badge>
                        </>
                      )}
                    </li>
                  ))}
                </ul>
                {counts.new > 20 && <p className="muted">{t("… et {count} autre(s).", { count: counts.new - 20 })}</p>}
              </section>
            )}
          </div>
        )}
      </Dialog>
    </>
  );
}

/** Export menu of a glossary: JSON, CSV, CSV for spreadsheets (semicolons and a byte order mark), TBX. */
export function GlossaryExport({ base, name, run }: { base: string; name: string; run: Run }) {
  const { t } = useI18n();
  const items: MenuEntry[] = [
    ["json", "JSON", ""],
    ["csv", "CSV", ""],
    ["csv", t("CSV (tableur, point-virgule)"), "?delimiter=semicolon&bom=true"],
    ["tbx", "TBX", ""],
  ].map(([format, label, query]) => ({
    label,
    icon: "file" as const,
    onSelect: () => void run(() => downloadGet(`${base}/export/${format}${query}`, `${name}.${format}`)),
  }));
  return (
    <Menu
      label={t("Exporter le glossaire")}
      trigger={(props) => (
        <Button {...props} icon="download" iconAfter="chevronDown">
          {t("Exporter")}
        </Button>
      )}
      items={items}
    />
  );
}
