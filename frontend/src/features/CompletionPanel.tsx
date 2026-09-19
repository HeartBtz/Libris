import { useEffect, useState } from "react";
import { api, labels, send } from "../api";
import { registerTranslations, useI18n } from "../i18n";
import type { Project, ProviderSummary, Run } from "../types";
import {
  Badge,
  Button,
  Callout,
  Card,
  EmptyState,
  Field,
  LoadingBlock,
  SegmentedControl,
  Select,
  Stat,
  StatusPill,
  Table,
} from "../ui";

registerTranslations({
  "Chargement du bilan…": "Loading summary…",
  "Bilan & récupération": "Summary & recovery",
  "Actualiser le bilan": "Refresh summary",
  "Traduction complète": "Translation complete",
  "Traduction incomplète": "Translation incomplete",
  "Sélectionnez les passages ci-dessous puis relancez-les avec le provider du livre ou un provider de remplacement.":
    "Select the passages below, then retry them with the book's provider or a replacement provider.",
  "Traduits": "Translated",
  "Manquants": "Missing",
  "Conservés en original": "Retained in original",
  Ouverts: "Open",
  "Alertes non résolues": "Unresolved alerts",
  "Choix humains protégés": "Protected human choices",
  "Dernier travail : {status}.": "Last job: {status}.",
  "Un travail est encore actif ou en attente.": "A job is still active or pending.",
  "Aucun travail actif.": "No active job.",
  "La conformité EPUB est vérifiée lors de l’export ; une couverture complète ne garantit pas la qualité littéraire.":
    "EPUB compliance is checked during export; complete coverage does not guarantee literary quality.",
  "Résultat de la revue finale": "Final review result",
  Examinés: "Reviewed",
  Résolus: "Resolved",
  Corrigés: "Corrected",
  Restants: "Remaining",
  Protégés: "Protected",
  Échecs: "Failures",
  "Passages à récupérer": "Passages to recover",
  "Filtrer les passages": "Filter passages",
  Tous: "All",
  Erreurs: "Errors",
  Refus: "Refusals",
  "Non commencés": "Not started",
  Bloqués: "Blocked",
  "Provider de récupération": "Recovery provider",
  Choisir: "Choose",
  "Sélectionner les passages affichés (200 max)": "Select displayed passages (200 max)",
  Désélectionner: "Deselect",
  "Récupération mise en file. Seuls les passages sélectionnés seront traités.":
    "Recovery queued. Only the selected passages will be processed.",
  "Mise en file…": "Queuing…",
  "Relancer la sélection ({count})": "Retry selection ({count})",
  "Aucun passage à récupérer pour ce filtre.": "No passages to recover for this filter.",
  Passage: "Passage",
  Section: "Section",
  Extrait: "Excerpt",
  "Choix humain protégé : récupération automatique désactivée.":
    "Protected human choice: automatic recovery disabled.",
  "{count} passages à récupérer ; seuls les premiers sont listés. Relancez-les, puis actualisez le bilan pour voir les suivants.":
    "{count} passages to recover; only the first ones are listed. Rerun them, then refresh the summary to see the next ones.",
  "Sélectionner le passage {position}": "Select passage {position}",
});

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
  recovery_total?: number;
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

type Filter = "all" | "error" | "refused" | "pending" | "blocked";

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
  const [providers, setProviders] = useState<ProviderSummary[]>([]);
  const [provider, setProvider] = useState(project.provider_id || "");
  const [selected, setSelected] = useState<string[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [message, setMessage] = useState("");
  useEffect(() => {
    let active = true;
    void run.background(async () => {
      const [data, list] = await Promise.all([
        api<Report>(`/projects/${project.id}/completion`),
        api<ProviderSummary[]>("/providers"),
      ]);
      if (active) {
        setReport(data);
        setProviders(list);
        setSelected((ids) => ids.filter((id) => data.recovery.some((s) => s.id === id && s.eligible)));
      }
    });
    return () => {
      active = false;
    };
  }, [project.id, reload, tick, run]);
  if (!report) return <LoadingBlock label={t("Chargement du bilan…")} lines={5} />;
  const visible = report.recovery.filter((s) => filter === "all" || s.status === filter);
  const review = project.progress?.review;
  return (
    <section className="review-panel">
      <div className="panel-header">
        <div>
          <h2>{t("Bilan & récupération")}</h2>
          <p className="muted">
            {t(
              "La conformité EPUB est vérifiée lors de l’export ; une couverture complète ne garantit pas la qualité littéraire.",
            )}
          </p>
        </div>
        <div className="panel-actions">
          <Button icon="refresh" onClick={() => setReload((x) => x + 1)}>
            {t("Actualiser le bilan")}
          </Button>
        </div>
      </div>
      <Card
        title={
          <span className="row">
            {report.coverage_complete ? t("Traduction complète") : t("Traduction incomplète")}
            <Badge tone={report.coverage_complete ? "success" : "warning"} dot>
              {report.translated} / {report.total}
            </Badge>
          </span>
        }
        description={
          <>
            {t("Dernier travail : {status}.", { status: labels[report.last_job_status] })}{" "}
            {report.processing ? t("Un travail est encore actif ou en attente.") : t("Aucun travail actif.")}
          </>
        }
      >
        <div className="stat-grid">
          <Stat label={t("Traduits")} value={report.translated} tone="success" />
          <Stat label={t("Manquants")} value={report.missing} tone={report.missing ? "warning" : undefined} />
          <Stat label={t("Conservés en original")} value={report.retained} />
          <Stat label={t("Ouverts")} value={report.flagged} />
          <Stat label={t("Alertes non résolues")} value={report.issues} tone={report.issues ? "warning" : undefined} />
          <Stat label={t("Choix humains protégés")} value={report.protected} />
        </div>
      </Card>
      {review && (
        <div className="stat-grid" role="group" aria-label={t("Résultat de la revue finale")}>
          <Stat label={t("Examinés")} value={review.examined} />
          <Stat label={t("Résolus")} value={review.resolved} />
          <Stat label={t("Corrigés")} value={review.revised} />
          <Stat label={t("Restants")} value={review.remaining} />
          <Stat label={t("Protégés")} value={review.protected} />
          <Stat label={t("Échecs")} value={review.failed} />
        </div>
      )}
      <Card
        padded={false}
        title={t("Passages à récupérer")}
        description={
          !!report.recovery.length && !report.processing
            ? t(
                "Sélectionnez les passages ci-dessous puis relancez-les avec le provider du livre ou un provider de remplacement.",
              )
            : undefined
        }
      >
        <div className="recovery-toolbar">
          <SegmentedControl
            size="sm"
            label={t("Filtrer les passages")}
            value={filter}
            onChange={setFilter}
            options={[
              { value: "all", label: t("Tous") },
              { value: "error", label: t("Erreurs") },
              { value: "refused", label: t("Refus") },
              { value: "pending", label: t("Non commencés") },
              { value: "blocked", label: t("Bloqués") },
            ]}
          />
          <Field label={t("Provider de récupération")} inline className="recovery-provider">
            <Select value={provider} onChange={(e) => setProvider(e.target.value)}>
              <option value="">{t("Choisir")}</option>
              {providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} · {p.model}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        {(report.recovery_total ?? 0) > report.recovery.length && (
          <div className="card-inset">
            <Callout tone="info">
              {t(
                "{count} passages à récupérer ; seuls les premiers sont listés. Relancez-les, puis actualisez le bilan pour voir les suivants.",
                { count: report.recovery_total ?? 0 },
              )}
            </Callout>
          </div>
        )}
        {visible.length ? (
          <Table>
            <thead>
              <tr>
                <th className="cell-tight">
                  <span className="sr-only">{t("Sélectionner les passages affichés (200 max)")}</span>
                </th>
                <th>{t("Passage")}</th>
                <th>{t("Section")}</th>
                <th>{t("Extrait")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {visible.map((s) => (
                <tr key={s.id}>
                  <td className="cell-tight">
                    <input
                      type="checkbox"
                      aria-label={t("Sélectionner le passage {position}", { position: s.position + 1 })}
                      disabled={!s.eligible || busy}
                      checked={selected.includes(s.id)}
                      onChange={(e) =>
                        setSelected((ids) => (e.target.checked ? [...ids, s.id] : ids.filter((id) => id !== s.id)))
                      }
                    />
                  </td>
                  <td className="tabular cell-tight">§ {s.position + 1}</td>
                  <td className="muted">{s.chapter}</td>
                  <td>
                    <div className="recovery-excerpt">
                      <span className="book-excerpt">{s.excerpt}</span>
                      {s.error && <span className="tone-text-danger">{s.error}</span>}
                      {!s.eligible && (
                        <span className="subtle">{t("Choix humain protégé : récupération automatique désactivée.")}</span>
                      )}
                    </div>
                  </td>
                  <td className="cell-tight">
                    <StatusPill status={s.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <EmptyState compact icon="check" title={t("Aucun passage à récupérer pour ce filtre.")} />
        )}
        <div className="card-footer">
          <Button
            size="sm"
            variant="ghost"
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
          </Button>
          <Button size="sm" variant="ghost" disabled={!selected.length || busy} onClick={() => setSelected([])}>
            {t("Désélectionner")}
          </Button>
          <span className="grow" />
          <Button
            variant="primary"
            icon="refresh"
            loading={busy}
            disabled={report.processing || !provider || !selected.length || selected.length > 200}
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
                  setMessage(t("Récupération mise en file. Seuls les passages sélectionnés seront traités."));
                  setReload((x) => x + 1);
                  refresh();
                } finally {
                  setBusy(false);
                }
              });
            }}
          >
            {busy ? t("Mise en file…") : t("Relancer la sélection ({count})", { count: selected.length })}
          </Button>
        </div>
      </Card>
      {message && (
        <Callout tone="success" role="status">
          {message}
        </Callout>
      )}
    </section>
  );
}
