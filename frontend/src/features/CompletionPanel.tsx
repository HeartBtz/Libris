import { useEffect, useState } from "react";
import { api, labels, send } from "../api";
import type { Project, Provider, Run } from "../types";

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
}: {
  project: Project;
  run: Run;
  refresh: () => void;
}) {
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
    void run(async () => {
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
  }, [project.id, reload, run]);
  if (!report) return <p role="status">Chargement du bilan…</p>;
  const visible = report.recovery.filter(
    (s) => filter === "all" || s.status === filter,
  );
  return (
    <section className="validation-panel">
      <div className="validation-heading">
        <div>
          <p className="eyebrow">Finir le livre</p>
          <h2>Bilan & récupération</h2>
        </div>
        <button onClick={() => setReload((x) => x + 1)}>
          Actualiser le bilan
        </button>
      </div>
      <div className="notice">
        <h3>
          {report.coverage_complete
            ? "Traduction complète"
            : "Traduction incomplète"}
        </h3>
        <p>
          {report.translated} / {report.total} passages traduits ·{" "}
          {report.missing} manquants · {report.retained} conservés en original
        </p>
        <p>
          {report.flagged} à vérifier · {report.issues} alertes non résolues ·{" "}
          {report.protected} choix humains protégés
        </p>
        <p>
          Dernier travail :{" "}
          {labels[report.last_job_status] || report.last_job_status}.{" "}
          {report.processing
            ? "Un travail est encore actif ou en attente."
            : "Aucun travail actif."}
        </p>
        <p className="muted">
          La conformité EPUB est vérifiée lors de l’export ; une couverture
          complète ne garantit pas la qualité littéraire.
        </p>
      </div>
      <h3>Passages à récupérer</h3>
      <div className="actions">
        <label>
          Filtrer les passages
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="all">Tous</option>
            <option value="error">Erreurs</option>
            <option value="refused">Refus</option>
            <option value="pending">Non commencés</option>
            <option value="blocked">Bloqués</option>
          </select>
        </label>
        <label>
          Provider de récupération
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
          >
            <option value="">Choisir</option>
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
          Sélectionner les passages affichés (200 max)
        </button>
        <button
          disabled={!selected.length || busy}
          onClick={() => setSelected([])}
        >
          Désélectionner
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
                });
                setSelected([]);
                setMessage(
                  "Récupération mise en file. Seuls les passages sélectionnés seront traités.",
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
            ? "Mise en file…"
            : `Relancer la sélection (${selected.length})`}
        </button>
      </div>
      {message && (
        <p role="status" className="notice">
          {message}
        </p>
      )}
      {!visible.length && <p>Aucun passage à récupérer pour ce filtre.</p>}
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
            Passage {s.position + 1} · {s.chapter} ·{" "}
            {labels[s.status] || s.status}
          </label>
          {s.error && <p>{s.error}</p>}
          <p className="muted">{s.excerpt}</p>
          {!s.eligible && (
            <small>
              Choix humain protégé : récupération automatique désactivée.
            </small>
          )}
        </article>
      ))}
    </section>
  );
}
