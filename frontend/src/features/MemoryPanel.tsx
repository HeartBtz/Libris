import { useCallback, useEffect, useState } from "react";
import { api, send } from "../api";
import { registerTranslations, useI18n } from "../i18n";
import type { Run } from "../types";
import { Badge, Button, Callout, Card, Icon, Menu, useDialogs, useToast } from "../ui";

registerTranslations({
  "Mémoire OpenViking": "OpenViking memory",
  "Connexion configurée · mode {backend}": "Connection configured · {backend} mode",
  "OpenViking non configuré": "OpenViking not configured",
  "Catalogue : {status}": "Catalog: {status}",
  "écrit — indexation à vérifier": "written — indexing to verify",
  chargement: "loading",
  "Synchroniser le livre et le graphe": "Synchronize the book and graph",
  "Vérifier dans OpenViking": "Check in OpenViking",
  "Maintenance de la mémoire": "Memory maintenance",
  "Réindexer dans OpenViking": "Reindex in OpenViking",
  "Réindexer la mémoire ?": "Reindex the memory?",
  "OpenViking recalcule l’index des documents de ce livre. Les documents eux-mêmes ne changent pas.":
    "OpenViking recomputes the index of this book's documents. The documents themselves do not change.",
  "Reconstruire depuis la base": "Rebuild from the database",
  "Reconstruire la mémoire ?": "Rebuild the memory?",
  "Tous les documents du livre seront réécrits dans OpenViking depuis la base locale. L’opération est idempotente ; l’indexation reste asynchrone.":
    "Every document of the book will be rewritten to OpenViking from the local database. The operation is idempotent; indexing remains asynchronous.",
  Réindexer: "Reindex",
  Reconstruire: "Rebuild",
  "Réindexation demandée à OpenViking.": "Reindexing requested from OpenViking.",
  "{count} documents remis en file de réécriture.": "{count} documents queued for rewriting.",
  "Racine :": "Root:",
  "Documents OpenViking du projet ({count})": "Project OpenViking documents ({count})",
  "Les liens ouvrent le contenu réellement lu dans OpenViking via le backend, sans exposer la clé. Les événements narratifs et les documents globaux sont séparés.":
    "Links open the content actually read in OpenViking through the backend, without exposing the key. Narrative events and global documents are kept separate.",
  "Résultat de la vérification": "Check result",
});

interface MemoryStatus {
  backend: string;
  configured: boolean;
  root_uri: string;
  catalog_status: string;
  outbox: Record<string, number>;
  errors: string[];
  documents: { name: string; uri: string; read_url: string }[];
}

export function MemoryPanel({ pid, run, refreshKey = 0 }: { pid: string; run: Run; refreshKey?: number }) {
  const { t } = useI18n();
  const { confirm } = useDialogs();
  const toast = useToast();
  const [state, setState] = useState<MemoryStatus | null>(null);
  const [check, setCheck] = useState<unknown>(null);
  const load = useCallback(async () => setState(await api(`/projects/${pid}/memory/status`)), [pid]);
  useEffect(() => {
    void run.background(load);
    const timer = setInterval(() => {
      void load().catch(() => {});
    }, 5000);
    return () => clearInterval(timer);
  }, [run, load, refreshKey]);
  return (
    <Card
      title={t("Mémoire OpenViking")}
      description={
        state?.configured
          ? t("Connexion configurée · mode {backend}", { backend: state.backend })
          : t("OpenViking non configuré")
      }
      actions={
        <Menu
          label={t("Maintenance de la mémoire")}
          trigger={(props) => (
            <Button {...props} size="sm" icon="settings" iconAfter="chevronDown">
              {t("Maintenance de la mémoire")}
            </Button>
          )}
          items={[
            {
              label: t("Synchroniser le livre et le graphe"),
              icon: "refresh",
              onSelect: () =>
                void run(async () => {
                  setCheck(await send(`/projects/${pid}/memory/synchronize`));
                  await load();
                }),
            },
            {
              label: t("Vérifier dans OpenViking"),
              icon: "search",
              onSelect: () => void run(async () => setCheck(await send(`/projects/${pid}/memory/check`))),
            },
            { kind: "separator" },
            {
              label: t("Réindexer dans OpenViking"),
              icon: "list",
              onSelect: () =>
                void (async () => {
                  const accepted = await confirm({
                    title: t("Réindexer la mémoire ?"),
                    message: t(
                      "OpenViking recalcule l’index des documents de ce livre. Les documents eux-mêmes ne changent pas.",
                    ),
                    confirmLabel: t("Réindexer"),
                  });
                  if (!accepted) return;
                  await run(async () => {
                    await send(`/projects/${pid}/memory/reindex`);
                    toast(t("Réindexation demandée à OpenViking."));
                  });
                })(),
            },
            {
              label: t("Reconstruire depuis la base"),
              icon: "history",
              onSelect: () =>
                void (async () => {
                  const accepted = await confirm({
                    title: t("Reconstruire la mémoire ?"),
                    message: t(
                      "Tous les documents du livre seront réécrits dans OpenViking depuis la base locale. L’opération est idempotente ; l’indexation reste asynchrone.",
                    ),
                    confirmLabel: t("Reconstruire"),
                  });
                  if (!accepted) return;
                  await run(async () => {
                    const result = await send<{ queued: number }>(`/projects/${pid}/memory/rebuild`);
                    toast(t("{count} documents remis en file de réécriture.", { count: result.queued }));
                    await load();
                  });
                })(),
            },
          ]}
        />
      }
    >
      <div className="stack-sm memory-panel">
        <div className="row">
          <Badge tone={state?.configured ? "success" : "neutral"} dot>
            {state?.backend || "—"}
          </Badge>
          <span className="subtle">
            {t("Catalogue : {status}", {
              status:
                state?.catalog_status === "sent"
                  ? t("écrit — indexation à vérifier")
                  : state?.catalog_status || t("chargement"),
            })}
          </span>
        </div>
        {state && (
          <>
            <p className="subtle">
              {t("Racine :")} <code>{state.root_uri}</code>
            </p>
            {!!Object.keys(state.outbox).length && (
              <p className="subtle tabular">
                {Object.entries(state.outbox)
                  .map(([k, v]) => `${k}: ${v}`)
                  .join(" · ")}
              </p>
            )}
            {!!state.documents.length && (
              <details className="disclosure disclosure-plain">
                <summary>{t("Documents OpenViking du projet ({count})", { count: state.documents.length })}</summary>
                <ul className="compact-list">
                  {state.documents.map((d) => (
                    <li key={d.name}>
                      <a href={d.read_url} target="_blank" rel="noreferrer">
                        {d.name} <Icon name="external" size={12} className="inline-icon" />
                      </a>
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {!!state.errors.length && <Callout tone="danger">{state.errors.join(" · ")}</Callout>}
          </>
        )}
        {check != null && (
          <details className="disclosure disclosure-plain" open>
            <summary>{t("Résultat de la vérification")}</summary>
            <pre>{JSON.stringify(check, null, 2)}</pre>
          </details>
        )}
        <p className="subtle">
          {t(
            "Les liens ouvrent le contenu réellement lu dans OpenViking via le backend, sans exposer la clé. Les événements narratifs et les documents globaux sont séparés.",
          )}
        </p>
      </div>
    </Card>
  );
}
