import { useEffect, useState } from "react";
import { api } from "../api";
import { formatCompact, formatNumber, registerTranslations, useI18n } from "../i18n";
import { Icon } from "../ui";

registerTranslations({
  Estimation: "Estimate",
  "≈ {tokens} tokens · ≈ {cost} (tarifs configurés)": "≈ {tokens} tokens · ≈ {cost} (configured rates)",
  "≈ {tokens} tokens": "≈ {tokens} tokens",
  "{count} passage restant": "{count} remaining passage",
  "{count} passages restants": "{count} remaining passages",
  "d’après {count} livre précédent": "based on {count} previous book",
  "d’après {count} livres précédents": "based on {count} previous books",
  "d’après l’historique et des valeurs par défaut": "based on history and default values",
  "estimation par défaut": "default estimate",
});

export type EstimatedOperation = "analyze" | "translate" | "review";

interface Estimate {
  input_tokens: number;
  output_tokens: number;
  requests: number;
  cost: number | null;
  passages?: number;
  basis_kind?: "history" | "mixed" | "default" | "none";
  history_books?: number;
}

/**
 * Cost and token forecast shown before a paid operation. The estimate is optional: when the
 * server cannot provide one (older server, nothing left to process), nothing is displayed.
 */
export function EstimateNote({ projectId, operation }: { projectId: string; operation: EstimatedOperation }) {
  const { t, tp } = useI18n();
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  useEffect(() => {
    let active = true;
    api<Estimate>(`/projects/${projectId}/estimate?operation=${operation}`)
      .then((value) => {
        if (active) setEstimate(value);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [projectId, operation]);
  if (!estimate || estimate.basis_kind === "none" || estimate.passages === 0) return null;
  const tokens = formatCompact((estimate.input_tokens || 0) + (estimate.output_tokens || 0));
  const basis =
    estimate.basis_kind === "history"
      ? tp(estimate.history_books || 0, "d’après {count} livre précédent", "d’après {count} livres précédents")
      : estimate.basis_kind === "mixed"
        ? t("d’après l’historique et des valeurs par défaut")
        : t("estimation par défaut");
  return (
    <div className="estimate-note" role="status">
      <Icon name="chart" />
      <div>
        <strong>{t("Estimation")}</strong>
        <span>
          {estimate.cost
            ? t("≈ {tokens} tokens · ≈ {cost} (tarifs configurés)", {
                tokens,
                cost: formatNumber(estimate.cost, { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
              })
            : t("≈ {tokens} tokens", { tokens })}
        </span>
        <small>
          {estimate.passages !== undefined &&
            `${tp(estimate.passages, "{count} passage restant", "{count} passages restants")} · `}
          {basis}
        </small>
      </div>
    </div>
  );
}
