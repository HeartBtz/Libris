import { useCallback, useEffect, useState } from "react";
import { api, send } from "../api";
import { registerTranslations, useI18n } from "../i18n";
import type { Run } from "../types";

const translations: Record<string, string> = {
  "Mémoire OpenViking": "OpenViking memory",
  "Connexion configurée · mode {backend}":
    "Connection configured · {backend} mode",
  "OpenViking non configuré": "OpenViking not configured",
  "Catalogue :": "Catalog:",
  "écrit — indexation à vérifier": "written — indexing to verify",
  chargement: "loading",
  "Synchroniser le livre et le graphe": "Synchronize the book and graph",
  "Vérifier dans OpenViking": "Check in OpenViking",
  "Racine :": "Root:",
  "Documents OpenViking du projet ({count})":
    "Project OpenViking documents ({count})",
  "Les liens ouvrent le contenu réellement lu dans OpenViking via le backend, sans exposer la clé. Les événements narratifs et les documents globaux sont séparés.":
    "Links open the content actually read in OpenViking through the backend, without exposing the key. Narrative events and global documents are kept separate.",
};

registerTranslations(translations);

interface MemoryStatus {
  backend: string;
  configured: boolean;
  root_uri: string;
  catalog_status: string;
  outbox: Record<string, number>;
  errors: string[];
  documents: { name: string; uri: string; read_url: string }[];
}

export function MemoryPanel({
  pid,
  run,
  refreshKey = 0,
}: {
  pid: string;
  run: Run;
  refreshKey?: number;
}) {
  const { t } = useI18n();
  const [state, setState] = useState<MemoryStatus | null>(null);
  const [check, setCheck] = useState<unknown>(null);
  const load = useCallback(
    async () => setState(await api(`/projects/${pid}/memory/status`)),
    [pid],
  );
  useEffect(() => {
    void run.background(load);
    const timer = setInterval(() => {
      void load().catch(() => {});
    }, 5000);
    return () => clearInterval(timer);
  }, [run, load, refreshKey]);
  return (
    <section>
      <h3>{t("Mémoire OpenViking")}</h3>
      <p>
        {state?.configured
          ? t("Connexion configurée · mode {backend}").replace(
              "{backend}",
              state.backend,
            )
          : t("OpenViking non configuré")}
      </p>
      <p className="muted">
        {t("Catalogue :")}{" "}
        {state?.catalog_status === "sent"
          ? t("écrit — indexation à vérifier")
          : state?.catalog_status || t("chargement")}
        .
      </p>
      <div className="actions">
        <button
          onClick={() =>
            void run(async () => {
              setCheck(await send(`/projects/${pid}/memory/synchronize`));
              await load();
            })
          }
        >
          {t("Synchroniser le livre et le graphe")}
        </button>
        <button
          onClick={() =>
            void run(async () =>
              setCheck(await send(`/projects/${pid}/memory/check`)),
            )
          }
        >
          {t("Vérifier dans OpenViking")}
        </button>
      </div>
      {state && (
        <>
          <p className="muted">
            {t("Racine :")} <code>{state.root_uri}</code>
          </p>
          <small>
            {Object.entries(state.outbox)
              .map(([k, v]) => `${k}: ${v}`)
              .join(" · ")}
          </small>
          <details open>
            <summary>
              {t("Documents OpenViking du projet ({count})").replace(
                "{count}",
                String(state.documents.length),
              )}
            </summary>
            <ul>
              {state.documents.map((d) => (
                <li key={d.name}>
                  <a href={d.read_url} target="_blank" rel="noreferrer">
                    {d.name} ↗
                  </a>
                </li>
              ))}
            </ul>
          </details>
          {!!state.errors.length && (
            <p className="inline-error">{state.errors.join(" · ")}</p>
          )}
        </>
      )}
      {check != null && <pre>{JSON.stringify(check, null, 2)}</pre>}
      <p className="muted">
        {t(
          "Les liens ouvrent le contenu réellement lu dans OpenViking via le backend, sans exposer la clé. Les événements narratifs et les documents globaux sont séparés.",
        )}
      </p>
    </section>
  );
}
