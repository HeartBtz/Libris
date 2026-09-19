import type { ReactNode } from "react";
import { registerTranslations, useI18n } from "../i18n";
import type { ProviderSummary } from "../types";
import { Field, IconButton, Select } from "../ui";

registerTranslations({
  "Fournisseurs de secours": "Fallback providers",
  "Ajouter un fournisseur de secours": "Add a fallback provider",
  "Choisir un fournisseur…": "Choose a provider…",
  "Monter {name}": "Move {name} up",
  "Descendre {name}": "Move {name} down",
  "Retirer {name}": "Remove {name}",
  "Fournisseur inconnu ({id})": "Unknown provider ({id})",
});

/**
 * An ordered list of fallback providers: added from a menu, moved up or down, removed.
 * Used by a volume's settings and by the installation's autopilot settings.
 */
export function ProviderChain({
  value,
  onChange,
  providers,
  hint,
  empty,
  exclude = [],
  max = 10,
  disabled = false,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  providers: ProviderSummary[];
  hint: ReactNode;
  empty: ReactNode;
  exclude?: string[];
  max?: number;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  const label = (providerId: string) => {
    const found = providers.find((provider) => provider.id === providerId);
    return found ? `${found.name} · ${found.model}` : t("Fournisseur inconnu ({id})", { id: providerId });
  };
  const move = (index: number, delta: number) => {
    const next = [...value];
    [next[index], next[index + delta]] = [next[index + delta], next[index]];
    onChange(next);
  };
  const available = providers.filter((provider) => !value.includes(provider.id) && !exclude.includes(provider.id));
  return (
    <fieldset className="fallback-providers" disabled={disabled}>
      <legend className="field-label">{t("Fournisseurs de secours")}</legend>
      <p className="field-hint">{hint}</p>
      {value.length ? (
        <ol className="fallback-list">
          {value.map((providerId, index) => (
            <li key={providerId}>
              <span className="tabular subtle">{index + 1}.</span>
              <span className="grow fallback-name">{label(providerId)}</span>
              <IconButton
                icon="chevronUp"
                size="sm"
                label={t("Monter {name}", { name: label(providerId) })}
                disabled={disabled || index === 0}
                onClick={() => move(index, -1)}
              />
              <IconButton
                icon="chevronDown"
                size="sm"
                label={t("Descendre {name}", { name: label(providerId) })}
                disabled={disabled || index === value.length - 1}
                onClick={() => move(index, 1)}
              />
              <IconButton
                icon="x"
                size="sm"
                label={t("Retirer {name}", { name: label(providerId) })}
                disabled={disabled}
                onClick={() => onChange(value.filter((item) => item !== providerId))}
              />
            </li>
          ))}
        </ol>
      ) : (
        <p className="subtle">{empty}</p>
      )}
      {value.length < max && available.length > 0 && (
        <Field label={t("Ajouter un fournisseur de secours")}>
          <Select
            value=""
            disabled={disabled}
            onChange={(e) => {
              const chosen = e.target.value;
              if (chosen && !value.includes(chosen)) onChange([...value, chosen]);
            }}
          >
            <option value="">{t("Choisir un fournisseur…")}</option>
            {available.map((provider) => (
              <option key={provider.id} value={provider.id}>
                {provider.name} · {provider.model}
              </option>
            ))}
          </Select>
        </Field>
      )}
    </fieldset>
  );
}
