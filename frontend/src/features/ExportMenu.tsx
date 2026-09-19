import { useState } from "react";
import { registerTranslations, useI18n } from "../i18n";
import type { Project } from "../types";
import { Button, Checkbox, Dialog, Field, Menu, Select } from "../ui";
import type { MenuEntry } from "../ui";

registerTranslations({
  Exporter: "Export",
  "Formats d’export": "Export formats",
  "EPUB traduit": "Translated EPUB",
  Texte: "Text",
  "Projet complet (.zip)": "Complete project (.zip)",
  "EPUB partiel · originaux conservés": "Partial EPUB · originals retained",
  "Rapport de couverture": "Coverage report",
  "Chapitres (.zip, un fichier par chapitre)": "Chapters (.zip, one file per chapter)",
  "Texte consolidé (.txt)": "Consolidated text (.txt)",
  "Options d’export…": "Export options…",
  "Options d’export": "Export options",
  "Choisissez le format, puis ce que l’export doit faire des passages non traduits.":
    "Choose the format, then what the export does with untranslated passages.",
  Format: "Format",
  "Compléter avec le texte original": "Fill in with the original text",
  "Les passages non traduits gardent leur texte source ; le manifeste du ZIP signale les chapitres incomplets.":
    "Untranslated passages keep their source text; the ZIP manifest flags the incomplete chapters.",
  "Les passages non traduits gardent leur texte source.": "Untranslated passages keep their source text.",
  "Ajouter le texte consolidé au ZIP": "Add the consolidated text to the ZIP",
  "Un fichier avec tous les chapitres sous leur titre, à côté des fichiers par chapitre.":
    "One file with every chapter under its heading, next to the per-chapter files.",
  Annuler: "Cancel",
});

export type ExportFormat = "epub" | "txt" | "txt-zip" | "md" | "bible" | "project";
export interface ExportOptions {
  allowSource?: boolean;
  consolidated?: boolean;
}

/** Formats offered for a volume: EPUB volumes rebuild their EPUB, TXT and JSON volumes export text files. */
export function exportFormats(project: Project): ExportFormat[] {
  return project.source_format && project.source_format !== "epub"
    ? ["txt-zip", "txt", "md", "bible", "project"]
    : ["epub", "txt", "md", "bible", "project"];
}

export function exportPath(id: string, format: ExportFormat, options: ExportOptions = {}) {
  const query = new URLSearchParams();
  if (options.allowSource && format !== "bible" && format !== "project") query.set("allow_source", "true");
  if (options.consolidated && format === "txt-zip") query.set("consolidated", "true");
  const search = query.toString();
  return `/projects/${id}/export/${format}${search ? `?${search}` : ""}`;
}

export function exportName(title: string, format: ExportFormat) {
  const suffix: Record<ExportFormat, string> = {
    epub: ".epub",
    txt: ".txt",
    "txt-zip": " - chapitres.zip",
    md: ".md",
    bible: ".json",
    project: ".zip",
  };
  return `${title}${suffix[format]}`;
}

export function ExportMenu({
  project,
  running,
  onExport,
}: {
  project: Project;
  running: boolean;
  onExport: (format: ExportFormat, options?: ExportOptions) => void;
}) {
  const { t } = useI18n();
  const formats = exportFormats(project);
  const [open, setOpen] = useState(false);
  const [format, setFormat] = useState<ExportFormat>(formats[0]);
  const [allowSource, setAllowSource] = useState(false);
  const [consolidated, setConsolidated] = useState(false);
  const labels: Record<ExportFormat, string> = {
    epub: t("EPUB traduit"),
    txt: formats.includes("txt-zip") ? t("Texte consolidé (.txt)") : t("Texte"),
    "txt-zip": t("Chapitres (.zip, un fichier par chapitre)"),
    md: "Markdown",
    bible: "Book Bible JSON",
    project: t("Projet complet (.zip)"),
  };
  const partialAllowed = format !== "bible" && format !== "project";
  const items: MenuEntry[] = [
    ...formats.map((value) => ({
      label: labels[value],
      icon: "file" as const,
      onSelect: () => onExport(value),
    })),
    ...(formats.includes("epub")
      ? [
          {
            label: t("EPUB partiel · originaux conservés"),
            icon: "file" as const,
            onSelect: () => onExport("epub", { allowSource: true }),
          },
        ]
      : []),
    { label: t("Options d’export…"), icon: "settings", onSelect: () => setOpen(true) },
    { kind: "separator" },
    { label: t("Rapport de couverture"), icon: "external", href: `/api/projects/${project.id}/coverage`, target: "_blank" },
  ];
  return (
    <>
      <Menu
        label={t("Formats d’export")}
        trigger={(props) => (
          <Button {...props} icon="download" iconAfter="chevronDown" loading={running}>
            {t("Exporter")}
          </Button>
        )}
        items={items}
      />
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title={t("Options d’export")}
        description={t("Choisissez le format, puis ce que l’export doit faire des passages non traduits.")}
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              {t("Annuler")}
            </Button>
            <Button
              variant="primary"
              icon="download"
              loading={running}
              onClick={() => {
                setOpen(false);
                onExport(format, { allowSource: partialAllowed && allowSource, consolidated });
              }}
            >
              {t("Exporter")}
            </Button>
          </>
        }
      >
        <div className="stack">
          <Field label={t("Format")}>
            <Select value={format} onChange={(event) => setFormat(event.target.value as ExportFormat)}>
              {formats.map((value) => (
                <option key={value} value={value}>
                  {labels[value]}
                </option>
              ))}
            </Select>
          </Field>
          <Checkbox
            label={t("Compléter avec le texte original")}
            description={
              format === "txt-zip"
                ? t(
                    "Les passages non traduits gardent leur texte source ; le manifeste du ZIP signale les chapitres incomplets.",
                  )
                : t("Les passages non traduits gardent leur texte source.")
            }
            checked={partialAllowed && allowSource}
            disabled={!partialAllowed}
            onChange={(event) => setAllowSource(event.target.checked)}
          />
          {format === "txt-zip" && (
            <Checkbox
              label={t("Ajouter le texte consolidé au ZIP")}
              description={t("Un fichier avec tous les chapitres sous leur titre, à côté des fichiers par chapitre.")}
              checked={consolidated}
              onChange={(event) => setConsolidated(event.target.checked)}
            />
          )}
        </div>
      </Dialog>
    </>
  );
}
