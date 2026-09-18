import { useEffect, useState } from "react";
import { api } from "../api";
import { formatCompact, formatNumber, formatPercent, registerTranslations, useI18n } from "../i18n";
import type { Run } from "../types";
import { Card, EmptyState, LoadingBlock, Page, PageHeader, Stat, Table } from "../ui";

registerTranslations({
  Statistiques: "Statistics",
  "Utilisation globale des modèles enregistrés.": "Global usage of registered models.",
  Modèle: "Model",
  Requêtes: "Requests",
  "Tokens entrée": "Input tokens",
  "Tokens sortie": "Output tokens",
  "Total tokens": "Total tokens",
  "Part des tokens": "Share of tokens",
  "Par modèle": "By model",
  "Aucun modèle enregistré.": "No registered model.",
  "Les statistiques apparaîtront après les premières requêtes aux modèles.":
    "Statistics will appear after the first model requests.",
  "Chargement des statistiques…": "Loading statistics…",
});

interface ModelStatistics {
  model: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export function Statistics({ run }: { run: Run }) {
  const { t } = useI18n();
  const [models, setModels] = useState<ModelStatistics[] | null>(null);
  useEffect(() => {
    void run.background(async () => setModels(await api<ModelStatistics[]>("/statistics/models")));
  }, [run]);
  const sum = (key: keyof Omit<ModelStatistics, "model">) => (models || []).reduce((total, m) => total + m[key], 0);
  const total = sum("total_tokens");
  return (
    <Page>
      <PageHeader title={t("Statistiques")} description={t("Utilisation globale des modèles enregistrés.")} />
      {models === null ? (
        <LoadingBlock label={t("Chargement des statistiques…")} lines={4} />
      ) : models.length ? (
        <div className="stack">
          <div className="stat-grid">
            <Stat label={t("Requêtes")} value={formatNumber(sum("requests"))} />
            <Stat label={t("Tokens entrée")} value={formatCompact(sum("input_tokens"))} />
            <Stat label={t("Tokens sortie")} value={formatCompact(sum("output_tokens"))} />
            <Stat label={t("Total tokens")} value={formatCompact(total)} />
          </div>
          <Card title={t("Par modèle")} padded={false}>
            <Table>
              <thead>
                <tr>
                  <th>{t("Modèle")}</th>
                  <th className="num">{t("Requêtes")}</th>
                  <th className="num">{t("Tokens entrée")}</th>
                  <th className="num">{t("Tokens sortie")}</th>
                  <th className="num">{t("Total tokens")}</th>
                  <th className="share-col">{t("Part des tokens")}</th>
                </tr>
              </thead>
              <tbody>
                {models.map((model) => {
                  const share = total ? (model.total_tokens / total) * 100 : 0;
                  return (
                    <tr key={model.model}>
                      <td>
                        <strong>{model.model}</strong>
                      </td>
                      <td className="num">{formatNumber(model.requests)}</td>
                      <td className="num">{formatNumber(model.input_tokens)}</td>
                      <td className="num">{formatNumber(model.output_tokens)}</td>
                      <td className="num">{formatNumber(model.total_tokens)}</td>
                      <td className="share-col">
                        <span className="share-bar" aria-hidden="true">
                          <span style={{ width: `${share}%` }} />
                        </span>
                        <span className="tabular subtle">{formatPercent(share)}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          </Card>
        </div>
      ) : (
        <Card>
          <EmptyState
            icon="chart"
            title={t("Aucun modèle enregistré.")}
            description={t("Les statistiques apparaîtront après les premières requêtes aux modèles.")}
          />
        </Card>
      )}
    </Page>
  );
}
