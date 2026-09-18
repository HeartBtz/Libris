import { useEffect, useLayoutEffect, useState } from "react";
import type { KeyboardEvent } from "react";
import { api, date, labels, send } from "../api";
import { formatNumber, registerTranslations, useI18n } from "../i18n";
import type { Chapter, Critique, LLMRequest, Project, Run, Segment, Unit, Version } from "../types";
import { useLeaveGuard, useUnsavedDraft } from "../unsaved";
import {
  Badge,
  Button,
  Callout,
  Dialog,
  EmptyState,
  Field,
  Icon,
  IconButton,
  Input,
  Menu,
  Select,
  Skeleton,
  StatusPill,
  TabPanel,
  Tabs,
  TextArea,
  cx,
  useDialogs,
  useMediaQuery,
} from "../ui";
import { MarkedText, MarkerTextarea, markers } from "./MarkedText";

registerTranslations({
  "Filtrer les passages": "Filter passages",
  "Tous les passages": "All passages",
  "À vérifier": "Needs review",
  Erreurs: "Errors",
  Incertitudes: "Uncertainties",
  "Refus du provider": "Provider refusals",
  "Originaux conservés": "Source retained",
  Prévisualiser: "Preview",
  "Instructions de la section": "Section instructions",
  "Consignes appliquées à tous les passages de cette section lors des prochaines traductions.":
    "Guidance applied to every passage of this section in the next translations.",
  Instructions: "Instructions",
  "Retraduire la section ?": "Retranslate the section?",
  "Les corrections humaines seront conservées et les résultats proposés dans l’historique.":
    "Human corrections will be kept and the results offered in the history.",
  "Retraduire la section": "Retranslate section",
  Source: "Source",
  Traduction: "Translation",
  "Aucun passage pour ce filtre.": "No passages for this filter.",
  "Essayez un autre filtre ou une autre section.": "Try another filter or section.",
  "Passages précédents": "Previous passages",
  "Passages {start}–{end}": "Passages {start}–{end}",
  "Passages suivants": "Next passages",
  "Prévisualisation du chapitre": "Chapter preview",
  "Rendu simplifié": "Simplified rendering",
  "Les passages non traduits restent en langue source. CSS simplifiée, scripts et ressources externes désactivés.":
    "Untranslated passages remain in the source language. Simplified CSS, scripts, and external resources are disabled.",
  "Rendu du chapitre": "Chapter rendering",
  "Sections du livre": "Book sections",
  "Masquer les sections": "Hide sections",
  "Afficher les sections": "Show sections",
  Analysé: "Analyzed",
  "Chargement des passages…": "Loading passages…",
  "Expression source": "Source expression",
  "Traduction à conserver": "Translation to keep",
  "Nouvelle expression source": "New source expression",
  "Nouvelle traduction": "New translation",
  "Ajouter et verrouiller": "Add and lock",
  "Terme enregistré. Les passages concernés sont marqués à réévaluer.":
    "Term saved. Affected passages are marked for reevaluation.",
  Glossaire: "Glossary",
  "Ajoutez une expression de ce passage au glossaire pour la conserver dans les traductions suivantes.":
    "Add an expression from this passage to the glossary to keep it in future translations.",
  "Retraduire avec une instruction": "Retranslate with an instruction",
  "Instruction pour cette retraduction": "Instruction for this retranslation",
  "Validé humainement": "Human-validated",
  "Correction humaine protégée": "Protected human correction",
  "Version {revision}": "Version {revision}",
  "Non enregistré": "Unsaved",
  "Traduction passage {passage} unité {unit}": "Translation passage {passage} unit {unit}",
  "La traduction apparaîtra ici…": "The translation will appear here…",
  "Une nouvelle version de ce passage est arrivée pendant votre saisie. Choisissez laquelle garder avant d’enregistrer.":
    "A new version of this passage arrived while you were typing. Choose which one to keep before saving.",
  "Recharger la version du serveur": "Reload the server version",
  "Conserver ma saisie": "Keep my text",
  "Original conservé par décision humaine. Ce passage n’est pas compté comme traduit ; utilisez l’export partiel ou saisissez une traduction.":
    "Source retained by human decision. This passage is not counted as translated; use the partial export or enter a translation.",
  "Refus enregistré. Vous pouvez saisir une traduction ou ouvrir l’inspecteur pour ajouter une analyse humaine.":
    "Refusal recorded. You can enter a translation or open the inspector to add a human analysis.",
  "Conserver l’original pour ce passage ?": "Keep the source text for this passage?",
  "Il restera signalé comme non traduit et sera disponible dans l’export partiel. Cela ne remplace pas une analyse humaine manquante.":
    "It will remain marked as untranslated and be available in the partial export. This does not replace missing human analysis.",
  "Conserver l’original pour l’export": "Retain source for export",
  "{count} incertitude": "{count} uncertainty",
  "{count} incertitudes": "{count} uncertainties",
  Enregistrer: "Save",
  Valider: "Validate",
  "Retraduire…": "Retranslate…",
  Retraduire: "Retranslate",
  "Avec plus de contexte": "With more context",
  "Avec une instruction…": "With an instruction…",
  "Consignes du passage…": "Passage guidance…",
  "Consignes du passage": "Passage guidance",
  "Consignes propres à ce passage, prises en compte lors de sa prochaine traduction.":
    "Guidance specific to this passage, used in its next translation.",
  "Consignes enregistrées.": "Guidance saved.",
  Consignes: "Guidance",
  "Contexte / historique / Ask AI": "Context / history / Ask AI",
  "Actions du passage {position}": "Passage {position} actions",
  "Les repères de mise en forme diffèrent de la source : conservez-les tels quels (⟦t0⟧…⟦/t0⟧).":
    "Formatting codes differ from the source: keep them as they are (⟦t0⟧…⟦/t0⟧).",
  "Les repères colorés encadrent une mise en forme du livre (italique, lien…) : laissez-les en place.":
    "Coloured codes wrap book formatting (italic, link…): leave them in place.",
  "tentative {attempt}/5": "attempt {attempt}/5",
  cache: "cache",
  provider: "provider",
  "Contexte réellement sélectionné / écarté": "Context actually selected / excluded",
  "Prompt final · {role}": "Final prompt · {role}",
  "Réponse interprétée": "Parsed response",
  "Réponse brute du provider": "Raw provider response",
  "Inspecteur du passage": "Passage inspector",
  "Passage {position}": "Passage {position}",
  "Fermer l’inspecteur": "Close inspector",
  "Sections de l’inspecteur": "Inspector sections",
  Contexte: "Context",
  Historique: "History",
  Critique: "Critique",
  "Analyse humaine": "Human analysis",
  "Requête enregistrée": "Saved request",
  "Aucune requête enregistrée pour ce passage.": "No saved request for this passage.",
  "Aucune version enregistrée.": "No saved version.",
  "Aucune critique pour ce passage.": "No critique for this passage.",
  "Appliquée lors de sa création": "Applied when created",
  Proposition: "Proposal",
  "Restaurer comme correction humaine": "Restore as human correction",
  "Résumez les informations nécessaires à la continuité : événements, personnages et références. Cette saisie sera enregistrée comme une analyse humaine, sans appel au modèle.":
    "Summarize the information needed for continuity: events, characters, and references. This input will be saved as a human analysis without calling the model.",
  "Texte source du passage": "Passage source text",
  "Résumé humain": "Human summary",
  "Enregistrer l’analyse humaine": "Save human analysis",
  "Analyse enregistrée. Vous pouvez reprendre le travail.": "Analysis saved. You can resume work.",
  "Analyse linguistique contextualisée ; aucune modification automatique du passage.":
    "Contextualized linguistic analysis; no automatic changes to the passage.",
  "Phrase sélectionnée (facultatif)": "Selected sentence (optional)",
  "Collez la phrase à examiner": "Paste the sentence to examine",
  Question: "Question",
  "Donne-moi trois variantes qui préservent le double sens.":
    "Give me three variants that preserve the double meaning.",
  "Consultation du modèle…": "Consulting the model…",
  "Demander à l’IA": "Ask AI",
  "Réponse de l’IA": "AI answer",
  Suggestion: "Suggestion",
});

const PAGE_SIZE = 50;

export function Editor({
  project,
  chapters,
  chapterId,
  onChapter,
  tick,
  run,
  refresh,
  focusRefusal,
}: {
  project: Project;
  chapters: Chapter[];
  chapterId: string;
  onChapter: (id: string) => void;
  tick: number;
  run: Run;
  refresh: () => void;
  focusRefusal?: string;
}) {
  const { t } = useI18n();
  const { confirm, prompt } = useDialogs();
  const confirmLeave = useLeaveGuard();
  const narrow = useMediaQuery("(max-width: 900px)");
  const [collapsed, setCollapsed] = useState(false);
  const [filter, setFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [loaded, setLoaded] = useState<{ key: string; items: Segment[] } | null>(null);
  const [selected, setSelected] = useState<Segment | null>(null);
  const [preview, setPreview] = useState("");
  const chapter = chapters.find((item) => item.id === chapterId);
  const key = `${chapterId}|${filter}|${offset}`;
  useEffect(() => {
    if (focusRefusal) {
      setFilter("refused");
      setOffset(0);
    }
  }, [focusRefusal]);
  useEffect(() => {
    setOffset(0);
    setSelected(null);
  }, [chapterId]);
  useEffect(() => {
    if (!chapterId) return;
    // A slow answer for the previous chapter, filter or page must never replace the current list.
    let active = true;
    void run.background(async () => {
      const items = await api<Segment[]>(
        `/projects/${project.id}/segments?chapter_id=${chapterId}&status=${filter}&offset=${offset}&limit=${PAGE_SIZE}`,
      );
      if (active) setLoaded({ key, items });
    });
    return () => {
      active = false;
    };
  }, [project.id, chapterId, filter, offset, tick, run, key]);
  const segments = loaded?.key === key ? loaded.items : null;
  const lastSegments = loaded?.items || [];
  if (!chapter) return null;
  const guarded = async (change: () => void) => {
    if (await confirmLeave()) change();
  };
  const sectionList = (
    <nav className="chapter-nav" aria-label={t("Sections du livre")}>
      <div className="chapter-nav-header">
        <span>{t("Sections du livre")}</span>
        <span className="subtle tabular">{chapters.length}</span>
      </div>
      <ol>
        {chapters.map((c) => (
          <li key={c.id}>
            <button
              type="button"
              className="chapter-item"
              aria-current={chapterId === c.id ? "true" : undefined}
              onClick={() => onChapter(c.id)}
            >
              <span className="chapter-number tabular">{String(c.position + 1).padStart(2, "0")}</span>
              <span className="chapter-title">{c.title}</span>
              {c.analyzed && <span className="chapter-dot" title={t("Analysé")} />}
            </button>
          </li>
        ))}
      </ol>
    </nav>
  );
  return (
    <section className={cx("editor", collapsed && "chapters-collapsed")}>
      {!narrow && !collapsed && sectionList}
      <div className="editor-main">
        <div className="editor-toolbar">
          {!narrow && (
            <IconButton
              icon="sidebar"
              size="sm"
              label={collapsed ? t("Afficher les sections") : t("Masquer les sections")}
              aria-expanded={!collapsed}
              onClick={() => setCollapsed((value) => !value)}
            />
          )}
          {narrow ? (
            <label className="chapter-select">
              <span className="sr-only">{t("Sections du livre")}</span>
              <Select value={chapterId} onChange={(event) => onChapter(event.target.value)}>
                {chapters.map((item) => (
                  <option key={item.id} value={item.id}>
                    {String(item.position + 1).padStart(2, "0")} · {item.title}
                  </option>
                ))}
              </Select>
            </label>
          ) : (
            <h2 className="editor-chapter-title">{chapter.title}</h2>
          )}
          <div className="editor-toolbar-actions">
            <label className="editor-filter">
              <span className="sr-only">{t("Filtrer les passages")}</span>
              <Select
                value={filter}
                onChange={(e) => {
                  const value = e.target.value;
                  void guarded(() => {
                    setFilter(value);
                    setOffset(0);
                  });
                }}
              >
                <option value="">{t("Tous les passages")}</option>
                <option value="check">{t("À vérifier")}</option>
                <option value="error">{t("Erreurs")}</option>
                <option value="uncertain">{t("Incertitudes")}</option>
                <option value="refused">{t("Refus du provider")}</option>
                <option value="source_retained">{t("Originaux conservés")}</option>
              </Select>
            </label>
            <Button
              size="sm"
              icon="eye"
              onClick={() =>
                void run(async () => {
                  const r = await api<{ html: string }>(`/projects/${project.id}/preview/${chapter.id}`);
                  setPreview(r.html);
                })
              }
            >
              {t("Prévisualiser")}
            </Button>
            <Menu
              label={t("Instructions")}
              trigger={(props) => <IconButton {...props} icon="more" size="sm" variant="secondary" label={t("Instructions")} />}
              items={[
                {
                  label: t("Instructions de la section"),
                  icon: "message",
                  hint: chapter.instructions ? "●" : undefined,
                  onSelect: () =>
                    void (async () => {
                      const instructions = await prompt({
                        title: t("Instructions de la section"),
                        message: t(
                          "Consignes appliquées à tous les passages de cette section lors des prochaines traductions.",
                        ),
                        label: chapter.title,
                        initialValue: chapter.instructions,
                        confirmLabel: t("Enregistrer"),
                      });
                      if (instructions === null) return;
                      await run(async () => {
                        await send(`/projects/${project.id}/chapters/${chapter.id}/instructions`, { instructions }, "PUT");
                        refresh();
                      });
                    })(),
                },
                {
                  label: t("Retraduire la section"),
                  icon: "refresh",
                  onSelect: () =>
                    void (async () => {
                      const accepted = await confirm({
                        title: t("Retraduire la section ?"),
                        message: t(
                          "Les corrections humaines seront conservées et les résultats proposés dans l’historique.",
                        ),
                        confirmLabel: t("Retraduire"),
                      });
                      if (!accepted) return;
                      await run(async () => {
                        await send(`/projects/${project.id}/jobs`, {
                          operation: "translate",
                          chapter_id: chapter.id,
                          force: true,
                        });
                        refresh();
                      });
                    })(),
                },
              ]}
            />
          </div>
        </div>
        <div className="column-labels" aria-hidden="true">
          <span>
            {t("Source")} · {project.source_language.toUpperCase()}
          </span>
          <span>
            {t("Traduction")} · {project.target_language.toUpperCase()}
          </span>
        </div>
        <div className="parallel-text">
          {segments === null ? (
            <SegmentSkeleton label={t("Chargement des passages…")} />
          ) : segments.length ? (
            segments.map((s) => (
              <SegmentRow key={s.id} segment={s} project={project} run={run} refresh={refresh} inspect={() => setSelected(s)} />
            ))
          ) : (
            <EmptyState
              compact
              icon="search"
              title={t("Aucun passage pour ce filtre.")}
              description={t("Essayez un autre filtre ou une autre section.")}
            />
          )}
        </div>
        {(offset > 0 || lastSegments.length === PAGE_SIZE) && (
          <div className="pagination">
            <Button
              size="sm"
              variant="ghost"
              icon="chevronLeft"
              disabled={offset === 0}
              onClick={() => void guarded(() => setOffset((v) => Math.max(0, v - PAGE_SIZE)))}
            >
              {t("Passages précédents")}
            </Button>
            <span className="subtle tabular">
              {t("Passages {start}–{end}", { start: offset + 1, end: offset + lastSegments.length })}
            </span>
            <Button
              size="sm"
              variant="ghost"
              iconAfter="chevronRight"
              disabled={lastSegments.length < PAGE_SIZE}
              onClick={() => void guarded(() => setOffset((v) => v + PAGE_SIZE))}
            >
              {t("Passages suivants")}
            </Button>
          </div>
        )}
      </div>
      {selected && (
        <Inspector segment={selected} project={project} run={run} close={() => setSelected(null)} refresh={refresh} />
      )}
      <Dialog
        open={!!preview}
        onClose={() => setPreview("")}
        title={t("Rendu simplifié")}
        description={t(
          "Les passages non traduits restent en langue source. CSS simplifiée, scripts et ressources externes désactivés.",
        )}
        ariaLabel={t("Prévisualisation du chapitre")}
        size="xl"
        className="preview-dialog"
      >
        <iframe
          title={t("Rendu du chapitre")}
          sandbox="allow-same-origin"
          srcDoc={preview}
          onLoad={(event) => {
            event.currentTarget.contentDocument?.addEventListener("keydown", (frameEvent) => {
              if (frameEvent.key === "Escape") {
                frameEvent.preventDefault();
                setPreview("");
              }
            });
          }}
        />
      </Dialog>
    </section>
  );
}

function SegmentSkeleton({ label }: { label: string }) {
  return (
    <div role="status" aria-label={label}>
      {Array.from({ length: 3 }, (_, index) => (
        <div key={index} className="segment-row segment-skeleton">
          <div className="segment-source stack-sm">
            <Skeleton width="30%" height={10} />
            <Skeleton width="95%" height={14} />
            <Skeleton width="80%" height={14} />
          </div>
          <div className="segment-translation stack-sm">
            <Skeleton width="30%" height={10} />
            <Skeleton width="100%" height={56} />
          </div>
        </div>
      ))}
    </div>
  );
}

export interface SuggestionDraft {
  unitId: string;
  text: string;
  nonce: number;
}

export function SegmentRow({
  segment,
  project,
  run,
  refresh,
  inspect,
  suggestion,
  hideSource = false,
}: {
  segment: Segment;
  project: Project;
  run: Run;
  refresh: () => void;
  inspect: () => void;
  suggestion?: SuggestionDraft;
  hideSource?: boolean;
}) {
  const { t, tp } = useI18n();
  const { confirm, prompt } = useDialogs();
  const fromServer = () =>
    segment.units.map((u) => ({
      id: u.id,
      text: segment.translated_units.find((t) => t.id === u.id)?.text || "",
    }));
  const [units, setUnits] = useState<Unit[]>(fromServer);
  const [base, setBase] = useState(segment.revision);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  useUnsavedDraft(`segment-${segment.id}`, dirty);
  useLayoutEffect(() => {
    if (!dirty) {
      setUnits(fromServer());
      setBase(segment.revision);
    }
  }, [segment.revision]);
  useEffect(() => {
    if (!suggestion) return;
    setUnits((current) => current.map((unit) => (unit.id === suggestion.unitId ? { ...unit, text: suggestion.text } : unit)));
    setDirty(true);
  }, [suggestion?.nonce]);
  const complete = units.every((u) => u.text);
  const markerMismatch = units.some(
    (unit, i) => unit.text && markers(unit.text).join() !== markers(segment.units[i]?.text || "").join(),
  );
  const hasMarkers = segment.units.some((unit) => markers(unit.text).length > 0);
  async function save(validated: boolean) {
    if (busy || !complete) return;
    setBusy(true);
    await run(async () => {
      const saved = await send<Segment>(`/segments/${segment.id}`, { revision: base, units, validated }, "PUT");
      setUnits(saved.translated_units);
      setBase(saved.revision);
      setDirty(false);
      refresh();
    });
    setBusy(false);
  }
  async function translate(deep = false, custom = false) {
    let instruction = "";
    if (custom) {
      const value = await prompt({
        title: t("Retraduire avec une instruction"),
        label: t("Instruction pour cette retraduction"),
        confirmLabel: t("Retraduire"),
        required: true,
      });
      if (value === null) return;
      instruction = value;
    }
    await run(async () => {
      await send(`/projects/${project.id}/jobs`, {
        operation: "translate",
        segment_id: segment.id,
        force: true,
        deep,
        instruction,
      });
      refresh();
    });
  }
  async function editInstructions() {
    const value = await prompt({
      title: t("Consignes du passage"),
      message: t("Consignes propres à ce passage, prises en compte lors de sa prochaine traduction."),
      label: t("Passage {position}", { position: segment.position + 1 }),
      initialValue: segment.instructions,
      confirmLabel: t("Enregistrer"),
    });
    if (value === null) return;
    await run(async () => {
      await send(`/segments/${segment.id}/instructions`, { instructions: value }, "PUT");
      refresh();
    });
  }
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (!(event.ctrlKey || event.metaKey)) return;
    if (event.key === "s" || event.key === "S") {
      event.preventDefault();
      void save(false);
    } else if (event.key === "Enter") {
      event.preventDefault();
      void save(true);
    }
  };
  const mac = /Mac|iPhone|iPad/.test(navigator.platform);
  const mod = mac ? "⌘" : "Ctrl";
  return (
    <article
      className={cx("segment-row", `status-${segment.status}`, dirty && "is-dirty", hideSource && "segment-row-single")}
      id={`segment-${segment.id}`}
      onKeyDown={onKeyDown}
    >
      {!hideSource && (
        <div className="segment-source">
          <div className="segment-meta">
            <span className="segment-number tabular">§ {segment.position + 1}</span>
            <span className="mobile-column-label">
              {t("Source")} · {project.source_language.toUpperCase()}
            </span>
          </div>
          <div className="book-text source-text">
            {segment.units.map((u) => (
              <p key={u.id}>
                <MarkedText text={u.text} />
              </p>
            ))}
          </div>
        </div>
      )}
      <div className="segment-translation">
        <div className="segment-meta">
          <span className="mobile-column-label">
            {t("Traduction")} · {project.target_language.toUpperCase()}
          </span>
          <StatusPill
            status={segment.validated ? "validated" : segment.status}
            label={segment.validated ? t("Validé humainement") : labels[segment.status]}
          />
          <span className="subtle">
            {segment.human ? t("Correction humaine protégée") : t("Version {revision}", { revision: segment.revision })}
          </span>
          {segment.instructions && (
            <Badge tone="info" title={segment.instructions}>
              {t("Consignes")}
            </Badge>
          )}
          {dirty && <Badge tone="warning">{t("Non enregistré")}</Badge>}
        </div>
        {units.map((u, i) => (
          <MarkerTextarea
            key={u.id}
            aria-label={t("Traduction passage {passage} unité {unit}", { passage: segment.position + 1, unit: i + 1 })}
            value={u.text}
            className="book-text"
            placeholder={t("La traduction apparaîtra ici…")}
            onChange={(e) => {
              const text = e.target.value;
              setDirty(true);
              setUnits((current) => current.map((v, j) => (j === i ? { ...v, text } : v)));
            }}
          />
        ))}
        {markerMismatch ? (
          <p className="segment-hint tone-text-warning">
            <Icon name="alert" size={14} />
            {t("Les repères de mise en forme diffèrent de la source : conservez-les tels quels (⟦t0⟧…⟦/t0⟧).")}
          </p>
        ) : (
          hasMarkers &&
          dirty && (
            <p className="segment-hint">
              <Icon name="info" size={14} />
              {t("Les repères colorés encadrent une mise en forme du livre (italique, lien…) : laissez-les en place.")}
            </p>
          )
        )}
        {dirty && base !== segment.revision && (
          <Callout
            tone="warning"
            role="status"
            actions={
              <>
                <Button
                  size="sm"
                  onClick={() => {
                    setUnits(fromServer());
                    setBase(segment.revision);
                    setDirty(false);
                  }}
                >
                  {t("Recharger la version du serveur")}
                </Button>
                <Button size="sm" onClick={() => setBase(segment.revision)}>
                  {t("Conserver ma saisie")}
                </Button>
              </>
            }
          >
            {t(
              "Une nouvelle version de ce passage est arrivée pendant votre saisie. Choisissez laquelle garder avant d’enregistrer.",
            )}
          </Callout>
        )}
        {segment.error && (
          <Callout tone="danger" className="segment-callout">
            {segment.error}
          </Callout>
        )}
        {segment.retained_source && (
          <Callout tone="neutral" className="segment-callout">
            {t(
              "Original conservé par décision humaine. Ce passage n’est pas compté comme traduit ; utilisez l’export partiel ou saisissez une traduction.",
            )}
          </Callout>
        )}
        {segment.status === "refused" && (
          <Callout
            tone="warning"
            className="segment-callout"
            actions={
              <Button
                size="sm"
                onClick={() =>
                  void (async () => {
                    const accepted = await confirm({
                      title: t("Conserver l’original pour ce passage ?"),
                      message: t(
                        "Il restera signalé comme non traduit et sera disponible dans l’export partiel. Cela ne remplace pas une analyse humaine manquante.",
                      ),
                      confirmLabel: t("Conserver l’original pour l’export"),
                    });
                    if (!accepted) return;
                    await run(async () => {
                      await send(`/segments/${segment.id}/retain-source`, { revision: segment.revision });
                      refresh();
                    });
                  })()
                }
              >
                {t("Conserver l’original pour l’export")}
              </Button>
            }
          >
            {t(
              "Refus enregistré. Vous pouvez saisir une traduction ou ouvrir l’inspecteur pour ajouter une analyse humaine.",
            )}
          </Callout>
        )}
        {!!segment.uncertainties.length && (
          <details className="disclosure disclosure-plain segment-uncertainties">
            <summary>
              {tp(segment.uncertainties.length, "{count} incertitude", "{count} incertitudes")}
            </summary>
            <ul>
              {segment.uncertainties.map((u, i) => (
                <li key={i}>{u}</li>
              ))}
            </ul>
          </details>
        )}
        <div className="segment-actions" role="group" aria-label={t("Actions du passage {position}", { position: segment.position + 1 })}>
          <Button size="sm" disabled={busy || !complete || !dirty} onClick={() => void save(false)} shortcut={`${mod} S`}>
            {t("Enregistrer")}
          </Button>
          <Button
            size="sm"
            variant={segment.validated && !dirty ? "ghost" : "secondary"}
            icon="check"
            disabled={busy || !complete}
            onClick={() => void save(true)}
            shortcut={`${mod} ↵`}
          >
            {t("Valider")}
          </Button>
          <Menu
            label={t("Retraduire…")}
            trigger={(props) => (
              <Button {...props} size="sm" variant="ghost" icon="refresh" iconAfter="chevronDown">
                {t("Retraduire…")}
              </Button>
            )}
            items={[
              { label: t("Retraduire"), icon: "refresh", onSelect: () => void translate() },
              { label: t("Avec plus de contexte"), icon: "sparkles", onSelect: () => void translate(true) },
              { label: t("Avec une instruction…"), icon: "message", onSelect: () => void translate(false, true) },
              { kind: "separator" },
              { label: t("Consignes du passage…"), icon: "edit", onSelect: () => void editInstructions() },
            ]}
          />
          <Button
            size="sm"
            variant="ghost"
            icon="history"
            onClick={inspect}
            className="segment-inspect"
            aria-label={t("Contexte / historique / Ask AI")}
          >
            {t("Contexte")}
          </Button>
        </div>
      </div>
    </article>
  );
}

export function RequestDetails({ request }: { request: LLMRequest }) {
  const { t } = useI18n();
  return (
    <div className="stack">
      <div className="request-summary">
        <Badge>{request.model}</Badge>
        <span>{formatNumber(request.duration, { maximumFractionDigits: 1 })} s</span>
        <span>{t("tentative {attempt}/5", { attempt: request.attempt })}</span>
        <span>{request.cached ? t("cache") : t("provider")}</span>
      </div>
      {request.error && <Callout tone="danger">{request.error}</Callout>}
      <details className="disclosure" open>
        <summary>{t("Contexte réellement sélectionné / écarté")}</summary>
        <pre>{JSON.stringify(request.context, null, 2)}</pre>
      </details>
      {request.messages?.map((m, i) => (
        <details className="disclosure" key={i}>
          <summary>{t("Prompt final · {role}", { role: m.role })}</summary>
          <pre>{m.content}</pre>
        </details>
      ))}
      <details className="disclosure">
        <summary>{t("Réponse interprétée")}</summary>
        <pre>{JSON.stringify(request.parsed, null, 2)}</pre>
      </details>
      <details className="disclosure">
        <summary>{t("Réponse brute du provider")}</summary>
        <pre>{JSON.stringify(request.raw, null, 2)}</pre>
      </details>
    </div>
  );
}

function CritiqueList({ critique }: { critique: Critique[] }) {
  const { t } = useI18n();
  if (!critique.length) return <p className="muted">{t("Aucune critique pour ce passage.")}</p>;
  return (
    <ul className="critique-list">
      {critique.map((item, index) => (
        <li key={index} className="critique-card">
          <div className="row">
            <Badge tone={item.severity === "error" ? "danger" : "warning"}>{item.category}</Badge>
          </div>
          <p>{item.description}</p>
          {item.suggestion && (
            <blockquote className="book-text">
              <span className="subtle">{t("Suggestion")}</span>
              {item.suggestion}
            </blockquote>
          )}
        </li>
      ))}
    </ul>
  );
}

export function Inspector({
  segment,
  project,
  run,
  close,
  refresh,
}: {
  segment: Segment;
  project: Project;
  run: Run;
  close: () => void;
  refresh: () => void;
}) {
  const { t } = useI18n();
  const [tab, setTab] = useState("context");
  const [requests, setRequests] = useState<LLMRequest[] | null>(null);
  const [versions, setVersions] = useState<Version[] | null>(null);
  const [index, setIndex] = useState(0);
  const [question, setQuestion] = useState("");
  const [selection, setSelection] = useState("");
  const [answer, setAnswer] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [humanSummary, setHumanSummary] = useState("");
  const [analysisSaved, setAnalysisSaved] = useState(false);
  const [glossarySource, setGlossarySource] = useState("");
  const [glossaryTranslation, setGlossaryTranslation] = useState("");
  const [glossarySaved, setGlossarySaved] = useState(false);
  useEffect(() => {
    void run.background(async () => {
      const [r, v] = await Promise.all([
        api<LLMRequest[]>(`/segments/${segment.id}/requests`),
        api<Version[]>(`/segments/${segment.id}/versions`),
      ]);
      setRequests(r);
      setVersions(v);
    });
  }, [run, segment.id]);
  const answerText =
    answer && typeof answer === "object" && "answer" in answer && typeof (answer as { answer: unknown }).answer === "string"
      ? (answer as { answer: string }).answer
      : null;
  return (
    <Dialog
      open
      onClose={close}
      variant="sheet"
      size="lg"
      ariaLabel={t("Inspecteur du passage")}
      closeLabel={t("Fermer l’inspecteur")}
      title={t("Passage {position}", { position: segment.position + 1 })}
      className="inspector"
    >
      <Tabs
        items={[
          { id: "context", label: t("Contexte") },
          { id: "history", label: t("Historique") },
          { id: "review", label: t("Critique") },
          { id: "ask", label: "Ask AI" },
          { id: "analysis", label: t("Analyse humaine") },
          { id: "glossary", label: t("Glossaire") },
        ]}
        value={tab}
        onChange={setTab}
        label={t("Sections de l’inspecteur")}
        idPrefix="inspector"
      />
      <TabPanel idPrefix="inspector" value={tab} className="inspector-panel">
        {tab === "context" ? (
          requests === null ? (
            <Skeleton height={120} />
          ) : requests.length ? (
            <div className="stack">
              <Field label={t("Requête enregistrée")}>
                <Select value={index} onChange={(e) => setIndex(+e.target.value)}>
                  {requests.map((r, i) => (
                    <option key={r.id} value={i}>
                      {r.operation} · {date(r.created_at)} · {labels[r.status]}
                    </option>
                  ))}
                </Select>
              </Field>
              <RequestDetails request={requests[index]} />
            </div>
          ) : (
            <EmptyState compact icon="history" title={t("Aucune requête enregistrée pour ce passage.")} />
          )
        ) : tab === "history" ? (
          versions === null ? (
            <Skeleton height={120} />
          ) : versions.length ? (
            <ol className="version-list">
              {versions.map((v) => (
                <li className="version-item" key={v.id}>
                  <div className="row-between">
                    <strong>
                      {v.origin} · {v.applied ? t("Appliquée lors de sa création") : t("Proposition")}
                    </strong>
                    <small className="subtle">{date(v.created_at)}</small>
                  </div>
                  <p className="book-text version-text">{v.units.map((u) => u.text).join("\n\n")}</p>
                  <div>
                    <Button
                      size="sm"
                      onClick={() =>
                        void run(async () => {
                          const current = await api<Segment>(`/segments/${segment.id}`);
                          await send(`/segments/${segment.id}/versions/${v.id}/restore`, {
                            revision: current.revision,
                            units: [],
                            validated: false,
                          });
                          refresh();
                          close();
                        })
                      }
                    >
                      {t("Restaurer comme correction humaine")}
                    </Button>
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <EmptyState compact icon="history" title={t("Aucune version enregistrée.")} />
          )
        ) : tab === "review" ? (
          <CritiqueList critique={segment.critique} />
        ) : tab === "analysis" ? (
          <div className="stack">
            <p className="muted">
              {t(
                "Résumez les informations nécessaires à la continuité : événements, personnages et références. Cette saisie sera enregistrée comme une analyse humaine, sans appel au modèle.",
              )}
            </p>
            <details className="disclosure">
              <summary>{t("Texte source du passage")}</summary>
              <p className="book-text">
                <MarkedText text={segment.source} />
              </p>
            </details>
            <form
              className="stack"
              onSubmit={(e) => {
                e.preventDefault();
                void run(async () => {
                  await send(`/segments/${segment.id}/analysis`, { summary: humanSummary }, "PUT");
                  setAnalysisSaved(true);
                  refresh();
                });
              }}
            >
              <Field label={t("Résumé humain")}>
                <TextArea rows={8} value={humanSummary} required onChange={(e) => setHumanSummary(e.target.value)} />
              </Field>
              <div>
                <Button type="submit" variant="primary">
                  {t("Enregistrer l’analyse humaine")}
                </Button>
              </div>
            </form>
            {analysisSaved && (
              <Callout tone="success" role="status">
                {t("Analyse enregistrée. Vous pouvez reprendre le travail.")}
              </Callout>
            )}
          </div>
        ) : tab === "glossary" ? (
          <div className="stack">
            <p className="muted">
              {t(
                "Ajoutez une expression de ce passage au glossaire pour la conserver dans les traductions suivantes.",
              )}
            </p>
            <form
              className="stack"
              onSubmit={(e) => {
                e.preventDefault();
                setBusy(true);
                void run(async () => {
                  await send(`/projects/${project.id}/glossary`, {
                    source: glossarySource,
                    translation: glossaryTranslation,
                    locked: true,
                    accepted: true,
                  });
                  setGlossarySource("");
                  setGlossaryTranslation("");
                  setGlossarySaved(true);
                  refresh();
                }).finally(() => setBusy(false));
              }}
            >
              <Field label={t("Nouvelle expression source")}>
                <Input
                  placeholder={t("Expression source")}
                  value={glossarySource}
                  onChange={(e) => setGlossarySource(e.target.value)}
                  required
                />
              </Field>
              <Field label={t("Nouvelle traduction")}>
                <Input
                  placeholder={t("Traduction à conserver")}
                  value={glossaryTranslation}
                  onChange={(e) => setGlossaryTranslation(e.target.value)}
                  required
                />
              </Field>
              <div>
                <Button type="submit" variant="primary" icon="lock" loading={busy}>
                  {t("Ajouter et verrouiller")}
                </Button>
              </div>
            </form>
            {glossarySaved && (
              <Callout tone="success" role="status">
                {t("Terme enregistré. Les passages concernés sont marqués à réévaluer.")}
              </Callout>
            )}
          </div>
        ) : (
          <div className="stack">
            <p className="muted">
              {t("Analyse linguistique contextualisée ; aucune modification automatique du passage.")}
            </p>
            <form
              className="stack"
              onSubmit={(e) => {
                e.preventDefault();
                setBusy(true);
                void run(async () => setAnswer(await send(`/segments/${segment.id}/ask`, { question, selection }))).finally(
                  () => setBusy(false),
                );
              }}
            >
              <Field label={t("Phrase sélectionnée (facultatif)")}>
                <TextArea
                  rows={3}
                  value={selection}
                  onChange={(e) => setSelection(e.target.value)}
                  placeholder={t("Collez la phrase à examiner")}
                />
              </Field>
              <Field label={t("Question")}>
                <TextArea
                  rows={3}
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  placeholder={t("Donne-moi trois variantes qui préservent le double sens.")}
                  required
                />
              </Field>
              <div>
                <Button type="submit" variant="primary" icon="sparkles" loading={busy}>
                  {busy ? t("Consultation du modèle…") : t("Demander à l’IA")}
                </Button>
              </div>
            </form>
            {answer != null &&
              (answerText ? (
                <div className="ai-answer">
                  <strong>{t("Réponse de l’IA")}</strong>
                  <p>{answerText}</p>
                </div>
              ) : (
                <pre>{JSON.stringify(answer, null, 2)}</pre>
              ))}
          </div>
        )}
      </TabPanel>
    </Dialog>
  );
}
