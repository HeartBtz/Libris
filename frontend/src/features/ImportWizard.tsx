import { useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent, ReactNode } from "react";
import { api, ApiError, send } from "../api";
import { formatNumber, registerTranslations, useI18n } from "../i18n";
import type {
  ChapterProposal,
  Confidence,
  ContextBackend,
  FileInspection,
  ImportFormat,
  ImportResult,
  ImportSession,
  Project,
  ProviderSummary,
  Quality,
  Run,
  Series,
  SeriesChapter,
  SeriesDetail,
  VolumeProposal,
} from "../types";
import {
  Badge,
  Button,
  Callout,
  Checkbox,
  Dialog,
  Field,
  FileButton,
  FormGrid,
  Icon,
  IconButton,
  Input,
  ProgressBar,
  Select,
  cx,
  useDialogs,
} from "../ui";

registerTranslations({
  "Suite d’un volume : seuls les chapitres nouveaux ou remplacés sont traduits et relus. Les chapitres déjà traduits servent de contexte, sans nouvel appel au modèle.":
    "Follow-up of a volume: only new or replaced chapters are translated and reviewed. Chapters already translated serve as context, with no new model call.",
  "Ajouter du contenu": "Add content",
  "Étapes de l’import": "Import steps",
  "Étape {current} sur {total}": "Step {current} of {total}",
  Format: "Format",
  Destination: "Destination",
  Fichiers: "Files",
  Fichier: "File",
  Titre: "Title",
  "Pré-analyse": "Pre-analysis",
  Confirmation: "Confirmation",
  Terminé: "Done",
  "Que voulez-vous ajouter ?": "What do you want to add?",
  "Livres EPUB": "EPUB books",
  "Un ou plusieurs EPUB : les volumes d’une série ou des volumes uniques.":
    "One or more EPUBs: the volumes of a series or standalone volumes.",
  "Chapitres TXT (webnovel)": "TXT chapters (webnovel)",
  "Un fichier texte par chapitre, toujours rattaché à une série.": "One text file per chapter, always attached to a series.",
  "Restaurer une archive Libris": "Restore a Libris archive",
  "Un projet exporté depuis Libris (.zip), avec tout son travail.": "A project exported from Libris (.zip), with all its work.",
  "Automatisation : l’API JSON reçoit des chapitres envoyés par un script.":
    "Automation: the JSON API receives chapters sent by a script.",
  "Chaque compte crée ses propres jetons.": "Every account creates its own tokens.",
  "Créer un jeton dans Mon compte › Jetons d’API": "Create a token in My account › API tokens",
  "Où ranger ces livres ?": "Where should these books go?",
  "Série existante": "Existing series",
  "Les volumes rejoignent une série de votre bibliothèque.": "The volumes join a series of your library.",
  "Nouvelle série": "New series",
  "Une série est créée pour ces fichiers.": "A series is created for these files.",
  "Volume unique": "Standalone volume",
  "Chaque EPUB devient un livre indépendant, sans série.": "Each EPUB becomes an independent book, without a series.",
  "Choisir la série": "Choose the series",
  "Choisissez une série…": "Choose a series…",
  "Aucune série pour l’instant.": "No series yet.",
  "Nom de la série": "Series name",
  "Laissez vide pour reprendre le nom détecté dans les fichiers.": "Leave empty to use the name detected in the files.",
  "Des chapitres TXT appartiennent toujours à une série : choisissez-la ou créez-la.":
    "TXT chapters always belong to a series: choose it or create it.",
  "Série des chapitres": "Series of the chapters",
  "Où ajouter les chapitres ?": "Where should the chapters go?",
  "Flux continu de la série": "Continuous flow of the series",
  "Les chapitres s’ajoutent à la suite, sans découpage en volumes.": "Chapters are appended in order, without volumes.",
  "Le flux continu existe : les chapitres y sont ajoutés.": "The continuous flow exists: the chapters are added to it.",
  "Le flux continu sera créé.": "The continuous flow will be created.",
  "Volume existant": "Existing volume",
  "Un volume texte de la série.": "A text volume of the series.",
  "Choisissez un volume…": "Choose a volume…",
  "Cette série n’a pas encore de volume texte.": "This series has no text volume yet.",
  "Nouveau volume": "New volume",
  "Un volume numéroté est créé dans la série.": "A numbered volume is created in the series.",
  "Numéro du volume": "Volume number",
  "Titre du volume": "Volume title",
  "Facultatif": "Optional",
  "Glissez-déposez vos fichiers {format} ici": "Drag and drop your {format} files here",
  "ou choisissez-les sur cet appareil. Rien n’est importé avant la confirmation.":
    "or choose them on this device. Nothing is imported before the confirmation.",
  "Choisir des fichiers": "Choose files",
  "Choisir l’archive (.zip)": "Choose the archive (.zip)",
  "Restauration en cours…": "Restoring…",
  "Le projet et son travail sont recréés tels qu’exportés ; les membres et le provider ne sont pas restaurés.":
    "The project and its work are recreated as exported; members and provider are not restored.",
  "{done}/{total} fichiers analysés": "{done}/{total} files analyzed",
  "Analyse des fichiers": "File analysis",
  "En attente": "Pending",
  "Envoi et analyse…": "Uploading and analyzing…",
  Analysé: "Analyzed",
  "Non envoyé": "Not uploaded",
  Échec: "Failed",
  "Retirer {name}": "Remove {name}",
  "Ce fichier n’est pas un {format} : il n’est pas envoyé.": "This file is not a {format}: it is not uploaded.",
  "Ce fichier est déjà dans la liste.": "This file is already in the list.",
  "Déjà dans votre bibliothèque : « {title} ».": "Already in your library: “{title}”.",
  "Doublon de « {name} » dans cet import.": "Duplicate of “{name}” in this import.",
  "chapitre {number}": "chapter {number}",
  "volume {number}": "volume {number}",
  "Série déclarée : {series}": "Declared series: {series}",
  "Confiance élevée": "High confidence",
  "Confiance moyenne": "Medium confidence",
  "Confiance faible": "Low confidence",
  "Série existante : les volumes y seront ajoutés.": "Existing series: the volumes will be added to it.",
  "Une nouvelle série sera créée.": "A new series will be created.",
  "Nom proposé : {reason}": "Suggested name: {reason}",
  "Chaque EPUB deviendra un volume unique, sans série.": "Each EPUB will become a standalone volume, without a series.",
  "Utiliser la première ligne comme titre": "Use the first line as the title",
  "Le titre de chaque chapitre est la première ligne de son fichier, qui n’est plus répétée dans le texte.":
    "Each chapter's title is the first line of its file, no longer repeated in the text.",
  "Renuméroter dans cet ordre": "Renumber in this order",
  "Trier par numéro": "Sort by number",
  "Monter {name}": "Move {name} up",
  "Descendre {name}": "Move {name} down",
  "Titre de {name}": "Title of {name}",
  "Numéro de volume de {name}": "Volume number of {name}",
  "Numéro de chapitre de {name}": "Chapter number of {name}",
  "N° vol.": "Vol. no.",
  "N° ch.": "Ch. no.",
  "Confirmer le numéro": "Confirm the number",
  "Confirmer : pas de numéro": "Confirm: no number",
  Ignorer: "Skip",
  "Ignorer ce fichier": "Skip this file",
  Remplacer: "Replace",
  "Remplacer le chapitre existant « {title} »": "Replace the existing chapter “{title}”",
  "Ignoré à l’import.": "Skipped at import.",
  "Indiquez un numéro de volume.": "Enter a volume number.",
  "Numéro invalide.": "Invalid number.",
  "Numéro en double dans ce lot.": "Duplicate number in this batch.",
  "Le volume {number} existe déjà dans la série : « {title} ».": "Volume {number} already exists in the series: “{title}”.",
  "Numéro incertain : confirmez-le ou corrigez-le.": "Uncertain number: confirm or correct it.",
  "Numéro retenu automatiquement ; modifiable ici.": "Number chosen automatically; you can change it here.",
  "Sans numéro : le volume suivant de la série lui sera attribué automatiquement.":
    "No number: it will automatically get the series' next volume.",
  "Sans numéro : chapitre placé automatiquement d’après son nom.": "No number: the chapter is placed automatically from its name.",
  "Chapitres Markdown, HTML ou DOCX": "Markdown, HTML or DOCX chapters",
  "Un fichier par chapitre : titres, paragraphes, listes et citations sont traduits ; le code et les tableaux restent tels quels.":
    "One file per chapter: headings, paragraphs, lists and quotes are translated; code and tables stay as they are.",
  "Format des chapitres": "Chapter format",
  "Des chapitres appartiennent toujours à une série : choisissez-la ou créez-la.":
    "Chapters always belong to a series: choose it or create it.",
  "Taille des passages": "Passage size",
  "Caractères par passage (vide : réglage de l’installation).": "Characters per passage (empty: installation setting).",
  "« Importer et lancer tout le pipeline » : le pilote automatique mène chaque volume jusqu’au résultat (analyse, traduction, relecture, arbitrage) sans aucune validation à faire. Vous pourrez corriger un passage si vous le souhaitez.":
    "“Import and run the whole pipeline”: the autopilot takes each volume all the way to the result (analysis, translation, review, arbitration) with no validation to do. You can correct a passage if you wish.",
  "Décisions automatiques": "Automatic decisions",
  "{name} : volume {number}": "{name}: volume {number}",
  "{name} : chapitre {number}": "{name}: chapter {number}",
  "{name} : sans numéro": "{name}: no number",
  "Indiquez un numéro de chapitre ou confirmez qu’il n’en a pas.": "Enter a chapter number or confirm it has none.",
  "Identique au chapitre existant : ignoré.": "Identical to the existing chapter: skipped.",
  "Le chapitre existe déjà (« {title} ») avec un autre contenu : cochez Remplacer ou ignorez ce fichier.":
    "The chapter already exists (“{title}”) with other content: tick Replace or skip this file.",
  "Remplacera le chapitre existant « {title} ».": "Will replace the existing chapter “{title}”.",
  "Ce fichier a déjà été importé dans « {title} ».": "This file was already imported into “{title}”.",
  "Nommez la série.": "Name the series.",
  "Aucun fichier à importer.": "No file to import.",
  "Numéros en double : {numbers}.": "Duplicate numbers: {numbers}.",
  "Numéros manquants : {numbers}.": "Missing numbers: {numbers}.",
  "{count} point bloquant à corriger avant de continuer.": "{count} blocking issue to fix before continuing.",
  "{count} points bloquants à corriger avant de continuer.": "{count} blocking issues to fix before continuing.",
  "Fichiers écartés": "Excluded files",
  "Ces fichiers ne seront pas importés.": "These files will not be imported.",
  "Retirer de l’import": "Remove from the import",
  "Fichier illisible : {reason}": "Unreadable file: {reason}",
  "Chargement de la pré-analyse…": "Loading pre-analysis…",
  Récapitulatif: "Summary",
  "Volumes uniques": "Standalone volumes",
  "Volumes créés": "Volumes created",
  "Chapitres créés": "Chapters created",
  "Chapitres identiques (ignorés)": "Identical chapters (skipped)",
  "Chapitres remplacés": "Chapters replaced",
  "Fichiers ignorés": "Skipped files",
  Cible: "Target",
  "Flux continu (créé)": "Continuous flow (created)",
  "Flux continu": "Continuous flow",
  "Nouveau volume {number}": "New volume {number}",
  "Série « {name} »": "Series “{name}”",
  "Série « {name} » (nouvelle)": "Series “{name}” (new)",
  "Réglages de traduction": "Translation settings",
  "Laissez vide pour garder la valeur par défaut de la série ou du livre.":
    "Leave empty to keep the series or book default.",
  "Langue source": "Source language",
  "Langue cible": "Target language",
  Provider: "Provider",
  Qualité: "Quality",
  "Source de mémoire": "Memory source",
  "Par défaut": "Default",
  "Par défaut ({value})": "Default ({value})",
  "Rapide": "Fast",
  "Normale": "Normal",
  "Élevée": "High",
  "Maximale": "Maximum",
  "Interne": "Internal",
  "Hybride": "Hybrid",
  "Importer uniquement": "Import only",
  "Importer et lancer l’analyse": "Import and start analysis",
  "Importer et lancer tout le pipeline": "Import and run the whole pipeline",
  "Import terminé.": "Import complete.",
  "{count} volume créé ou mis à jour": "{count} volume created or updated",
  "{count} volumes créés ou mis à jour": "{count} volumes created or updated",
  "{created} chapitres créés · {unchanged} identiques · {replaced} remplacés":
    "{created} chapters created · {unchanged} identical · {replaced} replaced",
  "{count} travail lancé": "{count} job started",
  "{count} travaux lancés": "{count} jobs started",
  "Ouvrir la série": "Open the series",
  "Ouvrir le volume": "Open the volume",
  "Abandonner": "Abandon",
  "Abandonner cet import ?": "Abandon this import?",
  "Les fichiers envoyés pour la pré-analyse sont supprimés ; rien n’a encore été importé.":
    "The files uploaded for pre-analysis are deleted; nothing has been imported yet.",
  "Abandonner l’import": "Abandon the import",
  Retour: "Back",
  Continuer: "Continue",
  Fermer: "Close",
  "Supprimer des passages corrigés ?": "Delete corrected passages?",
  "{count} passage corrigé ou validé par une personne serait supprimé par ce remplacement.":
    "{count} passage corrected or validated by a person would be deleted by this replacement.",
  "{count} passages corrigés ou validés par une personne seraient supprimés par ce remplacement.":
    "{count} passages corrected or validated by a person would be deleted by this replacement.",
  "Remplacer quand même": "Replace anyway",
  "Rattachement": "Attachment",
});

type Format = ImportFormat | "archive";
type ChapterFormat = Exclude<ImportFormat, "epub">;
/** Extensions the server accepts for each import format (app/engines/ingestion UPLOAD_EXTENSIONS). */
const EXTENSIONS: Record<ImportFormat, string[]> = {
  epub: ["epub"],
  txt: ["txt"],
  md: ["md", "markdown"],
  html: ["html", "htm", "xhtml"],
  docx: ["docx"],
};
const FORMAT_LABELS: Record<ImportFormat, string> = { epub: "EPUB", txt: "TXT", md: "Markdown", html: "HTML", docx: "DOCX" };
const isChapterFormat = (value: Format | null): value is ChapterFormat =>
  !!value && value !== "epub" && value !== "archive";
type Step = "format" | "destination" | "files" | "review" | "confirm" | "done";
type DestinationMode = "existing" | "new" | "standalone";
type TargetMode = "serial" | "volume" | "new_volume";
type Start = "none" | "analyze" | "pipeline";

interface Upload {
  key: string;
  name: string;
  size: number;
  state: "queued" | "uploading" | "done" | "failed" | "rejected";
  error: string;
  inspection: FileInspection | null;
}

interface Row {
  index: number;
  name: string;
  title: string;
  number: string;
  guess: number | null;
  confidence: Confidence;
  reason: string;
  warnings: string[];
  firstLine: string;
  /** Why the server would refuse this file: it is sent as skipped. */
  excluded: string;
  existingChapter: ChapterProposal["existing_chapter"];
  /** A chapter the server reported as conflicting when committing. */
  conflict: { title: string } | null;
  libraryTitle: string;
  confirmed: boolean;
  skip: boolean;
  replace: boolean;
}

interface Defaults {
  source_language: string;
  target_language: string;
  provider_id: string;
  quality: "" | Quality;
  context_backend: "" | ContextBackend;
  passage_max_chars: string;
}

export interface WizardStart {
  seriesId?: string;
  files?: File[];
}

// One upload at a time: the server appends each file to the session's list, and without a row
// lock (SQLite) two concurrent uploads would overwrite each other's entry.
const PARALLEL_UPLOADS = 1;

export function formatOf(name: string): Format | null {
  const extension = name.toLowerCase().split(".").pop() || "";
  if (extension === "zip") return "archive";
  const found = (Object.keys(EXTENSIONS) as ImportFormat[]).find((format) => EXTENSIONS[format].includes(extension));
  return found || null;
}

const normalizeName = (value: string) => value.split(/\s+/).filter(Boolean).join(" ").toLocaleLowerCase();

function parseNumber(value: string): number | null | undefined {
  const text = value.trim().replace(",", ".");
  if (!text) return null;
  const parsed = Number(text);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : undefined;
}

export function fileSize(bytes: number) {
  return bytes >= 1024 ** 2
    ? `${formatNumber(bytes / 1024 ** 2, { maximumFractionDigits: 1 })} Mo`
    : `${formatNumber(Math.max(1, Math.round(bytes / 1024)))} Ko`;
}

function missingNumbers(numbers: number[]): number[] {
  const whole = new Set(numbers.filter((n) => Number.isInteger(n)));
  const top = Math.max(0, ...whole);
  return Array.from({ length: top }, (_, index) => index + 1).filter((n) => !whole.has(n));
}

export function ImportWizard({
  run,
  start,
  onClose,
  onImported,
}: {
  run: Run;
  start?: WizardStart;
  onClose: () => void;
  onImported: () => void;
}) {
  const { t, tp } = useI18n();
  const { confirm } = useDialogs();
  const initialFormat = start?.files?.length ? formatOf(start.files[0].name) : null;
  const [format, setFormat] = useState<Format | null>(initialFormat);
  // TXT, Markdown, HTML and DOCX files are chapters of a series; an EPUB is a volume.
  const chapters = isChapterFormat(format);
  // Low-confidence guesses are applied by the server unless it asks for a confirmation
  // (IMPORT_CONFIRM_LOW_CONFIDENCE); a refusal at commit time also turns the confirmation on.
  const [confirmRequired, setConfirmRequired] = useState(false);
  const [step, setStep] = useState<Step>(
    initialFormat === "archive" ? "files" : initialFormat ? "destination" : "format",
  );
  const [seriesList, setSeriesList] = useState<Series[] | null>(null);
  const [mode, setMode] = useState<DestinationMode | null>(start?.seriesId ? "existing" : null);
  const [seriesId, setSeriesId] = useState(start?.seriesId || "");
  const [seriesName, setSeriesName] = useState("");
  const [targetMode, setTargetMode] = useState<TargetMode>("serial");
  const [targetProject, setTargetProject] = useState("");
  const [newVolumeNumber, setNewVolumeNumber] = useState("");
  const [newVolumeTitle, setNewVolumeTitle] = useState("");
  const [detail, setDetail] = useState<SeriesDetail | null>(null);
  const [session, setSession] = useState<ImportSession | null>(null);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [seriesReason, setSeriesReason] = useState("");
  const [existingChapters, setExistingChapters] = useState<SeriesChapter[]>([]);
  const [firstLineTitle, setFirstLineTitle] = useState(false);
  const [defaults, setDefaults] = useState<Defaults>({
    source_language: "",
    target_language: "",
    provider_id: "",
    quality: "",
    context_backend: "",
    passage_max_chars: "",
  });
  const [providers, setProviders] = useState<ProviderSummary[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const sessionRef = useRef<ImportSession | null>(null);
  const queue = useRef<{ key: string; file: File }[]>([]);
  const active = useRef(0);
  const counter = useRef(0);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    void run.background(async () => {
      const [series, available] = await Promise.all([
        api<Series[]>("/series"),
        api<ProviderSummary[]>("/providers"),
      ]);
      setSeriesList(series);
      setProviders(available);
    });
  }, [run]);
  const matched =
    mode === "existing"
      ? seriesList?.find((series) => series.id === seriesId)
      : mode === "new" && seriesName.trim()
        ? seriesList?.find((series) => normalizeName(series.name) === normalizeName(seriesName))
        : undefined;
  const targetSeriesId = matched?.id || (mode === "existing" ? seriesId : "");
  useEffect(() => {
    if (!targetSeriesId) {
      setDetail(null);
      return;
    }
    let current = true;
    void run.background(async () => {
      const value = await api<SeriesDetail>(`/series/${targetSeriesId}`);
      if (current) setDetail(value);
    });
    return () => {
      current = false;
    };
  }, [targetSeriesId, run]);

  const seriesMode = mode !== "standalone";
  const serial = detail?.volume_list.find((project) => project.project_kind === "serial");
  const textVolumes = (detail?.volume_list || []).filter(
    (project) => project.project_kind !== "serial" && project.source_format !== "epub" && !project.archived_at,
  );
  const targetProjectId =
    !chapters ? "" : targetMode === "volume" ? targetProject : targetMode === "serial" ? serial?.id || "" : "";
  const steps: Step[] = format === "archive" ? ["format", "files"] : ["format", "destination", "files", "review", "confirm"];
  const stepLabels: Record<Step, string> = {
    format: t("Format"),
    destination: t("Destination"),
    files: t("Fichiers"),
    review: t("Pré-analyse"),
    confirm: t("Confirmation"),
    done: t("Terminé"),
  };
  const stepIndex = step === "done" ? steps.length : steps.indexOf(step);

  const patch = (key: string, change: Partial<Upload>) =>
    setUploads((all) => all.map((upload) => (upload.key === key ? { ...upload, ...change } : upload)));
  function pump() {
    const current = sessionRef.current;
    if (!current) return;
    while (active.current < PARALLEL_UPLOADS && queue.current.length) {
      const { key, file } = queue.current.shift()!;
      active.current += 1;
      patch(key, { state: "uploading" });
      const form = new FormData();
      form.append("file", file);
      void api<FileInspection>(`/imports/${current.id}/files`, { method: "POST", body: form })
        .then((inspection) => patch(key, { state: "done", inspection }))
        .catch((e: unknown) => patch(key, { state: "failed", error: e instanceof Error ? e.message : String(e) }))
        .finally(() => {
          active.current -= 1;
          pump();
        });
    }
  }
  function addFiles(files: File[], wanted: Format | null = format) {
    if (!wanted || wanted === "archive") return;
    const label = FORMAT_LABELS[wanted];
    const added: Upload[] = [];
    for (const file of files) {
      const key = `f${++counter.current}`;
      const base = { key, name: file.name, size: file.size, inspection: null };
      if (formatOf(file.name) !== wanted)
        added.push({ ...base, state: "rejected", error: t("Ce fichier n’est pas un {format} : il n’est pas envoyé.", { format: label }) });
      else if (
        [...uploads, ...added].some(
          (u) => (u.state === "queued" || u.state === "uploading" || u.state === "done") && u.name === file.name && u.size === file.size,
        )
      )
        added.push({ ...base, state: "rejected", error: t("Ce fichier est déjà dans la liste.") });
      else {
        added.push({ ...base, state: "queued", error: "" });
        queue.current.push({ key, file });
      }
    }
    // Choosing a file again after a failed upload is a retry: the failed attempt makes way.
    const retried = new Set(added.filter((u) => u.state === "queued").map((u) => `${u.name}:${u.size}`));
    setUploads((all) => [...all.filter((u) => !(u.state === "failed" && retried.has(`${u.name}:${u.size}`))), ...added]);
    // Without a session yet (files dropped on the library), the uploads start once it exists.
    pump();
  }
  const dropped = useRef(false);
  useEffect(() => {
    if (dropped.current) return;
    dropped.current = true;
    if (start?.files?.length && initialFormat && initialFormat !== "archive") addFiles(start.files, initialFormat);
  });

  async function discardSession() {
    const current = sessionRef.current;
    sessionRef.current = null;
    setSession(null);
    if (current && !current.result) await api(`/imports/${current.id}`, { method: "DELETE" }).catch(() => undefined);
  }
  async function ensureSession(wanted: ImportFormat) {
    if (sessionRef.current?.format === wanted) return;
    await discardSession();
    const created = await send<ImportSession>("/imports", { format: wanted });
    sessionRef.current = created;
    setSession(created);
    setConfirmRequired(!!created.confirm_low_confidence);
    pump();
  }
  function chooseFormat(next: Format) {
    if (next === format) return;
    setFormat(next);
    // Files of another format no longer belong here: start over.
    queue.current = [];
    setUploads([]);
    setRows(null);
    if (isChapterFormat(next) && mode === "standalone") setMode(null);
    void discardSession();
  }
  async function remove(upload: Upload) {
    const current = sessionRef.current;
    if (upload.inspection && current) {
      try {
        await api(`/imports/${current.id}/files/${upload.inspection.index}`, { method: "DELETE" });
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        return;
      }
      setRows(null);
    }
    queue.current = queue.current.filter((item) => item.key !== upload.key);
    setUploads((all) => all.filter((item) => item.key !== upload.key));
  }

  async function loadReview() {
    const current = sessionRef.current;
    if (!current) return;
    const params = new URLSearchParams();
    if (targetSeriesId) params.set("series_id", targetSeriesId);
    if (targetProjectId) params.set("project_id", targetProjectId);
    const view = await api<ImportSession>(`/imports/${current.id}?${params}`);
    if (view.confirm_low_confidence) setConfirmRequired(true);
    const existing =
      chapters && targetSeriesId && targetProjectId
        ? await api<SeriesChapter[]>(`/series/${targetSeriesId}/chapters?project_id=${targetProjectId}`)
        : [];
    setExistingChapters(existing);
    const proposal = view.proposal;
    if (format === "epub" && mode === "new" && !seriesName.trim() && proposal?.series?.name)
      setSeriesName(proposal.series.name);
    setSeriesReason(proposal?.series?.series_reason || "");
    const files = new Map(view.files.map((file) => [file.index, file]));
    const proposed = new Set<number>();
    const built: Row[] = (proposal?.items || []).map((item) => {
      proposed.add(item.index);
      const file = files.get(item.index)!;
      const number = format === "epub" ? (item as VolumeProposal).volume_number : (item as ChapterProposal).chapter_number;
      const duplicate = file.duplicate;
      const excluded =
        duplicate?.kind === "batch"
          ? t("Doublon de « {name} » dans cet import.", { name: duplicate.name })
          : duplicate?.kind === "library" && format === "epub"
            ? t("Déjà dans votre bibliothèque : « {title} ».", { title: duplicate.title })
            : "";
      return {
        index: item.index,
        name: file.name,
        title: item.title || file.title,
        number: number === null ? "" : String(number),
        guess: number,
        confidence: item.confidence,
        reason: item.reason,
        warnings: [...file.warnings, ...("warnings" in item ? item.warnings : [])],
        firstLine: typeof file.meta.first_line === "string" ? file.meta.first_line : "",
        excluded,
        existingChapter: "existing_chapter" in item ? item.existing_chapter : null,
        conflict: null,
        libraryTitle: duplicate?.kind === "library" && chapters ? duplicate.title : "",
        confirmed: false,
        skip: false,
        replace: false,
      };
    });
    for (const file of view.files)
      if (!proposed.has(file.index))
        built.push({
          index: file.index,
          name: file.name,
          title: file.title,
          number: "",
          guess: null,
          confidence: "low",
          reason: "",
          warnings: file.warnings,
          firstLine: "",
          excluded: t("Fichier illisible : {reason}", { reason: file.errors.join(" ") }),
          existingChapter: null,
          conflict: null,
          libraryTitle: "",
          confirmed: false,
          skip: true,
          replace: false,
        });
    setRows(built);
  }

  const kept = (rows || []).filter((row) => !row.excluded && !row.skip);
  const taken = useMemo(
    () =>
      new Map(
        (detail?.volume_list || [])
          .filter((project) => project.volume_number !== null)
          .map((project) => [project.volume_number as number, project.title]),
      ),
    [detail],
  );
  const newVolumeTaken = taken.get(parseNumber(newVolumeNumber) ?? -1);
  const chaptersByNumber = useMemo(
    () =>
      new Map(
        existingChapters
          .filter((chapter) => chapter.chapter_number !== null)
          .map((chapter) => [chapter.chapter_number as number, chapter]),
      ),
    [existingChapters],
  );
  const numbersInBatch = kept.map((row) => parseNumber(row.number)).filter((n): n is number => typeof n === "number");
  const duplicates = Array.from(new Set(numbersInBatch.filter((n, i) => numbersInBatch.indexOf(n) !== i))).sort(
    (a, b) => a - b,
  );
  const existingFor = (row: Row, number: number | null | undefined) => {
    if (typeof number !== "number") return null;
    const known = chaptersByNumber.get(number);
    if (known)
      return {
        title: known.title,
        same: row.existingChapter?.chapter_id === known.id && row.existingChapter.same_content,
      };
    if (row.conflict) return { title: row.conflict.title, same: false };
    if (row.existingChapter && number === row.guess)
      return { title: row.existingChapter.title, same: row.existingChapter.same_content };
    return null;
  };
  function checks(row: Row) {
    const errors: string[] = [];
    const warnings: string[] = [...row.warnings];
    const notes: string[] = [];
    let needsConfirm = false;
    let canReplace = false;
    if (row.excluded || row.skip) return { errors, warnings, notes: [t("Ignoré à l’import.")], needsConfirm, canReplace };
    const number = parseNumber(row.number);
    if (format === "epub") {
      if (!seriesMode) return { errors, warnings, notes, needsConfirm, canReplace };
      if (number === undefined || (typeof number === "number" && (!Number.isInteger(number) || number < 1)))
        errors.push(t("Numéro invalide."));
      else if (number === null) {
        if (confirmRequired) errors.push(t("Indiquez un numéro de volume."));
        else notes.push(t("Sans numéro : le volume suivant de la série lui sera attribué automatiquement."));
      } else {
        if (duplicates.includes(number)) errors.push(t("Numéro en double dans ce lot."));
        if (taken.has(number))
          errors.push(t("Le volume {number} existe déjà dans la série : « {title} ».", { number, title: taken.get(number)! }));
      }
      needsConfirm = row.confidence === "low" && number === row.guess;
    } else {
      if (number === undefined) errors.push(t("Numéro invalide."));
      else if (number === null) {
        needsConfirm = true;
        if (!confirmRequired) notes.push(t("Sans numéro : chapitre placé automatiquement d’après son nom."));
        else if (!row.confirmed) errors.push(t("Indiquez un numéro de chapitre ou confirmez qu’il n’en a pas."));
      } else {
        if (duplicates.includes(number)) errors.push(t("Numéro en double dans ce lot."));
        needsConfirm = row.confidence === "low" && number === row.guess;
        const existing = existingFor(row, number);
        if (existing?.same) notes.push(t("Identique au chapitre existant : ignoré."));
        else if (existing) {
          canReplace = true;
          if (row.replace) notes.push(t("Remplacera le chapitre existant « {title} ».", { title: existing.title }));
          else
            errors.push(
              t("Le chapitre existe déjà (« {title} ») avec un autre contenu : cochez Remplacer ou ignorez ce fichier.", {
                title: existing.title,
              }),
            );
        }
      }
      if (row.libraryTitle) warnings.push(t("Ce fichier a déjà été importé dans « {title} ».", { title: row.libraryTitle }));
    }
    // A low-confidence guess is the server's decision unless it asks for a confirmation: shown, editable,
    // never blocking.
    if (needsConfirm && !row.confirmed && number !== null) {
      if (confirmRequired) errors.push(t("Numéro incertain : confirmez-le ou corrigez-le."));
      else notes.push(t("Numéro retenu automatiquement ; modifiable ici."));
    }
    return { errors, warnings, notes, needsConfirm, canReplace };
  }
  const rowChecks = new Map((rows || []).map((row) => [row.index, checks(row)]));
  const nameMissing = seriesMode && mode === "new" && !seriesName.trim();
  const blocking = [
    ...(nameMissing ? [t("Nommez la série.")] : []),
    ...(rows && !kept.length ? [t("Aucun fichier à importer.")] : []),
    ...(duplicates.length && (chapters || seriesMode)
      ? [t("Numéros en double : {numbers}.", { numbers: duplicates.join(", ") })]
      : []),
  ];
  const rowErrors = Array.from(rowChecks.values()).reduce((total, check) => total + check.errors.length, 0);
  const blockingCount = blocking.length + rowErrors;
  const missing =
    chapters
      ? missingNumbers([...numbersInBatch, ...chaptersByNumber.keys()])
      : seriesMode
        ? missingNumbers([...numbersInBatch, ...taken.keys()])
        : [];

  const uploaded = uploads.filter((upload) => upload.state === "done");
  const pending = uploads.some((upload) => upload.state === "queued" || upload.state === "uploading");
  const canContinue =
    step === "format"
      ? !!format
      : step === "destination"
        ? format === "epub"
          ? mode === "standalone" || mode === "new" || (mode === "existing" && !!seriesId)
          : (mode === "new" ? !!seriesName.trim() : mode === "existing" && !!seriesId) &&
            (targetMode === "serial" ||
              (targetMode === "volume" && !!targetProject) ||
              (targetMode === "new_volume" && (parseNumber(newVolumeNumber) ?? 0) >= 1 && !newVolumeTaken))
        : step === "files"
          ? !!session && !pending && uploaded.length > 0
          : step === "review"
            ? !!rows && blockingCount === 0
            : false;

  async function next() {
    setError("");
    setBusy(true);
    try {
      if (step === "format") setStep(format === "archive" ? "files" : "destination");
      else if (step === "destination") {
        await ensureSession(format as ImportFormat);
        setStep("files");
      } else if (step === "files") {
        await loadReview();
        setStep("review");
      } else if (step === "review") setStep("confirm");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  function back() {
    setError("");
    const previous = steps[stepIndex - 1];
    if (previous) setStep(previous);
  }
  async function commit(launch: Start, discardHuman = false): Promise<void> {
    const current = sessionRef.current;
    if (!current || !rows) return;
    setError("");
    setBusy(true);
    const settings = Object.fromEntries(
      Object.entries(defaults)
        .filter(([, value]) => value !== "")
        .map(([key, value]) => [key, key === "passage_max_chars" ? Number(value) : value]),
    );
    const body = {
      destination:
        mode === "standalone"
          ? { mode: "standalone" }
          : mode === "existing"
            ? { mode: "series", series_id: seriesId }
            : { mode: "series", series_name: seriesName.trim() },
      ...(chapters
        ? {
            target:
              targetMode === "volume"
                ? { mode: "volume", project_id: targetProject }
                : targetMode === "new_volume"
                  ? { mode: "new_volume", volume_number: parseNumber(newVolumeNumber), volume_title: newVolumeTitle.trim() }
                  : { mode: "serial" },
          }
        : {}),
      items: rows.map((row) => {
        const number = parseNumber(row.number) ?? null;
        return {
          index: row.index,
          title: row.title.trim(),
          ...(format === "epub"
            ? { volume_number: seriesMode ? number : null }
            : { chapter_number: number }),
          confirmed: row.confirmed,
          replace: row.replace,
          skip: row.skip || !!row.excluded,
        };
      }),
      first_line_title: format === "txt" && firstLineTitle,
      discard_human: discardHuman,
      settings,
      start: launch,
    };
    try {
      const answer = await send<ImportResult>(`/imports/${current.id}/commit`, body);
      sessionRef.current = { ...current, result: answer };
      setSession(sessionRef.current);
      setResult(answer);
      setStep("done");
      onImported();
    } catch (e) {
      const detail = e instanceof ApiError && e.status === 409 && e.detail && typeof e.detail === "object" ? e.detail : null;
      if (detail && "protected_segments" in detail) {
        const count = Array.isArray(detail.protected_segments) ? detail.protected_segments.length : 0;
        setBusy(false);
        const accepted = await confirm({
          title: t("Supprimer des passages corrigés ?"),
          message: tp(
            count,
            "{count} passage corrigé ou validé par une personne serait supprimé par ce remplacement.",
            "{count} passages corrigés ou validés par une personne seraient supprimés par ce remplacement.",
          ),
          confirmLabel: t("Remplacer quand même"),
          tone: "danger",
        });
        if (accepted) return commit(launch, true);
        return;
      }
      if (e instanceof ApiError && e.status === 422 && !confirmRequired) {
        // The server may require confirmations (IMPORT_CONFIRM_LOW_CONFIDENCE): show them if it does.
        const view = await api<ImportSession>(`/imports/${current.id}`).catch(() => null);
        if (view?.confirm_low_confidence) {
          setConfirmRequired(true);
          setStep("review");
        }
      }
      if (detail && "conflicts" in detail && Array.isArray(detail.conflicts)) {
        const conflicts = detail.conflicts as { number: number | null; title: string }[];
        setRows((all) =>
          (all || []).map((row) => {
            const found = conflicts.find((item) => item.number !== null && item.number === parseNumber(row.number));
            return found ? { ...row, conflict: { title: found.title } } : row;
          }),
        );
        setStep("review");
      }
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function restore(file: File) {
    setError("");
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const project = await api<Project>("/projects/import", { method: "POST", body: form });
      onImported();
      onClose();
      location.hash = `project/${project.id}`;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function close() {
    if (busy) return;
    const current = sessionRef.current;
    if (current && !current.result && uploads.some((upload) => upload.inspection)) {
      const accepted = await confirm({
        title: t("Abandonner cet import ?"),
        message: t("Les fichiers envoyés pour la pré-analyse sont supprimés ; rien n’a encore été importé."),
        confirmLabel: t("Abandonner l’import"),
        tone: "danger",
      });
      if (!accepted) return;
    }
    queue.current = [];
    void discardSession();
    onClose();
  }

  const updateRow = (index: number, change: Partial<Row>) =>
    setRows((all) => (all || []).map((row) => (row.index === index ? { ...row, ...change } : row)));
  const move = (index: number, delta: number) =>
    setRows((all) => {
      const list = [...(all || [])];
      const from = list.findIndex((row) => row.index === index);
      const to = from + delta;
      if (from < 0 || to < 0 || to >= list.length) return all;
      [list[from], list[to]] = [list[to], list[from]];
      return list;
    });
  function renumber() {
    setRows((all) => {
      const list = all || [];
      const numbers = list
        .filter((row) => !row.excluded && !row.skip)
        .map((row) => parseNumber(row.number))
        .filter((n): n is number => typeof n === "number" && n >= 1);
      let value = Math.floor(numbers.length ? Math.min(...numbers) : 1);
      // An explicit order is the person's decision: the numbers no longer need a confirmation.
      return list.map((row) => (row.excluded || row.skip ? row : { ...row, number: String(value++), confirmed: true }));
    });
  }
  function sortByNumber() {
    setRows((all) =>
      [...(all || [])].sort((a, b) => {
        const x = parseNumber(a.number);
        const y = parseNumber(b.number);
        const left = typeof x === "number" ? x : Number.MAX_SAFE_INTEGER;
        const right = typeof y === "number" ? y : Number.MAX_SAFE_INTEGER;
        return left - right || a.name.localeCompare(b.name, undefined, { numeric: true });
      }),
    );
  }

  const confidenceLabel = (value: Confidence) =>
    value === "high" ? t("Confiance élevée") : value === "medium" ? t("Confiance moyenne") : t("Confiance faible");
  const confidenceTone = (value: Confidence) => (value === "high" ? "success" : value === "medium" ? "info" : "warning");
  const seriesLabel =
    mode === "standalone"
      ? t("Volumes uniques")
      : mode === "existing"
        ? t("Série « {name} »", { name: matched?.name || "" })
        : matched
          ? t("Série « {name} »", { name: matched.name })
          : t("Série « {name} » (nouvelle)", { name: seriesName.trim() });

  const body = (() => {
    if (step === "format")
      return (
        <div className="stack">
          <fieldset className="choice-group">
            <legend>{t("Que voulez-vous ajouter ?")}</legend>
            <Choice
              name="import-format"
              checked={format === "epub"}
              onChange={() => chooseFormat("epub")}
              title={t("Livres EPUB")}
              description={t("Un ou plusieurs EPUB : les volumes d’une série ou des volumes uniques.")}
              icon="book"
            />
            <Choice
              name="import-format"
              checked={format === "txt"}
              onChange={() => chooseFormat("txt")}
              title={t("Chapitres TXT (webnovel)")}
              description={t("Un fichier texte par chapitre, toujours rattaché à une série.")}
              icon="file"
            />
            <Choice
              name="import-format"
              checked={format === "md" || format === "html" || format === "docx"}
              onChange={() => chooseFormat(format === "html" || format === "docx" ? format : "md")}
              title={t("Chapitres Markdown, HTML ou DOCX")}
              description={t(
                "Un fichier par chapitre : titres, paragraphes, listes et citations sont traduits ; le code et les tableaux restent tels quels.",
              )}
              icon="file"
            >
              <Field label={t("Format des chapitres")}>
                <Select value={format || "md"} onChange={(e) => chooseFormat(e.target.value as ChapterFormat)}>
                  <option value="md">Markdown (.md)</option>
                  <option value="html">HTML (.html)</option>
                  <option value="docx">DOCX (.docx)</option>
                </Select>
              </Field>
            </Choice>
            <Choice
              name="import-format"
              checked={format === "archive"}
              onChange={() => chooseFormat("archive")}
              title={t("Restaurer une archive Libris")}
              description={t("Un projet exporté depuis Libris (.zip), avec tout son travail.")}
              icon="archive"
            />
          </fieldset>
          <Callout tone="neutral">
            {t("Automatisation : l’API JSON reçoit des chapitres envoyés par un script.")}{" "}
            {t("Chaque compte crée ses propres jetons.")}{" "}
            <a href="#account" onClick={() => void close()}>
              {t("Créer un jeton dans Mon compte › Jetons d’API")}
            </a>
          </Callout>
        </div>
      );
    if (step === "destination")
      return (
        <div className="stack">
          <fieldset className="choice-group">
            <legend>{format === "epub" ? t("Où ranger ces livres ?") : t("Série des chapitres")}</legend>
            {chapters && (
              <p className="field-hint">
                {format === "txt"
                  ? t("Des chapitres TXT appartiennent toujours à une série : choisissez-la ou créez-la.")
                  : t("Des chapitres appartiennent toujours à une série : choisissez-la ou créez-la.")}
              </p>
            )}
            <Choice
              name="import-destination"
              checked={mode === "existing"}
              onChange={() => setMode("existing")}
              title={t("Série existante")}
              description={t("Les volumes rejoignent une série de votre bibliothèque.")}
              icon="book"
              disabled={seriesList !== null && !seriesList.some((series) => !series.shared)}
              disabledHint={t("Aucune série pour l’instant.")}
            >
              <Field label={t("Choisir la série")}>
                <Select value={seriesId} onChange={(e) => setSeriesId(e.target.value)}>
                  <option value="">{t("Choisissez une série…")}</option>
                  {(seriesList || [])
                    .filter((series) => !series.shared)
                    .map((series) => (
                      <option key={series.id} value={series.id}>
                        {series.name}
                      </option>
                    ))}
                </Select>
              </Field>
            </Choice>
            <Choice
              name="import-destination"
              checked={mode === "new"}
              onChange={() => setMode("new")}
              title={t("Nouvelle série")}
              description={t("Une série est créée pour ces fichiers.")}
              icon="plus"
            >
              <Field
                label={t("Nom de la série")}
                hint={format === "epub" ? t("Laissez vide pour reprendre le nom détecté dans les fichiers.") : undefined}
              >
                <Input value={seriesName} onChange={(e) => setSeriesName(e.target.value)} maxLength={500} />
              </Field>
              {matched && <p className="field-hint">{t("Série existante : les volumes y seront ajoutés.")}</p>}
            </Choice>
            {format === "epub" && (
              <Choice
                name="import-destination"
                checked={mode === "standalone"}
                onChange={() => setMode("standalone")}
                title={t("Volume unique")}
                description={t("Chaque EPUB devient un livre indépendant, sans série.")}
                icon="file"
              />
            )}
          </fieldset>
          {chapters && (mode === "new" || (mode === "existing" && seriesId)) && (
            <fieldset className="choice-group">
              <legend>{t("Où ajouter les chapitres ?")}</legend>
              <Choice
                name="import-target"
                checked={targetMode === "serial"}
                onChange={() => setTargetMode("serial")}
                title={t("Flux continu de la série")}
                description={`${t("Les chapitres s’ajoutent à la suite, sans découpage en volumes.")} ${
                  serial ? t("Le flux continu existe : les chapitres y sont ajoutés.") : t("Le flux continu sera créé.")
                }`}
                icon="list"
              />
              <Choice
                name="import-target"
                checked={targetMode === "volume"}
                onChange={() => setTargetMode("volume")}
                title={t("Volume existant")}
                description={t("Un volume texte de la série.")}
                icon="book"
                disabled={!textVolumes.length}
                disabledHint={t("Cette série n’a pas encore de volume texte.")}
              >
                <Field label={t("Volume existant")}>
                  <Select value={targetProject} onChange={(e) => setTargetProject(e.target.value)}>
                    <option value="">{t("Choisissez un volume…")}</option>
                    {textVolumes.map((project) => (
                      <option key={project.id} value={project.id}>
                        {project.volume_number ? `${project.volume_number} · ` : ""}
                        {project.title}
                      </option>
                    ))}
                  </Select>
                </Field>
              </Choice>
              <Choice
                name="import-target"
                checked={targetMode === "new_volume"}
                onChange={() => setTargetMode("new_volume")}
                title={t("Nouveau volume")}
                description={t("Un volume numéroté est créé dans la série.")}
                icon="plus"
              >
                <FormGrid>
                  <Field
                    label={t("Numéro du volume")}
                    error={
                      newVolumeTaken
                        ? t("Le volume {number} existe déjà dans la série : « {title} ».", {
                            number: newVolumeNumber,
                            title: newVolumeTaken,
                          })
                        : undefined
                    }
                  >
                    <Input
                      type="number"
                      min="1"
                      max="10000"
                      inputMode="numeric"
                      value={newVolumeNumber}
                      onChange={(e) => setNewVolumeNumber(e.target.value)}
                    />
                  </Field>
                  <Field label={t("Titre du volume")} hint={t("Facultatif")}>
                    <Input value={newVolumeTitle} onChange={(e) => setNewVolumeTitle(e.target.value)} maxLength={500} />
                  </Field>
                </FormGrid>
              </Choice>
            </fieldset>
          )}
        </div>
      );
    if (step === "files" && format === "archive")
      return (
        <div className="stack">
          <div className="wizard-drop">
            <Icon name="archive" size={24} />
            <p>{t("Le projet et son travail sont recréés tels qu’exportés ; les membres et le provider ne sont pas restaurés.")}</p>
            <FileButton
              label={busy ? t("Restauration en cours…") : t("Choisir l’archive (.zip)")}
              accept=".zip"
              primary
              disabled={busy}
              onFiles={([file]) => void restore(file)}
            />
          </div>
        </div>
      );
    if (step === "files") {
      const label = FORMAT_LABELS[format as ImportFormat];
      const accept = EXTENSIONS[format as ImportFormat].map((extension) => `.${extension}`).join(",");
      const done = uploads.filter((upload) => upload.state === "done" || upload.state === "failed").length;
      const counted = uploads.filter((upload) => upload.state !== "rejected").length;
      return (
        <div className="stack">
          <div
            className={cx("wizard-drop", dragging && "is-dragging")}
            onDragOver={(event: DragEvent) => {
              if (!event.dataTransfer.types.includes("Files")) return;
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event: DragEvent) => {
              event.preventDefault();
              setDragging(false);
              addFiles(Array.from(event.dataTransfer.files));
            }}
          >
            <Icon name="upload" size={24} />
            <strong>{t("Glissez-déposez vos fichiers {format} ici", { format: label })}</strong>
            <p className="muted">{t("ou choisissez-les sur cet appareil. Rien n’est importé avant la confirmation.")}</p>
            <FileButton label={t("Choisir des fichiers")} accept={accept} multiple onFiles={(files) => addFiles(files)} />
          </div>
          {counted > 0 && (
            <div className="stack-sm" role="status">
              <span className="muted">{t("{done}/{total} fichiers analysés", { done, total: counted })}</span>
              <ProgressBar value={done} max={counted} label={t("Analyse des fichiers")} size="sm" />
            </div>
          )}
          {uploads.length > 0 && (
            <ul className="wizard-files">
              {uploads.map((upload) => (
                <UploadItem
                  key={upload.key}
                  upload={upload}
                  format={format as ImportFormat}
                  onRemove={() => void remove(upload)}
                />
              ))}
            </ul>
          )}
        </div>
      );
    }
    if (step === "review") {
      if (!rows) return <p className="muted">{t("Chargement de la pré-analyse…")}</p>;
      const listed = rows.filter((row) => !row.excluded);
      const excluded = rows.filter((row) => row.excluded);
      return (
        <div className="stack">
          {format === "epub" && !seriesMode ? (
            <Callout tone="info">{t("Chaque EPUB deviendra un volume unique, sans série.")}</Callout>
          ) : (
            <div className="wizard-series">
              {mode === "new" ? (
                <Field
                  label={t("Nom de la série")}
                  hint={seriesReason ? t("Nom proposé : {reason}", { reason: seriesReason }) : undefined}
                >
                  <Input value={seriesName} onChange={(e) => setSeriesName(e.target.value)} maxLength={500} />
                </Field>
              ) : (
                <div className="stack-sm">
                  <span className="field-label">{t("Nom de la série")}</span>
                  <strong>{matched?.name}</strong>
                </div>
              )}
              <Badge tone={matched ? "info" : "accent"}>
                {matched ? t("Série existante : les volumes y seront ajoutés.") : t("Une nouvelle série sera créée.")}
              </Badge>
            </div>
          )}
          {format === "txt" && (
            <Checkbox
              label={t("Utiliser la première ligne comme titre")}
              description={t(
                "Le titre de chaque chapitre est la première ligne de son fichier, qui n’est plus répétée dans le texte.",
              )}
              checked={firstLineTitle}
              onChange={(e) => setFirstLineTitle(e.target.checked)}
            />
          )}
          {blockingCount > 0 ? (
            <Callout tone="danger" role="alert">
              {tp(
                blockingCount,
                "{count} point bloquant à corriger avant de continuer.",
                "{count} points bloquants à corriger avant de continuer.",
              )}
              {blocking.length > 0 && (
                <ul className="wizard-messages">
                  {blocking.map((text) => (
                    <li key={text}>{text}</li>
                  ))}
                </ul>
              )}
            </Callout>
          ) : null}
          {missing.length > 0 && (
            <Callout tone="warning" role="status">
              {t("Numéros manquants : {numbers}.", { numbers: missing.join(", ") })}
            </Callout>
          )}
          {(chapters || seriesMode) && listed.length > 1 && (
            <div className="row">
              {chapters && (
                <Button size="sm" icon="list" onClick={sortByNumber}>
                  {t("Trier par numéro")}
                </Button>
              )}
              <Button size="sm" icon="refresh" onClick={renumber}>
                {t("Renuméroter dans cet ordre")}
              </Button>
            </div>
          )}
          <div className={cx("wizard-table", !(chapters || seriesMode) && "no-number")}>
            <div className="wizard-table-head" aria-hidden="true">
              <span>{t("Fichier")}</span>
              {(chapters || seriesMode) && <span>{format === "epub" ? t("N° vol.") : t("N° ch.")}</span>}
              <span>{t("Titre")}</span>
            </div>
            <ol className="wizard-rows">
              {listed.map((row, position) => {
                const check = rowChecks.get(row.index)!;
                const ignored = row.skip;
                const tone = ignored ? "neutral" : check.errors.length ? "danger" : check.warnings.length ? "warning" : "success";
                return (
                  <li key={row.index} className={cx("wizard-row", `tone-border-${tone}`, ignored && "is-skipped")}>
                    <div className="wizard-row-file">
                      <span className="wizard-row-name" title={row.name}>
                        {row.name}
                      </span>
                      <span className="row">
                        <Badge tone={confidenceTone(row.confidence)} title={row.reason}>
                          {confidenceLabel(row.confidence)}
                        </Badge>
                        {row.reason && <span className="wizard-reason">{row.reason}</span>}
                      </span>
                    </div>
                    {(chapters || seriesMode) && (
                      <Field label={format === "epub" ? t("N° vol.") : t("N° ch.")} className="wizard-number">
                        <Input
                          inputMode="decimal"
                          aria-label={
                            format === "epub"
                              ? t("Numéro de volume de {name}", { name: row.name })
                              : t("Numéro de chapitre de {name}", { name: row.name })
                          }
                          value={row.number}
                          disabled={ignored}
                          onChange={(e) => updateRow(row.index, { number: e.target.value })}
                        />
                      </Field>
                    )}
                    <Field label={t("Titre")} className="wizard-title">
                      <Input
                        aria-label={t("Titre de {name}", { name: row.name })}
                        value={format === "txt" && firstLineTitle ? row.firstLine || row.title : row.title}
                        disabled={ignored || (format === "txt" && firstLineTitle)}
                        maxLength={500}
                        onChange={(e) => updateRow(row.index, { title: e.target.value })}
                      />
                    </Field>
                    <div className="wizard-row-tools">
                      <IconButton
                        icon="chevronUp"
                        size="sm"
                        label={t("Monter {name}", { name: row.name })}
                        disabled={position === 0}
                        onClick={() => move(row.index, -1)}
                      />
                      <IconButton
                        icon="chevronDown"
                        size="sm"
                        label={t("Descendre {name}", { name: row.name })}
                        disabled={position === listed.length - 1}
                        onClick={() => move(row.index, 1)}
                      />
                    </div>
                    {check.errors.length + check.warnings.length + check.notes.length > 0 && (
                      <ul className="wizard-messages wizard-row-wide">
                        {check.errors.map((text) => (
                          <li key={text} className="tone-text-danger">
                            <Icon name="alert" size={12} /> {text}
                          </li>
                        ))}
                        {check.warnings.map((text) => (
                          <li key={text} className="tone-text-warning">
                            <Icon name="info" size={12} /> {text}
                          </li>
                        ))}
                        {check.notes.map((text) => (
                          <li key={text} className="muted">
                            <Icon name="check" size={12} /> {text}
                          </li>
                        ))}
                      </ul>
                    )}
                    <div className="wizard-row-actions wizard-row-wide">
                      {check.needsConfirm && confirmRequired && !ignored && (
                        <Checkbox
                          label={parseNumber(row.number) === null ? t("Confirmer : pas de numéro") : t("Confirmer le numéro")}
                          checked={row.confirmed}
                          onChange={(e) => updateRow(row.index, { confirmed: e.target.checked })}
                        />
                      )}
                      {check.canReplace && !ignored && (
                        <Checkbox
                          label={t("Remplacer")}
                          aria-label={t("Remplacer le chapitre existant « {title} »", {
                            title: existingFor(row, parseNumber(row.number))?.title || "",
                          })}
                          checked={row.replace}
                          onChange={(e) => updateRow(row.index, { replace: e.target.checked })}
                        />
                      )}
                      <Checkbox
                        label={t("Ignorer")}
                        aria-label={`${t("Ignorer ce fichier")} · ${row.name}`}
                        checked={row.skip}
                        onChange={(e) => updateRow(row.index, { skip: e.target.checked })}
                      />
                    </div>
                  </li>
                );
              })}
            </ol>
          </div>
          {excluded.length > 0 && (
            <section className="stack-sm" aria-label={t("Fichiers écartés")}>
              <h3 className="wizard-subtitle">{t("Fichiers écartés")}</h3>
              <p className="field-hint">{t("Ces fichiers ne seront pas importés.")}</p>
              <ul className="wizard-files">
                {excluded.map((row) => {
                  const upload = uploads.find((item) => item.inspection?.index === row.index);
                  return (
                    <li key={row.index} className="wizard-file tone-border-danger">
                      <span className="wizard-file-name">{row.name}</span>
                      <span className="tone-text-danger wizard-file-state">{row.excluded}</span>
                      {upload && (
                        <Button size="sm" variant="ghost" icon="trash" onClick={() => void remove(upload)}>
                          {t("Retirer de l’import")}
                        </Button>
                      )}
                    </li>
                  );
                })}
              </ul>
            </section>
          )}
        </div>
      );
    }
    if (step === "confirm") {
      const existingCount = kept.filter((row) => existingFor(row, parseNumber(row.number))).length;
      const identical = kept.filter((row) => existingFor(row, parseNumber(row.number))?.same).length;
      const replaced = kept.filter((row) => row.replace && existingFor(row, parseNumber(row.number))).length;
      const skipped = (rows || []).length - kept.length;
      const seriesDefault = (value: string | null | undefined) =>
        value ? t("Par défaut ({value})", { value }) : t("Par défaut");
      const providerName = (id: string | null | undefined) => providers.find((p) => p.id === id)?.name || id || "";
      const qualityLabel = (value: string | null | undefined) =>
        value ? t(({ fast: "Rapide", normal: "Normale", high: "Élevée", maximum: "Maximale" } as Record<string, string>)[value] || value) : "";
      const backendLabel = (value: string | null | undefined) =>
        value ? t(({ internal: "Interne", openviking: "OpenViking", hybrid: "Hybride" } as Record<string, string>)[value] || value) : "";
      const detected = uploaded.find((upload) => upload.inspection?.language)?.inspection?.language;
      return (
        <div className="stack">
          <section className="card card-padded wizard-summary" aria-label={t("Récapitulatif")}>
            <h3 className="wizard-subtitle">{t("Récapitulatif")}</h3>
            <dl className="summary-list">
              <dt>{t("Rattachement")}</dt>
              <dd>{seriesLabel}</dd>
              {format === "epub" ? (
                <>
                  <dt>{mode === "standalone" ? t("Volumes uniques") : t("Volumes créés")}</dt>
                  <dd className="tabular">
                    {seriesMode
                      ? kept
                          .map((row) => `${parseNumber(row.number) ?? "?"} · ${row.title}`)
                          .join(" — ")
                      : kept.map((row) => row.title).join(" — ")}
                  </dd>
                </>
              ) : (
                <>
                  <dt>{t("Cible")}</dt>
                  <dd>
                    {targetMode === "serial"
                      ? serial
                        ? t("Flux continu")
                        : t("Flux continu (créé)")
                      : targetMode === "volume"
                        ? textVolumes.find((project) => project.id === targetProject)?.title
                        : t("Nouveau volume {number}", { number: newVolumeNumber })}
                  </dd>
                  <dt>{t("Chapitres créés")}</dt>
                  <dd className="tabular">{kept.length - existingCount}</dd>
                  <dt>{t("Chapitres identiques (ignorés)")}</dt>
                  <dd className="tabular">{identical}</dd>
                  <dt>{t("Chapitres remplacés")}</dt>
                  <dd className="tabular">{replaced}</dd>
                </>
              )}
              <dt>{t("Fichiers ignorés")}</dt>
              <dd className="tabular">{skipped}</dd>
            </dl>
            {chapters && (targetMode === "volume" || (targetMode === "serial" && !!serial)) && (
              <p className="field-hint">
                {t(
                  "Suite d’un volume : seuls les chapitres nouveaux ou remplacés sont traduits et relus. Les chapitres déjà traduits servent de contexte, sans nouvel appel au modèle.",
                )}
              </p>
            )}
          </section>
          <section className="stack-sm" aria-label={t("Réglages de traduction")}>
            <h3 className="wizard-subtitle">{t("Réglages de traduction")}</h3>
            <p className="field-hint">{t("Laissez vide pour garder la valeur par défaut de la série ou du livre.")}</p>
            <FormGrid columns={3}>
              <Field label={t("Langue source")}>
                <Input
                  value={defaults.source_language}
                  placeholder={detail?.source_language || detected || ""}
                  onChange={(e) => setDefaults({ ...defaults, source_language: e.target.value })}
                />
              </Field>
              <Field label={t("Langue cible")}>
                <Input
                  value={defaults.target_language}
                  placeholder={detail?.target_language || ""}
                  onChange={(e) => setDefaults({ ...defaults, target_language: e.target.value })}
                />
              </Field>
              <Field label={t("Provider")}>
                <Select
                  value={defaults.provider_id}
                  onChange={(e) => setDefaults({ ...defaults, provider_id: e.target.value })}
                >
                  <option value="">{seriesDefault(providerName(detail?.provider_id))}</option>
                  {providers.map((provider) => (
                    <option key={provider.id} value={provider.id}>
                      {provider.name} · {provider.model}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label={t("Qualité")}>
                <Select
                  value={defaults.quality}
                  onChange={(e) => setDefaults({ ...defaults, quality: e.target.value as Defaults["quality"] })}
                >
                  <option value="">{seriesDefault(qualityLabel(detail?.quality))}</option>
                  <option value="fast">{t("Rapide")}</option>
                  <option value="normal">{t("Normale")}</option>
                  <option value="high">{t("Élevée")}</option>
                  <option value="maximum">{t("Maximale")}</option>
                </Select>
              </Field>
              <Field label={t("Source de mémoire")}>
                <Select
                  value={defaults.context_backend}
                  onChange={(e) =>
                    setDefaults({ ...defaults, context_backend: e.target.value as Defaults["context_backend"] })
                  }
                >
                  <option value="">{seriesDefault(backendLabel(detail?.context_backend))}</option>
                  <option value="internal">{t("Interne")}</option>
                  <option value="openviking">OpenViking</option>
                  <option value="hybrid">{t("Hybride")}</option>
                </Select>
              </Field>
              <Field label={t("Taille des passages")} hint={t("Caractères par passage (vide : réglage de l’installation).")}>
                <Input
                  type="number"
                  min="500"
                  max="20000"
                  step="100"
                  inputMode="numeric"
                  placeholder="3500"
                  value={defaults.passage_max_chars}
                  onChange={(e) => setDefaults({ ...defaults, passage_max_chars: e.target.value })}
                />
              </Field>
            </FormGrid>
          </section>
          <Callout tone="accent" icon="sparkles">
            {t(
              "« Importer et lancer tout le pipeline » : le pilote automatique mène chaque volume jusqu’au résultat (analyse, traduction, relecture, arbitrage) sans aucune validation à faire. Vous pourrez corriger un passage si vous le souhaitez.",
            )}
          </Callout>
        </div>
      );
    }
    if (step === "done" && result)
      return (
        <div className="stack">
          <Callout tone="success" role="status" title={t("Import terminé.")}>
            <ul className="wizard-messages">
              {result.series_name && <li>{t("Série « {name} »", { name: result.series_name })}</li>}
              <li>
                {tp(result.projects.length, "{count} volume créé ou mis à jour", "{count} volumes créés ou mis à jour")}
                {" : "}
                {result.projects.map((project) => project.title).join(" — ")}
              </li>
              {chapters && (
                <li>
                  {t("{created} chapitres créés · {unchanged} identiques · {replaced} remplacés", {
                    created: result.chapters.created,
                    unchanged: result.chapters.unchanged,
                    replaced: result.chapters.replaced,
                  })}
                </li>
              )}
              {result.jobs.length > 0 && (
                <li>{tp(result.jobs.length, "{count} travail lancé", "{count} travaux lancés")}</li>
              )}
            </ul>
          </Callout>
          {!!result.decisions?.length && (
            <Callout tone="info" title={t("Décisions automatiques")}>
              <ul className="wizard-messages">
                {result.decisions.map((decision) => {
                  const number = decision.volume_number ?? decision.chapter_number;
                  const label =
                    number === null || number === undefined
                      ? t("{name} : sans numéro", { name: decision.name })
                      : "volume_number" in decision
                        ? t("{name} : volume {number}", { name: decision.name, number })
                        : t("{name} : chapitre {number}", { name: decision.name, number });
                  return (
                    <li key={`${decision.index}-${decision.reason}`}>
                      {label} — <span className="muted">{decision.reason}</span>
                    </li>
                  );
                })}
              </ul>
            </Callout>
          )}
          {result.warnings.length > 0 && (
            <Callout tone="warning" role="status">
              <ul className="wizard-messages">
                {result.warnings.map((text) => (
                  <li key={text}>{text}</li>
                ))}
              </ul>
            </Callout>
          )}
        </div>
      );
    return null;
  })();

  const go = (hash: string) => {
    onClose();
    location.hash = hash;
  };
  const footer =
    step === "done" && result ? (
      <>
        <Button onClick={onClose}>{t("Fermer")}</Button>
        {result.projects.length === 1 && (
          <Button onClick={() => go(`project/${result.projects[0].id}`)}>{t("Ouvrir le volume")}</Button>
        )}
        {result.series_id && (
          <Button variant="primary" iconAfter="arrowRight" onClick={() => go(`series/${result.series_id}`)}>
            {t("Ouvrir la série")}
          </Button>
        )}
      </>
    ) : (
      <>
        <Button variant="ghost" className="wizard-abandon" onClick={() => void close()} disabled={busy}>
          {t("Abandonner")}
        </Button>
        {stepIndex > 0 && (
          <Button icon="chevronLeft" onClick={back} disabled={busy}>
            {t("Retour")}
          </Button>
        )}
        {step === "confirm" ? (
          <>
            <Button onClick={() => void commit("none")} loading={busy}>
              {t("Importer uniquement")}
            </Button>
            <Button onClick={() => void commit("analyze")} disabled={busy}>
              {t("Importer et lancer l’analyse")}
            </Button>
            <Button variant="primary" onClick={() => void commit("pipeline")} disabled={busy}>
              {t("Importer et lancer tout le pipeline")}
            </Button>
          </>
        ) : (
          !(step === "files" && format === "archive") && (
            <Button
              variant="primary"
              iconAfter="chevronRight"
              onClick={() => void next()}
              disabled={!canContinue}
              loading={busy}
            >
              {t("Continuer")}
            </Button>
          )
        )}
      </>
    );

  return (
    <Dialog
      open
      onClose={() => void close()}
      title={t("Ajouter du contenu")}
      description={
        step === "done"
          ? t("Terminé")
          : `${t("Étape {current} sur {total}", { current: stepIndex + 1, total: steps.length })} · ${stepLabels[step]}`
      }
      size="xl"
      className="import-wizard"
      footer={footer}
    >
      <ol className="wizard-steps" aria-label={t("Étapes de l’import")}>
        {steps.map((item, index) => (
          <li
            key={item}
            aria-current={item === step ? "step" : undefined}
            className={cx(index < stepIndex && "is-done", item === step && "is-current")}
          >
            <span className="wizard-step-number" aria-hidden="true">
              {index < stepIndex ? <Icon name="check" size={12} /> : index + 1}
            </span>
            <span className="wizard-step-label">{stepLabels[item]}</span>
          </li>
        ))}
      </ol>
      {error && (
        <Callout tone="danger" role="alert" className="pre-line">
          {error}
        </Callout>
      )}
      {body}
    </Dialog>
  );
}

function Choice({
  name,
  checked,
  onChange,
  title,
  description,
  icon,
  disabled = false,
  disabledHint,
  children,
}: {
  name: string;
  checked: boolean;
  onChange: () => void;
  title: string;
  description: string;
  icon: "book" | "file" | "archive" | "plus" | "list";
  disabled?: boolean;
  disabledHint?: string;
  children?: ReactNode;
}) {
  return (
    <div className={cx("choice", checked && "is-checked", disabled && "is-disabled")}>
      <label className="choice-label">
        <input type="radio" name={name} checked={checked} disabled={disabled} onChange={onChange} />
        <Icon name={icon} size={18} className="choice-icon" />
        <span className="choice-text">
          <span className="choice-title">{title}</span>
          <span className="choice-description">{disabled && disabledHint ? disabledHint : description}</span>
        </span>
      </label>
      {checked && children && <div className="choice-body">{children}</div>}
    </div>
  );
}

function UploadItem({
  upload,
  format,
  onRemove,
}: {
  upload: Upload;
  format: ImportFormat;
  onRemove: () => void;
}) {
  const { t } = useI18n();
  const inspection = upload.inspection;
  const duplicate = inspection?.duplicate;
  const problems = [
    ...(upload.error ? [upload.error] : []),
    ...(inspection?.errors || []),
    ...(duplicate?.kind === "library"
      ? [t("Déjà dans votre bibliothèque : « {title} ».", { title: duplicate.title })]
      : duplicate?.kind === "batch"
        ? [t("Doublon de « {name} » dans cet import.", { name: duplicate.name })]
        : []),
  ];
  const tone = problems.length ? "danger" : inspection?.warnings.length ? "warning" : upload.state === "done" ? "success" : "neutral";
  const state =
    upload.state === "queued"
      ? t("En attente")
      : upload.state === "uploading"
        ? t("Envoi et analyse…")
        : upload.state === "done"
          ? t("Analysé")
          : upload.state === "rejected"
            ? t("Non envoyé")
            : t("Échec");
  const summary = inspection
    ? [
        inspection.title,
        format !== "epub" && inspection.chapter_number !== null
          ? t("chapitre {number}", { number: inspection.chapter_number })
          : "",
        format === "epub" && inspection.series ? t("Série déclarée : {series}", { series: inspection.series }) : "",
        format === "epub" && inspection.series_index !== null ? t("volume {number}", { number: inspection.series_index }) : "",
        inspection.language,
      ]
        .filter(Boolean)
        .join(" · ")
    : "";
  return (
    <li className={cx("wizard-file", `tone-border-${tone}`)}>
      <div className="wizard-file-main">
        <span className="wizard-file-name" title={upload.name}>
          {upload.name}
        </span>
        <span className="wizard-file-meta subtle tabular">
          {FORMAT_LABELS[format]} · {fileSize(upload.size)}
        </span>
        {summary && <span className="wizard-file-meta">{summary}</span>}
        {problems.map((text) => (
          <span key={text} className="tone-text-danger wizard-file-meta">
            {text}
          </span>
        ))}
        {inspection?.warnings.map((text) => (
          <span key={text} className="tone-text-warning wizard-file-meta">
            {text}
          </span>
        ))}
      </div>
      <span className={cx("wizard-file-state", `tone-text-${tone}`)}>
        {upload.state === "uploading" && <span className="spinner" aria-hidden="true" />} {state}
      </span>
      <IconButton
        icon="trash"
        size="sm"
        label={t("Retirer {name}", { name: upload.name })}
        disabled={upload.state === "uploading"}
        onClick={onRemove}
      />
    </li>
  );
}
