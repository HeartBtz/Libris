import { useEffect, useState } from "react";
import { api, number } from "../api";
import { registerTranslations, useI18n } from "../i18n";
import type { Run } from "../types";

const translations: Record<string, string> = {
  "Statistiques": "Statistics",
  "Utilisation globale des modèles enregistrés.": "Global usage of registered models.",
  "← Bibliothèque": "← Library",
  "Modèle": "Model",
  "Requêtes": "Requests",
  "Tokens entrée": "Input tokens",
  "Tokens sortie": "Output tokens",
  "Total tokens": "Total tokens",
  "Aucun modèle enregistré.": "No registered model.",
};

registerTranslations(translations);

interface ModelStatistics {
  model: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export function Statistics({ run }: { run: Run }) {
  const { t } = useI18n();
  const [models, setModels] = useState<ModelStatistics[]>([]);

  useEffect(() => {
    void run.background(async () => setModels(await api<ModelStatistics[]>("/statistics/models")));
  }, [run]);

  return (
    <main className="settings">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Libris</p>
          <h1>{t("Statistiques")}</h1>
          <p className="muted">{t("Utilisation globale des modèles enregistrés.")}</p>
        </div>
        <a href="#library">{t("← Bibliothèque")}</a>
      </div>
      {models.length ? (
        <div className="table-wrap library-table-wrap">
          <table className="library-table">
            <thead>
              <tr>
                <th>{t("Modèle")}</th>
                <th>{t("Requêtes")}</th>
                <th>{t("Tokens entrée")}</th>
                <th>{t("Tokens sortie")}</th>
                <th>{t("Total tokens")}</th>
              </tr>
            </thead>
            <tbody>
              {models.map((model) => (
                <tr key={model.model}>
                  <td>{model.model}</td>
                  <td data-label={t("Requêtes")}>{number(model.requests)}</td>
                  <td data-label={t("Tokens entrée")}>{number(model.input_tokens)}</td>
                  <td data-label={t("Tokens sortie")}>{number(model.output_tokens)}</td>
                  <td data-label={t("Total tokens")}>{number(model.total_tokens)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted">{t("Aucun modèle enregistré.")}</p>
      )}
    </main>
  );
}
