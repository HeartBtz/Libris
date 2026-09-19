import { useCallback, useEffect, useState } from "react";
import { api, send } from "../api";
import { formatDateTime, registerTranslations, useI18n } from "../i18n";
import type { Run } from "../types";
import { Badge, Button, Callout, Card, EmptyState, Switch, useDialogs } from "../ui";
import type { Tone } from "../ui";

registerTranslations({
  "Nettoyage d’OpenViking": "OpenViking cleanup",
  "Supprimer un volume ou une série peut aussi effacer ses documents OpenViking. Le worker s’en charge après la suppression, réessaie en cas de panne et consigne ce qu’il efface. Seul le dossier de l’élément supprimé est touché.":
    "Deleting a volume or a series can also remove its OpenViking documents. The worker does it after the deletion, retries during an outage and logs what it removes. Only the deleted item's directory is touched.",
  "Effacer les documents OpenViking à la suppression": "Remove OpenViking documents on deletion",
  "Désactivé : les documents restent dans OpenViking ; la base ne les admet simplement plus.":
    "Off: the documents stay in OpenViking; the database simply no longer admits them.",
  "Réglage enregistré ici": "Setting saved here",
  "Réglage de l’environnement": "Environment setting",
  "Valeur de l’environnement (OPENVIKING_CLEANUP_ON_DELETE) : {value}": "Environment value (OPENVIKING_CLEANUP_ON_DELETE): {value}",
  activé: "on",
  désactivé: "off",
  "Enregistrer le nettoyage": "Save the cleanup",
  "Revenir à la valeur de l’environnement": "Go back to the environment value",
  "Nettoyage enregistré. Il s’applique aux prochaines suppressions.": "Cleanup saved. It applies to the next deletions.",
  "Valeur de l’environnement rétablie.": "Environment value restored.",
  "Configurez d’abord l’URL OpenViking : sans elle, rien n’est effacé.": "Set the OpenViking URL first: without it, nothing is removed.",
  "Documents orphelins": "Orphan documents",
  "Dossiers de volumes et de séries supprimés ou déplacés avant l’activation du nettoyage. La recherche n’efface rien : relisez la liste, puis confirmez.":
    "Directories of volumes and series deleted or moved before the cleanup was on. The search removes nothing: read the list, then confirm.",
  "Chercher les orphelins (essai à blanc)": "Look for orphans (dry run)",
  "Aucun document orphelin sous {root}.": "No orphan document under {root}.",
  "{count} dossier(s) orphelin(s) sous {root}": "{count} orphan directory(ies) under {root}",
  "Seuls les {shown} premiers sont listés ; relancez la recherche après leur nettoyage.":
    "Only the first {shown} are listed; search again after cleaning them.",
  "Effacer ces {count} dossier(s)": "Remove these {count} directory(ies)",
  "Effacer ces dossiers d’OpenViking ?": "Remove these directories from OpenViking?",
  "Chaque dossier est vérifié de nouveau dans la base avant d’être effacé. Tout se reconstruit depuis la base si besoin.":
    "Each directory is checked again against the database before it is removed. Everything can be rebuilt from the database if needed.",
  Effacer: "Remove",
  "Nettoyage planifié : le worker efface ces dossiers dans quelques secondes.": "Cleanup scheduled: the worker removes these directories within seconds.",
  "Refusés, plus orphelins : {count}": "Refused, no longer orphans: {count}",
  "Journal des nettoyages": "Cleanup log",
  "Aucun nettoyage pour l’instant.": "No cleanup yet.",
  Actualiser: "Refresh",
  "Volume supprimé": "Deleted volume",
  "Série supprimée": "Deleted series",
  "Orphelins": "Orphans",
  "En attente": "Waiting",
  "En cours": "Running",
  Terminé: "Done",
  "{count} tentative(s)": "{count} attempt(s)",
  "Prochain essai : {date}": "Next try: {date}",
  "Réessayer maintenant": "Retry now",
  "Détail des dossiers": "Directories in detail",
  "effacé ({count} document(s))": "removed ({count} document(s))",
  "déjà absent": "already absent",
  "conservé : utilisé de nouveau": "kept: in use again",
  "volume supprimé": "volume deleted",
  "volume déplacé": "volume moved",
  "série supprimée": "series deleted",
  "ancienne disposition (0.5)": "old layout (0.5)",
});

interface CleanupView {
  enabled: boolean;
  default: boolean;
  saved: boolean;
  configured: boolean;
}
interface CleanupResult {
  uri: string;
  status: "removed" | "absent" | "kept";
  reason: string;
  document_count?: number;
  documents?: string[];
}
/** `GET /api/settings/memory/cleanups`. */
export interface CleanupEntry {
  id: string;
  kind: "volume" | "series" | "orphans";
  label: string;
  status: "pending" | "running" | "done";
  attempts: number;
  next_attempt: number;
  error: string;
  created_at: number;
  finished_at: number | null;
  uris: string[];
  results: CleanupResult[];
}
interface OrphanScan {
  root_uri: string;
  orphans: { uri: string; reason: string }[];
  total: number;
}

const REASONS: Record<string, string> = {
  volume_deleted: "volume supprimé",
  volume_moved: "volume déplacé",
  series_deleted: "série supprimée",
  legacy_layout: "ancienne disposition (0.5)",
};
const KINDS: Record<CleanupEntry["kind"], string> = {
  volume: "Volume supprimé",
  series: "Série supprimée",
  orphans: "Orphelins",
};
const STATUSES: Record<CleanupEntry["status"], [string, Tone]> = {
  pending: ["En attente", "warning"],
  running: ["En cours", "accent"],
  done: ["Terminé", "success"],
};

/** Settings › Memory · OpenViking: the opt-in cleanup switch, the orphan dry run and the log. */
export function OpenVikingCleanup({ run }: { run: Run }) {
  const { t } = useI18n();
  const { confirm } = useDialogs();
  const [view, setView] = useState<CleanupView | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [scan, setScan] = useState<OrphanScan | null>(null);
  const [log, setLog] = useState<CleanupEntry[]>([]);
  const load = (next: CleanupView) => {
    setView(next);
    setEnabled(next.enabled);
  };
  const refreshLog = useCallback(async () => setLog(await api<CleanupEntry[]>("/settings/memory/cleanups")), []);
  useEffect(() => {
    void run.background(async () => {
      load(await api<CleanupView>("/settings/memory/cleanup"));
      await refreshLog();
    });
  }, [run, refreshLog]);

  async function act(task: () => Promise<void>) {
    setBusy(true);
    setStatus("");
    await run(task).finally(() => setBusy(false));
  }
  async function clean() {
    if (!scan) return;
    const uris = scan.orphans.map((item) => item.uri);
    const accepted = await confirm({
      title: t("Effacer ces dossiers d’OpenViking ?"),
      message: t(
        "Chaque dossier est vérifié de nouveau dans la base avant d’être effacé. Tout se reconstruit depuis la base si besoin.",
      ),
      confirmLabel: t("Effacer"),
      tone: "danger",
    });
    if (!accepted) return;
    await act(async () => {
      const answer = await send<{ refused: string[] }>("/settings/memory/orphans/clean", { uris });
      setScan(null);
      setStatus(
        [
          t("Nettoyage planifié : le worker efface ces dossiers dans quelques secondes."),
          answer.refused.length ? t("Refusés, plus orphelins : {count}", { count: String(answer.refused.length) }) : "",
        ]
          .filter(Boolean)
          .join(" "),
      );
      await refreshLog();
    });
  }

  const ready = view !== null;
  return (
    <Card
      className="settings-card"
      title={t("Nettoyage d’OpenViking")}
      description={t(
        "Supprimer un volume ou une série peut aussi effacer ses documents OpenViking. Le worker s’en charge après la suppression, réessaie en cas de panne et consigne ce qu’il efface. Seul le dossier de l’élément supprimé est touché.",
      )}
      actions={
        view && (
          <Badge tone={view.saved ? "accent" : "neutral"}>
            {view.saved ? t("Réglage enregistré ici") : t("Réglage de l’environnement")}
          </Badge>
        )
      }
    >
      <div className="stack">
        {view && !view.configured && (
          <Callout tone="warning">{t("Configurez d’abord l’URL OpenViking : sans elle, rien n’est effacé.")}</Callout>
        )}
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            void act(async () => {
              load(await send<CleanupView>("/settings/memory/cleanup", { enabled }, "PUT"));
              setStatus(t("Nettoyage enregistré. Il s’applique aux prochaines suppressions."));
            });
          }}
        >
          <Switch
            label={t("Effacer les documents OpenViking à la suppression")}
            description={t("Désactivé : les documents restent dans OpenViking ; la base ne les admet simplement plus.")}
            checked={enabled}
            disabled={!ready || busy}
            onChange={(e) => {
              setStatus("");
              setEnabled(e.target.checked);
            }}
          />
          {view && (
            <p className="subtle">
              {t("Valeur de l’environnement (OPENVIKING_CLEANUP_ON_DELETE) : {value}", {
                value: view.default ? t("activé") : t("désactivé"),
              })}
            </p>
          )}
          <div className="form-actions">
            <Button type="submit" variant="primary" disabled={!ready || busy}>
              {t("Enregistrer le nettoyage")}
            </Button>
            <Button
              disabled={!ready || busy || !view?.saved}
              onClick={() =>
                void act(async () => {
                  load(await api<CleanupView>("/settings/memory/cleanup", { method: "DELETE" }));
                  setStatus(t("Valeur de l’environnement rétablie."));
                })
              }
            >
              {t("Revenir à la valeur de l’environnement")}
            </Button>
          </div>
        </form>

        <section className="stack" aria-labelledby="openviking-orphans">
          <h3 id="openviking-orphans" className="section-title">
            {t("Documents orphelins")}
          </h3>
          <p className="subtle">
            {t(
              "Dossiers de volumes et de séries supprimés ou déplacés avant l’activation du nettoyage. La recherche n’efface rien : relisez la liste, puis confirmez.",
            )}
          </p>
          <div className="form-actions">
            <Button
              disabled={!ready || busy || !view?.configured}
              onClick={() =>
                void act(async () => setScan(await send<OrphanScan>("/settings/memory/orphans/scan")))
              }
            >
              {t("Chercher les orphelins (essai à blanc)")}
            </Button>
          </div>
          {scan &&
            (scan.orphans.length === 0 ? (
              <p role="status">{t("Aucun document orphelin sous {root}.", { root: scan.root_uri })}</p>
            ) : (
              <div className="stack">
                <p role="status">
                  {t("{count} dossier(s) orphelin(s) sous {root}", { count: String(scan.total), root: scan.root_uri })}
                </p>
                <ul className="cleanup-list" aria-label={t("Documents orphelins")}>
                  {scan.orphans.map((item) => (
                    <li key={item.uri}>
                      <code className="cleanup-uri">{item.uri}</code>
                      <Badge tone="neutral">{t(REASONS[item.reason] ?? item.reason)}</Badge>
                    </li>
                  ))}
                </ul>
                {scan.total > scan.orphans.length && (
                  <p className="subtle">
                    {t("Seuls les {shown} premiers sont listés ; relancez la recherche après leur nettoyage.", {
                      shown: String(scan.orphans.length),
                    })}
                  </p>
                )}
                <div className="form-actions">
                  <Button variant="danger" disabled={busy} onClick={() => void clean()}>
                    {t("Effacer ces {count} dossier(s)", { count: String(scan.orphans.length) })}
                  </Button>
                </div>
              </div>
            ))}
        </section>

        {status && (
          <p role="status" className="form-status tone-text-success">
            {status}
          </p>
        )}

        <section className="stack" aria-labelledby="openviking-cleanup-log">
          <div className="cleanup-heading">
            <h3 id="openviking-cleanup-log" className="section-title">
              {t("Journal des nettoyages")}
            </h3>
            <Button disabled={!ready} onClick={() => void run(refreshLog)}>
              {t("Actualiser")}
            </Button>
          </div>
          {log.length === 0 ? (
            <EmptyState compact icon="info" title={t("Aucun nettoyage pour l’instant.")} />
          ) : (
            <ul className="cleanup-list" aria-label={t("Journal des nettoyages")}>
              {log.map((entry) => (
                <CleanupLogEntry
                  key={entry.id}
                  entry={entry}
                  onRetry={() =>
                    void run(async () => {
                      await send(`/settings/memory/cleanups/${entry.id}/retry`);
                      await refreshLog();
                    })
                  }
                />
              ))}
            </ul>
          )}
        </section>
      </div>
    </Card>
  );
}

function CleanupLogEntry({ entry, onRetry }: { entry: CleanupEntry; onRetry: () => void }) {
  const { t } = useI18n();
  const [label, tone] = STATUSES[entry.status];
  const describe = (result: CleanupResult) =>
    result.status === "removed"
      ? t("effacé ({count} document(s))", { count: String(result.document_count ?? 0) })
      : result.status === "absent"
        ? t("déjà absent")
        : t("conservé : utilisé de nouveau");
  return (
    <li>
      <div className="cleanup-heading">
        <strong>
          {t(KINDS[entry.kind])}
          {entry.label ? ` · ${entry.label}` : ""}
        </strong>
        <Badge tone={tone}>{t(label)}</Badge>
      </div>
      <p className="subtle">
        {formatDateTime(entry.finished_at ?? entry.created_at)}
        {entry.attempts > 1 ? ` · ${t("{count} tentative(s)", { count: String(entry.attempts) })}` : ""}
        {entry.status === "pending" && entry.next_attempt > Date.now() / 1000
          ? ` · ${t("Prochain essai : {date}", { date: formatDateTime(entry.next_attempt) })}`
          : ""}
      </p>
      {entry.error && <p className="tone-text-danger">{entry.error}</p>}
      {entry.results.length > 0 && (
        <details className="disclosure">
          <summary>{t("Détail des dossiers")}</summary>
          <ul className="cleanup-results">
            {entry.results.map((result) => (
              <li key={result.uri}>
                <code className="cleanup-uri">{result.uri}</code> — {describe(result)}
                {result.documents && result.documents.length > 0 && (
                  <ul>
                    {result.documents.map((document) => (
                      <li key={document}>
                        <code className="cleanup-uri">{document}</code>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
      {entry.status === "pending" && (
        <div className="form-actions">
          <Button onClick={onRetry}>
            {t("Réessayer maintenant")}
          </Button>
        </div>
      )}
    </li>
  );
}
