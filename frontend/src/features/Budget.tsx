import { useEffect, useRef, useState } from "react";
import { api, send } from "../api";
import { formatDateTime, formatNumber, registerTranslations, useI18n } from "../i18n";
import type { Project, Run, User } from "../types";
import { Badge, Button, Callout, Card, Field, FormGrid, Input, LoadingBlock, ProgressBar, Select, Stat, useDialogs } from "../ui";
import type { Tone } from "../ui";

registerTranslations({
  "Budget du livre": "Book budget",
  "Plafond de dépense du livre, tous travaux confondus, dans la devise des prix des fournisseurs. Avant un lancement, l’estimation est comparée à ce qui reste ; près du plafond, le travail passe à un fournisseur de secours moins cher, sinon il se met en pause.":
    "Spending cap of the book, all jobs included, in the currency of the provider prices. Before a launch the estimate is compared with what remains; near the cap the job moves to a cheaper fallback provider, otherwise it pauses.",
  "Sans budget": "No budget",
  "Dans le budget": "Within budget",
  "Proche du plafond": "Near the cap",
  "Plafond atteint": "Cap reached",
  "Dépensé": "Spent",
  "Plafond": "Cap",
  "Reste": "Left",
  "Consommation du budget": "Budget used",
  "Budget de ce livre": "Budget of this book",
  "Modifier le budget du livre": "Change the book budget",
  "Réglage de l’installation ({amount})": "Installation setting ({amount})",
  "Réglage de l’installation (aucun)": "Installation setting (none)",
  "Plafond propre": "Own cap",
  "Aucun budget pour ce livre": "No budget for this book",
  "Montant du plafond": "Cap amount",
  "Dans la devise où les prix des fournisseurs sont saisis.": "In the currency the provider prices are entered in.",
  "Enregistrer le budget": "Save the budget",
  "Budget enregistré.": "Budget saved.",
  "Seuil de bascule : {percent}. Estimation au-dessus du reste : {action}.":
    "Switch threshold: {percent}. Estimate above what is left: {action}.",
  "avertissement": "warning",
  "lancement refusé": "launch refused",
  "Le fournisseur de ce livre n’a pas de prix : ses appels comptent pour 0 et le budget ne peut pas l’arrêter.":
    "This book's provider has no price: its calls count as 0 and the budget cannot stop it.",
  "Dernier travail": "Last job",
  "Coût estimé": "Estimated cost",
  "Coût réel": "Real cost",
  "Non estimé": "Not estimated",
  "{count} bascule vers un fournisseur moins cher": "{count} switch to a cheaper provider",
  "{count} bascules vers un fournisseur moins cher": "{count} switches to a cheaper provider",
  "Mis en pause par le budget": "Paused by the budget",
  "Seul le propriétaire du livre peut changer son budget.": "Only the book's owner can change its budget.",
  Budgets: "Budgets",
  "Budgets de l’installation": "Installation budgets",
  "Plafond par défaut des livres qui n’ont pas le leur, et comportement près du plafond. Sans valeur enregistrée ici, les variables BUDGET_* s’appliquent.":
    "Default cap of the books without their own, and behaviour near the cap. Without a value saved here, the BUDGET_* variables apply.",
  "Plafond par défaut d’un livre": "Default cap of a book",
  "0 : aucun plafond par défaut.": "0: no default cap.",
  "Seuil de bascule (%)": "Switch threshold (%)",
  "À ce niveau, un travail passe à un fournisseur moins cher, ou se met en pause s’il n’y en a pas.":
    "At this level a job moves to a cheaper provider, or pauses when there is none.",
  "Estimation au-dessus du reste": "Estimate above what is left",
  "Avertir et lancer": "Warn and launch",
  "Refuser le lancement": "Refuse the launch",
  "Enregistrer les budgets": "Save the budgets",
  "Revenir aux valeurs de l’environnement": "Go back to the environment values",
  "Revenir aux valeurs de l’environnement ?": "Go back to the environment values?",
  "Les valeurs enregistrées ici sont oubliées ; les variables d’environnement s’appliquent de nouveau aux prochaines décisions.":
    "The values saved here are forgotten; the environment variables apply again to the next decisions.",
  Revenir: "Go back",
  "Valeurs enregistrées ici": "Values saved here",
  "Valeurs de l’environnement": "Environment values",
  "Réglages enregistrés. Ils s’appliquent aux prochaines décisions, sans redémarrage.":
    "Settings saved. They apply to the next decisions, without a restart.",
  "Valeurs de l’environnement rétablies.": "Environment values restored.",
  "Valeur de l’environnement : {value}": "Environment value: {value}",
  "Ce serveur ne gère pas encore les budgets.": "This server does not handle budgets yet.",
  "Budget du jeton": "Token budget",
  "Facultatif. Une requête qui lancerait du travail est refusée (HTTP 402) une fois le plafond atteint ; un travail en cours se met en pause près du plafond.":
    "Optional. A request that would start work is refused (HTTP 402) once the cap is reached; a running job pauses near the cap.",
  "Période": "Period",
  "Par mois": "Per month",
  "Sur toute la vie du jeton": "Over the token's life",
  "Budget : {spent} sur {amount} ce mois-ci": "Budget: {spent} of {amount} this month",
  "Budget : {spent} sur {amount} au total": "Budget: {spent} of {amount} in total",
  "Remis à zéro le {date}": "Reset on {date}",
  "Budget de {name}": "Budget of {name}",
  "Modifier le budget": "Change the budget",
  "Vide : aucun plafond.": "Empty: no cap.",
  "Reste {remaining} sur le budget du livre : l’estimation le dépasse, le lancement sera refusé.":
    "{remaining} left in the book budget: the estimate exceeds it, the launch will be refused.",
  "Reste {remaining} sur le budget du livre : l’estimation le dépasse, le travail se mettra en pause près du plafond.":
    "{remaining} left in the book budget: the estimate exceeds it, the job will pause near the cap.",
  "Reste {remaining} sur le budget du livre.": "{remaining} left in the book budget.",
});

export const money = (value: number | null | undefined) =>
  formatNumber(value || 0, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

interface CostReport {
  estimated: number | null;
  actual: number | null;
  budget: number | null;
  warning?: string | null;
  paused_for_budget?: boolean;
  provider_switches?: number;
}
/** `GET /api/projects/{id}/budget`. */
interface BookBudgetView {
  amount: number | null;
  own_amount: number | null;
  default_amount: number | null;
  spent: number;
  remaining: number | null;
  ratio: number;
  state: "none" | "ok" | "near" | "exceeded";
  switch_threshold: number;
  on_estimate: "warn" | "refuse";
  priced: boolean;
  currency_note: string;
  last_job: ({ id: string; status: string; stop_reason: string } & CostReport) | null;
}
/** The budget block of `GET /api/projects/{id}/estimate`, when the book has a cap. */
export interface EstimateBudget {
  amount: number;
  spent: number;
  remaining: number;
  exceeds: boolean;
  on_estimate: "warn" | "refuse";
}
/** `budget` of an API token (null without a cap). */
export interface TokenBudgetView {
  amount: number;
  period: "month" | "total";
  spent: number;
  resets_at: number | null;
}
interface BudgetValues {
  default_book: number;
  switch_threshold: number;
  on_estimate: "warn" | "refuse";
}
interface BudgetAdmin {
  values: BudgetValues;
  defaults: BudgetValues;
  saved: boolean;
}

const STATES: Record<BookBudgetView["state"], [string, Tone]> = {
  none: ["Sans budget", "neutral"],
  ok: ["Dans le budget", "success"],
  near: ["Proche du plafond", "warning"],
  exceeded: ["Plafond atteint", "danger"],
};

/** The book's cap, what it has cost, and its last job's estimate against its real cost. */
export function BookBudget({ project, user, run, tick }: { project: Project; user: User; run: Run; tick?: number }) {
  const { t, tp } = useI18n();
  const [view, setView] = useState<BookBudgetView | null>(null);
  const [missing, setMissing] = useState(false);
  const [mode, setMode] = useState<"default" | "own" | "none">("default");
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const owner = project.owner_id === user.id;
  // The form follows the saved choice when it loads or is saved, not on every refresh of the figures.
  const load = (next: BookBudgetView, form = true) => {
    setView(next);
    if (!form) return;
    setMode(next.own_amount === null ? "default" : next.own_amount > 0 ? "own" : "none");
    setAmount(next.own_amount ? String(next.own_amount) : "");
  };
  const loaded = useRef(false);
  useEffect(() => {
    let active = true;
    void run.background(async () => {
      // Servers before 0.7 have no budgets: the card is simply not shown.
      const next = await api<BookBudgetView>(`/projects/${project.id}/budget`).catch(() => null);
      if (!active) return;
      if (next) {
        load(next, !loaded.current);
        loaded.current = true;
      } else setMissing(true);
    });
    return () => {
      active = false;
    };
  }, [run, project.id, tick]);
  if (missing) return null;
  if (!view) return <LoadingBlock label={t("Budget du livre")} lines={2} />;
  const [label, tone] = STATES[view.state];
  const last = view.last_job;
  return (
    <Card
      className="settings-card budget-card"
      title={t("Budget du livre")}
      description={t(
        "Plafond de dépense du livre, tous travaux confondus, dans la devise des prix des fournisseurs. Avant un lancement, l’estimation est comparée à ce qui reste ; près du plafond, le travail passe à un fournisseur de secours moins cher, sinon il se met en pause.",
      )}
      actions={
        <Badge tone={tone} dot>
          {t(label)}
        </Badge>
      }
    >
      <div className="stack">
        <div className="stat-grid">
          <Stat label={t("Dépensé")} value={money(view.spent)} />
          <Stat label={t("Plafond")} value={view.amount ? money(view.amount) : "—"} />
          <Stat label={t("Reste")} value={view.remaining === null ? "—" : money(view.remaining)} tone={tone} />
        </div>
        {view.amount ? (
          <ProgressBar
            value={Math.min(view.ratio, 1) * 100}
            label={t("Consommation du budget")}
            tone={view.state === "ok" ? "accent" : tone}
          />
        ) : null}
        <p className="field-hint">
          {view.currency_note}{" "}
          {t("Seuil de bascule : {percent}. Estimation au-dessus du reste : {action}.", {
            percent: `${Math.round(view.switch_threshold * 100)} %`,
            action: view.on_estimate === "refuse" ? t("lancement refusé") : t("avertissement"),
          })}
        </p>
        {view.amount && !view.priced ? (
          <Callout tone="warning">
            {t("Le fournisseur de ce livre n’a pas de prix : ses appels comptent pour 0 et le budget ne peut pas l’arrêter.")}
          </Callout>
        ) : null}
        {last && (last.estimated !== null || last.actual !== null) && (
          <div className="budget-last">
            <strong>{t("Dernier travail")}</strong>
            <dl className="budget-figures">
              <div>
                <dt>{t("Coût estimé")}</dt>
                <dd className="tabular">{last.estimated === null ? t("Non estimé") : money(last.estimated)}</dd>
              </div>
              <div>
                <dt>{t("Coût réel")}</dt>
                <dd className="tabular">{money(last.actual)}</dd>
              </div>
            </dl>
            {!!last.provider_switches && (
              <small>
                {tp(
                  last.provider_switches,
                  "{count} bascule vers un fournisseur moins cher",
                  "{count} bascules vers un fournisseur moins cher",
                )}
              </small>
            )}
            {last.paused_for_budget && <Badge tone="warning">{t("Mis en pause par le budget")}</Badge>}
            {last.warning && <small className="subtle">{last.warning}</small>}
          </div>
        )}
        {owner ? (
          <form
            className="stack"
            aria-label={t("Modifier le budget du livre")}
            onSubmit={(event) => {
              event.preventDefault();
              setBusy(true);
              setStatus("");
              void run(async () => {
                const value = mode === "default" ? null : mode === "none" ? 0 : Number(amount);
                load(await send<BookBudgetView>(`/projects/${project.id}/budget`, { amount: value }, "PUT"));
                setStatus(t("Budget enregistré."));
              }).finally(() => setBusy(false));
            }}
          >
            <FormGrid>
              <Field label={t("Budget de ce livre")}>
                <Select
                  value={mode}
                  disabled={busy}
                  onChange={(event) => {
                    setStatus("");
                    setMode(event.target.value as typeof mode);
                  }}
                >
                  <option value="default">
                    {view.default_amount
                      ? t("Réglage de l’installation ({amount})", { amount: money(view.default_amount) })
                      : t("Réglage de l’installation (aucun)")}
                  </option>
                  <option value="own">{t("Plafond propre")}</option>
                  <option value="none">{t("Aucun budget pour ce livre")}</option>
                </Select>
              </Field>
              {mode === "own" && (
                <Field label={t("Montant du plafond")} hint={t("Dans la devise où les prix des fournisseurs sont saisis.")}>
                  <Input
                    type="number"
                    min={0.01}
                    step={0.01}
                    required
                    inputMode="decimal"
                    disabled={busy}
                    value={amount}
                    onChange={(event) => {
                      setStatus("");
                      setAmount(event.target.value);
                    }}
                  />
                </Field>
              )}
            </FormGrid>
            <div className="form-actions">
              <Button type="submit" variant="primary" loading={busy}>
                {t("Enregistrer le budget")}
              </Button>
            </div>
            {status && (
              <p role="status" className="form-status tone-text-success">
                {status}
              </p>
            )}
          </form>
        ) : (
          <p className="field-hint">{t("Seul le propriétaire du livre peut changer son budget.")}</p>
        )}
      </div>
    </Card>
  );
}

/** The estimate against what is left of the book's budget, in the launch confirmation. */
export function EstimateBudgetLine({ budget }: { budget?: EstimateBudget | null }) {
  const { t } = useI18n();
  if (!budget) return null;
  const remaining = money(budget.remaining);
  return (
    <small className={budget.exceeds ? "tone-text-danger" : undefined}>
      {!budget.exceeds
        ? t("Reste {remaining} sur le budget du livre.", { remaining })
        : budget.on_estimate === "refuse"
          ? t("Reste {remaining} sur le budget du livre : l’estimation le dépasse, le lancement sera refusé.", { remaining })
          : t("Reste {remaining} sur le budget du livre : l’estimation le dépasse, le travail se mettra en pause près du plafond.", {
              remaining,
            })}
    </small>
  );
}

/** The installation's default book cap, switch threshold and launch behaviour (administrators). */
export function BudgetSettings({ run }: { run: Run }) {
  const { t } = useI18n();
  const { confirm } = useDialogs();
  const [view, setView] = useState<BudgetAdmin | null>(null);
  const [draft, setDraft] = useState<BudgetValues | null>(null);
  const [unsupported, setUnsupported] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const load = (next: BudgetAdmin) => {
    setView(next);
    setDraft(next.values);
  };
  useEffect(() => {
    void run.background(async () => {
      const next = await api<BudgetAdmin>("/settings/budget").catch(() => null);
      if (next && typeof next === "object" && "values" in next) load(next);
      else setUnsupported(true);
    });
  }, [run]);
  if (unsupported) return <Callout tone="info">{t("Ce serveur ne gère pas encore les budgets.")}</Callout>;
  if (!view || !draft) return <LoadingBlock label={t("Budgets de l’installation")} />;
  const set = (change: Partial<BudgetValues>) => {
    setStatus("");
    setDraft({ ...draft, ...change });
  };
  async function reset() {
    const accepted = await confirm({
      title: t("Revenir aux valeurs de l’environnement ?"),
      message: t(
        "Les valeurs enregistrées ici sont oubliées ; les variables d’environnement s’appliquent de nouveau aux prochaines décisions.",
      ),
      confirmLabel: t("Revenir"),
    });
    if (!accepted) return;
    setBusy(true);
    await run(async () => {
      load(await api<BudgetAdmin>("/settings/budget", { method: "DELETE" }));
      setStatus(t("Valeurs de l’environnement rétablies."));
    }).finally(() => setBusy(false));
  }
  return (
    <Card
      className="settings-card"
      title={t("Budgets de l’installation")}
      description={t(
        "Plafond par défaut des livres qui n’ont pas le leur, et comportement près du plafond. Sans valeur enregistrée ici, les variables BUDGET_* s’appliquent.",
      )}
      actions={
        <Badge tone={view.saved ? "accent" : "neutral"}>
          {view.saved ? t("Valeurs enregistrées ici") : t("Valeurs de l’environnement")}
        </Badge>
      }
    >
      <form
        className="stack"
        aria-label={t("Budgets de l’installation")}
        onSubmit={(event) => {
          event.preventDefault();
          setBusy(true);
          void run(async () => {
            load(await send<BudgetAdmin>("/settings/budget", draft, "PUT"));
            setStatus(t("Réglages enregistrés. Ils s’appliquent aux prochaines décisions, sans redémarrage."));
          }).finally(() => setBusy(false));
        }}
      >
        <FormGrid columns={3}>
          <Field
            label={t("Plafond par défaut d’un livre")}
            hint={`${t("0 : aucun plafond par défaut.")} ${t("Valeur de l’environnement : {value}", {
              value: money(view.defaults.default_book),
            })}`}
          >
            <Input
              type="number"
              min={0}
              step={0.01}
              required
              inputMode="decimal"
              disabled={busy}
              value={draft.default_book}
              onChange={(event) => set({ default_book: Number(event.target.value || 0) })}
            />
          </Field>
          <Field
            label={t("Seuil de bascule (%)")}
            hint={t("À ce niveau, un travail passe à un fournisseur moins cher, ou se met en pause s’il n’y en a pas.")}
          >
            <Input
              type="number"
              min={50}
              max={100}
              step={1}
              required
              inputMode="numeric"
              disabled={busy}
              value={Math.round(draft.switch_threshold * 100)}
              onChange={(event) => set({ switch_threshold: Number(event.target.value || 0) / 100 })}
            />
          </Field>
          <Field label={t("Estimation au-dessus du reste")}>
            <Select
              value={draft.on_estimate}
              disabled={busy}
              onChange={(event) => set({ on_estimate: event.target.value as BudgetValues["on_estimate"] })}
            >
              <option value="warn">{t("Avertir et lancer")}</option>
              <option value="refuse">{t("Refuser le lancement")}</option>
            </Select>
          </Field>
        </FormGrid>
        <div className="form-actions">
          <Button type="submit" variant="primary" loading={busy}>
            {t("Enregistrer les budgets")}
          </Button>
          <Button disabled={busy || !view.saved} onClick={() => void reset()}>
            {t("Revenir aux valeurs de l’environnement")}
          </Button>
        </div>
        {status && (
          <p role="status" className="form-status tone-text-success">
            {status}
          </p>
        )}
      </form>
    </Card>
  );
}

/** Cap and period chosen when a token is created (empty amount: no cap). */
export function TokenBudgetFields({
  amount,
  period,
  onChange,
  disabled,
}: {
  amount: string;
  period: "month" | "total";
  onChange: (amount: string, period: "month" | "total") => void;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  return (
    <FormGrid>
      <Field
        label={t("Budget du jeton")}
        hint={t(
          "Facultatif. Une requête qui lancerait du travail est refusée (HTTP 402) une fois le plafond atteint ; un travail en cours se met en pause près du plafond.",
        )}
      >
        <Input
          type="number"
          min={0.01}
          step={0.01}
          inputMode="decimal"
          disabled={disabled}
          value={amount}
          placeholder={t("Vide : aucun plafond.")}
          onChange={(event) => onChange(event.target.value, period)}
        />
      </Field>
      <Field label={t("Période")}>
        <Select
          value={period}
          disabled={disabled}
          onChange={(event) => onChange(amount, event.target.value as "month" | "total")}
        >
          <option value="month">{t("Par mois")}</option>
          <option value="total">{t("Sur toute la vie du jeton")}</option>
        </Select>
      </Field>
    </FormGrid>
  );
}

/** What a token's requests spent against its cap, with a small form to change it. */
export function TokenBudgetLine({
  token,
  run,
  onSaved,
}: {
  token: { id: string; name: string; state: string; budget?: TokenBudgetView | null };
  run: Run;
  onSaved: () => Promise<void>;
}) {
  const { t } = useI18n();
  const budget = token.budget;
  const [amount, setAmount] = useState(budget ? String(budget.amount) : "");
  const [period, setPeriod] = useState<"month" | "total">(budget?.period || "month");
  const [busy, setBusy] = useState(false);
  if (token.budget === undefined) return null; // servers before 0.7
  const ratio = budget ? budget.spent / budget.amount : 0;
  return (
    <div className="token-budget">
      {budget && (
        <small className={ratio >= 1 ? "tone-text-danger" : undefined}>
          {budget.period === "month"
            ? t("Budget : {spent} sur {amount} ce mois-ci", { spent: money(budget.spent), amount: money(budget.amount) })
            : t("Budget : {spent} sur {amount} au total", { spent: money(budget.spent), amount: money(budget.amount) })}
          {budget.resets_at ? ` · ${t("Remis à zéro le {date}", { date: formatDateTime(budget.resets_at) })}` : ""}
        </small>
      )}
      {token.state !== "revoked" && (
        <details className="disclosure">
          <summary>{t("Modifier le budget")}</summary>
          <form
            className="stack"
            aria-label={t("Budget de {name}", { name: token.name })}
            onSubmit={(event) => {
              event.preventDefault();
              setBusy(true);
              void run(async () => {
                await send(`/tokens/${token.id}/budget`, { amount: amount ? Number(amount) : null, period }, "PUT");
                await onSaved();
              }).finally(() => setBusy(false));
            }}
          >
            <TokenBudgetFields
              amount={amount}
              period={period}
              disabled={busy}
              onChange={(nextAmount, nextPeriod) => {
                setAmount(nextAmount);
                setPeriod(nextPeriod);
              }}
            />
            <div>
              <Button type="submit" loading={busy}>
                {t("Enregistrer le budget")}
              </Button>
            </div>
          </form>
        </details>
      )}
    </div>
  );
}
