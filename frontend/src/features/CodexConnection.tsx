import { useEffect, useState } from "react";
import { send } from "../api";
import type { Run } from "../types";

interface Status {
  connected: boolean;
  type?: string;
  plan?: string;
}
interface Login {
  verificationUrl?: string;
  userCode?: string;
}

export function CodexConnection({
  providerId,
  run,
  onModels,
}: {
  providerId: string;
  run: Run;
  onModels: (models: string[]) => void;
}) {
  const [status, setStatus] = useState<Status | null>(null);
  const [login, setLogin] = useState<Login | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const path = `/providers/${providerId}/codex`;
  useEffect(() => {
    if (!providerId) return;
    let mounted = true;
    send<Status>(`${path}/status`)
      .then((s) => {
        if (mounted) setStatus(s);
      })
      .catch((e) => {
        if (mounted) setMessage(String(e.message));
      });
    return () => {
      mounted = false;
    };
  }, [providerId, path]);
  useEffect(() => {
    if (!login) return;
    let mounted = true;
    const poll = setInterval(() => {
      send<Status>(`${path}/status`)
        .then((s) => {
          if (mounted) {
            setStatus(s);
            if (s.connected) {
              setLogin(null);
              setMessage(
                "Connexion ChatGPT enregistrée. Détectez les modèles, puis enregistrez celui choisi.",
              );
            }
          }
        })
        .catch(() => {
          if (mounted)
            setMessage(
              "Vérification de la connexion en attente. Vous pouvez réessayer.",
            );
        });
    }, 3000);
    return () => {
      mounted = false;
      clearInterval(poll);
    };
  }, [login, path]);
  return (
    <section className="notice" aria-label="Connexion Codex ChatGPT">
      <h3>Compte ChatGPT / Codex</h3>
      <p>
        Cette connexion utilise Codex chez OpenAI et les quotas de votre
        abonnement. Elle est partagée par les projets qui choisissent ce
        provider. Les identifiants restent dans le connecteur serveur dédié.
      </p>
      {!providerId ? (
        <p>Enregistrez d’abord le provider pour ouvrir sa connexion.</p>
      ) : (
        <>
          <p role="status">
            {status?.connected
              ? `Connecté${status.plan ? ` · ${status.plan}` : ""}`
              : "Compte non connecté"}
          </p>
          <div className="actions">
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                void run(async () => {
                  setLogin(await send<Login>(`${path}/login`));
                  setMessage(
                    "Ouvrez le lien officiel et saisissez le code. Activez la connexion par code d’appareil dans les paramètres de sécurité ChatGPT si nécessaire.",
                  );
                }).finally(() => setBusy(false));
              }}
            >
              Se connecter avec ChatGPT
            </button>
            <button
              type="button"
              onClick={() =>
                void run(async () => {
                  setStatus(await send<Status>(`${path}/status`));
                  const result = await send<{ models: string[] }>(
                    `${path}/models`,
                  );
                  onModels(result.models);
                  setMessage(
                    "Modèles récupérés. Choisissez le modèle puis cliquez sur Enregistrer.",
                  );
                })
              }
            >
              Vérifier / détecter les modèles Codex
            </button>
            <button
              type="button"
              disabled={!status?.connected}
              onClick={() =>
                void run(async () => {
                  setStatus(await send<Status>(`${path}/logout`));
                  setLogin(null);
                  setMessage("Compte déconnecté.");
                })
              }
            >
              Déconnecter ce compte
            </button>
          </div>
          {login?.verificationUrl && (
            <p>
              <a href={login.verificationUrl} target="_blank" rel="noreferrer">
                Ouvrir la connexion officielle OpenAI ↗
              </a>
              <br />
              Code temporaire : <strong>{login.userCode}</strong>
            </p>
          )}
          {message && <p role="status">{message}</p>}
        </>
      )}
    </section>
  );
}
