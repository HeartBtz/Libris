import { useEffect, useState } from "react";
import { send } from "../api";
import { registerTranslations, useI18n } from "../i18n";
import type { Run } from "../types";

const translations: Record<string, string> = {
  "Connexion ChatGPT enregistrée. Détectez les modèles, puis enregistrez celui choisi.": "ChatGPT connection saved. Detect the models, then save the selected one.",
  "Vérification de la connexion en attente. Vous pouvez réessayer.": "Connection verification is pending. You can try again.",
  "Connexion Codex ChatGPT": "Codex ChatGPT connection",
  "Compte ChatGPT / Codex": "ChatGPT / Codex account",
  "Cette connexion utilise Codex chez OpenAI et les quotas de votre abonnement. Elle est partagée par les projets qui choisissent ce provider. Les identifiants restent dans le connecteur serveur dédié.": "This connection uses Codex at OpenAI and your subscription quotas. It is shared by projects that choose this provider. Credentials remain in the dedicated server connector.",
  "Enregistrez d’abord le provider pour ouvrir sa connexion.": "Save the provider first to open its connection.",
  "Connecté": "Connected",
  "Compte non connecté": "Account not connected",
  "Ouvrez le lien officiel et saisissez le code. Activez la connexion par code d’appareil dans les paramètres de sécurité ChatGPT si nécessaire.": "Open the official link and enter the code. Enable device-code sign-in in ChatGPT security settings if needed.",
  "Se connecter avec ChatGPT": "Sign in with ChatGPT",
  "Modèles récupérés. Choisissez le modèle puis cliquez sur Enregistrer.": "Models retrieved. Choose a model, then click Save.",
  "Vérifier / détecter les modèles Codex": "Check / detect Codex models",
  "Compte déconnecté.": "Account disconnected.",
  "Déconnecter ce compte": "Disconnect this account",
  "Ouvrir la connexion officielle OpenAI ↗": "Open the official OpenAI sign-in ↗",
  "Code temporaire :": "Temporary code:",
};
registerTranslations(translations);

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
  const { t } = useI18n();
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
                t("Connexion ChatGPT enregistrée. Détectez les modèles, puis enregistrez celui choisi."),
              );
            }
          }
        })
        .catch(() => {
          if (mounted)
            setMessage(
              t("Vérification de la connexion en attente. Vous pouvez réessayer."),
            );
        });
    }, 3000);
    return () => {
      mounted = false;
      clearInterval(poll);
    };
  }, [login, path]);
  return (
    <section className="notice" aria-label={t("Connexion Codex ChatGPT")}>
      <h3>{t("Compte ChatGPT / Codex")}</h3>
      <p>{t("Cette connexion utilise Codex chez OpenAI et les quotas de votre abonnement. Elle est partagée par les projets qui choisissent ce provider. Les identifiants restent dans le connecteur serveur dédié.")}</p>
      {!providerId ? (
        <p>{t("Enregistrez d’abord le provider pour ouvrir sa connexion.")}</p>
      ) : (
        <>
          <p role="status">
            {status?.connected
              ? `${t("Connecté")}${status.plan ? ` · ${status.plan}` : ""}`
              : t("Compte non connecté")}
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
                    t("Ouvrez le lien officiel et saisissez le code. Activez la connexion par code d’appareil dans les paramètres de sécurité ChatGPT si nécessaire."),
                  );
                }).finally(() => setBusy(false));
              }}
            >
              {t("Se connecter avec ChatGPT")}
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
                    t("Modèles récupérés. Choisissez le modèle puis cliquez sur Enregistrer."),
                  );
                })
              }
            >
              {t("Vérifier / détecter les modèles Codex")}
            </button>
            <button
              type="button"
              disabled={!status?.connected}
              onClick={() =>
                void run(async () => {
                  setStatus(await send<Status>(`${path}/logout`));
                  setLogin(null);
                  setMessage(t("Compte déconnecté."));
                })
              }
            >
              {t("Déconnecter ce compte")}
            </button>
          </div>
          {login?.verificationUrl && (
            <p>
              <a href={login.verificationUrl} target="_blank" rel="noreferrer">
                {t("Ouvrir la connexion officielle OpenAI ↗")}
              </a>
              <br />
              {t("Code temporaire :")} <strong>{login.userCode}</strong>
            </p>
          )}
          {message && <p role="status">{message}</p>}
        </>
      )}
    </section>
  );
}
