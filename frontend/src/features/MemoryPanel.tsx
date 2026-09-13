import { useCallback, useEffect, useState } from "react";
import { api, send } from "../api";
import type { Run } from "../types";

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
  const [state, setState] = useState<MemoryStatus | null>(null);
  const [check, setCheck] = useState<unknown>(null);
  const load = useCallback(
    async () => setState(await api(`/projects/${pid}/memory/status`)),
    [pid],
  );
  useEffect(() => {
    void run(load);
    const timer = setInterval(() => {
      void load().catch(() => {});
    }, 5000);
    return () => clearInterval(timer);
  }, [run, load, refreshKey]);
  return (
    <section>
      <h3>Mémoire OpenViking</h3>
      <p>
        {state?.configured
          ? `Connexion configurée · mode ${state.backend}`
          : "OpenViking non configuré"}
      </p>
      <p className="muted">
        Catalogue :{" "}
        {state?.catalog_status === "sent"
          ? "écrit — indexation à vérifier"
          : state?.catalog_status || "chargement"}
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
          Synchroniser le livre et le graphe
        </button>
        <button
          onClick={() =>
            void run(async () =>
              setCheck(await send(`/projects/${pid}/memory/check`)),
            )
          }
        >
          Vérifier dans OpenViking
        </button>
      </div>
      {state && (
        <>
          <p className="muted">
            Racine : <code>{state.root_uri}</code>
          </p>
          <small>
            {Object.entries(state.outbox)
              .map(([k, v]) => `${k}: ${v}`)
              .join(" · ")}
          </small>
          <details open>
            <summary>
              Documents OpenViking du projet ({state.documents.length})
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
        Les liens ouvrent le contenu réellement lu dans OpenViking via le
        backend, sans exposer la clé. Les événements narratifs et les documents
        globaux sont séparés.
      </p>
    </section>
  );
}
