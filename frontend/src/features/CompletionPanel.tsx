import { useEffect, useState } from "react";
import { api, labels, send } from "../api";
import { registerTranslations, useI18n } from "../i18n";
import type { Project, Provider, Run } from "../types";

const translations: Record<string, string> = {
  "Chargement du bilan…": "Loading summary…",
  "Finir le livre": "Finish the book",
  "Bilan & récupération": "Summary & recovery",
  "Actualiser le bilan": "Refresh summary",
  "Traduction complète": "Translation complete",
  "Traduction incomplète": "Translation incomplete",
  "Sélectionnez les passages ci-dessous puis relancez-les avec le provider du livre ou un provider de remplacement.": "Select the segments below, then retry them with the book's provider or a replacement provider.",
  "passages traduits": "segments translated",
  "manquants": "missing",
  "conservés en original": "retained in the original",
  "à vérifier": "to review",
  "alertes non résolues": "unresolved alerts",
  "choix humains protégés": "protected human choices",
  "Dernier travail :": "Last job:",
  "Un travail est encore actif ou en attente.": "A job is still active or pending.",
  "Aucun travail actif.": "No active job.",
  "La conformité EPUB est vérifiée lors de l’export ; une couverture complète ne garantit pas la qualité littéraire.": "EPUB compliance is checked during export; complete coverage does not guarantee literary quality.",
  "Résultat de la revue finale": "Final review result",
  "Examinés": "Reviewed",
  "Résolus": "Resolved",
  "Corrigés": "Corrected",
  "Restants": "Remaining",
  "Protégés": "Protected",
  "Échecs": "Failures",
  "Passages à récupérer": "Segments to recover",
  "Filtrer les passages": "Filter segments",
  "Tous": "All",
  "Erreurs": "Errors",
  "Refus": "Refusals",
  "Non commencés": "Not started",
  "Bloqués": "Blocked",
  "Provider de récupération": "Recovery provider",
  "Choisir": "Choose",
  "Sélectionner les passages affichés (200 max)": "Select displayed segments (200 max)",
  "Désélectionner": "Deselect",
  "Récupération mise en file. Seuls les passages sélectionnés seront traités.": "Recovery queued. Only the selected segments will be processed.",
  "Mise en file…": "Queuing…",
  "Relancer la sélection": "Retry selection",
  "Aucun passage à récupérer pour ce filtre.": "No segments to recover for this filter.",
  "Passage": "Segment",
  "Choix humain protégé : récupération automatique désactivée.": "Protected human choice: automatic recovery disabled.",
};

registerTranslations(translations);

interface Report {
  total: number;
  translated: number;
  missing: number;
  retained: number;
  coverage_complete: boolean;
  flagged: number;
  issues: number;
  protected: number;
  processing: boolean;
  last_job_status: string;
  recovery: {
    id: string;
    position: number;
    chapter: string;
    status: string;
    error: string;
    excerpt: string;
    eligible: boolean;
  }[];
}

export function CompletionPanel({
  project,
  run,
  refresh,
  tick = 0,
}: {
  project: Project;
  run: Run;
  refresh: () => void;
  tick?: number;
}) {
  const { t } = useI18n();
  const [report, setReport] = useState<Report | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [provider, setProvider] = useState(project.provider_id || "");
  const [selected, setSelected] = useState<string[]>([]);
  const [filter, setFilter] = useState("all");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [message, setMessage] = useState("");
  useEffect(() => {
    let active = true;
    void run.background(async () => {
      const [data, list] = await Promise.all([
        api<Report>(`/projects/${project.id}/completion`),
        api<Provider[]>("/providers"),
      ]);
      if (active) {
        setReport(data);
        setProviders(list);
        setSelected((ids) =>
          ids.filter((id) =>
            data.recovery.some((s) => s.id === id && s.eligible),
          ),
        );
      }
    });
    return () => {
      active = false;
    };
  }, [project.id, reload, tick, run]);
  if (!report) return <p role="status">{t("Chargement du bilan…")}</p>;
  const visible = report.recovery.filter(
    (s) => filter === "all" || s.status === filter,
  );
  return (
    <section className="validation-panel">
      <div className="validation-heading">
        <div>
          <p className="eyebrow">{t("Finir le livre")}</p>
          <h2>{t("Bilan & récupération")}</h2>
        </div>
        <button onClick={() => setReload((x) => x + 1)}>
          {t("Actualiser le bilan")}
        </button>
      </div>
      <div className="notice">
        <h3>
          {report.coverage_complete
            ? t("Traduction complète")
            : t("Traduction incomplète")}
        </h3>
        {!!report.recovery.length && !report.processing && (
          <p>
            {t("Sélectionnez les passages ci-dessous puis relancez-les avec le provider du livre ou un provider de remplacement.")}
          </p>
        )}
        <p>
          {report.translated} / {report.total} {t("passages traduits")} · {report.missing} {t("manquants")} · {report.retained} {t("conservés en original")}
        </p>
        <p>
          {report.flagged} {t("à vérifier")} · {report.issues} {t("alertes non résolues")} · {report.protected} {t("choix humains protégés")}
        </p>
        <p>
          {t("Dernier travail :")} {" "}
          {labels[report.last_job_status] || report.last_job_status}.{" "}
          {report.processing
            ? t("Un travail est encore actif ou en attente.")
            : t("Aucun travail actif.")}
        </p>
        <p className="muted">
          {t("La conformité EPUB est vérifiée lors de l’export ; une couverture complète ne garantit pas la qualité littéraire.")}
        </p>
      </div>
      {project.progress && (
        <div
          className="review-outcome"
          aria-label={t("Résultat de la revue finale")}
        >
          {[
            [t("Examinés"), project.progress.review.examined],
            [t("Résolus"), project.progress.review.resolved],
            [t("Corrigés"), project.progress.review.revised],
            [t("Restants"), project.progress.review.remaining],
            [t("Protégés"), project.progress.review.protected],
            [t("Échecs"), project.progress.review.failed],
          ].map(([label, value]) => (
            <span key={label}>
              <strong>{value}</strong>
              <small>{label}</small>
            </span>
          ))}
        </div>
      )}
      <h3>{t("Passages à récupérer")}</h3>
      <div className="actions">
        <label>
          {t("Filtrer les passages")}
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="all">{t("Tous")}</option>
            <option value="error">{t("Erreurs")}</option>
            <option value="refused">{t("Refus")}</option>
            <option value="pending">{t("Non commencés")}</option>
            <option value="blocked">{t("Bloqués")}</option>
          </select>
        </label>
        <label>
          {t("Provider de récupération")}
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
          >
            <option value="">{t("Choisir")}</option>
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} · {p.model}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="actions">
        <button
          disabled={busy || !visible.some((s) => s.eligible)}
          onClick={() =>
            setSelected(
              visible
                .filter((s) => s.eligible)
                .slice(0, 200)
                .map((s) => s.id),
            )
          }
        >
          {t("Sélectionner les passages affichés (200 max)")}
        </button>
        <button
          disabled={!selected.length || busy}
          onClick={() => setSelected([])}
        >
          {t("Désélectionner")}
        </button>
        <button
          className="primary"
          disabled={
            busy ||
            report.processing ||
            !provider ||
            !selected.length ||
            selected.length > 200
          }
          onClick={() => {
            setBusy(true);
            setMessage("");
            void run(async () => {
              try {
                await send(`/projects/${project.id}/jobs`, {
                  operation: "translate",
                  segment_ids: selected,
                  provider_id: provider,
                  continue_pipeline: true,
                });
                setSelected([]);
                setMessage(
                  t("Récupération mise en file. Seuls les passages sélectionnés seront traités."),
                );
                setReload((x) => x + 1);
                refresh();
              } finally {
                setBusy(false);
              }
            });
          }}
        >
          {busy
            ? t("Mise en file…")
            : `${t("Relancer la sélection")} (${selected.length})`}
        </button>
      </div>
      {message && (
        <p role="status" className="notice">
          {message}
        </p>
      )}
      {!visible.length && <p>{t("Aucun passage à récupérer pour ce filtre.")}</p>}
      {visible.map((s) => (
        <article key={s.id} className="notice">
          <label>
            <input
              type="checkbox"
              disabled={!s.eligible || busy}
              checked={selected.includes(s.id)}
              onChange={(e) =>
                setSelected((ids) =>
                  e.target.checked
                    ? [...ids, s.id]
                    : ids.filter((id) => id !== s.id),
                )
              }
            />
            {t("Passage")} {s.position + 1} · {s.chapter} ·{" "}
            {labels[s.status] || s.status}
          </label>
          {s.error && <p>{s.error}</p>}
          <p className="muted">{s.excerpt}</p>
          {!s.eligible && (
            <small>
              {t("Choix humain protégé : récupération automatique désactivée.")}
            </small>
          )}
        </article>
      ))}
    </section>
  );
}
