import { useCallback, useEffect, useState } from "react";
import { api, ApiError, send } from "../api";
import { formatDateTime, formatNumber, registerTranslations, useI18n } from "../i18n";
import type { Job, Run, User } from "../types";
import {
  Badge,
  Button,
  Callout,
  Card,
  EmptyState,
  Field,
  FormGrid,
  IconButton,
  Input,
  LoadingBlock,
  Page,
  PageHeader,
  Select,
  Stat,
  useDialogs,
} from "../ui";
import type { Tone } from "../ui";

registerTranslations({
  "File d’attente": "Queue",
  "Les travaux sont pris à tour de rôle entre les comptes, par priorité. La position est celle du travail dans la file de son fournisseur.":
    "Jobs are taken in turn between accounts, by priority. The position is the job's place in the line of its provider.",
  "Chargement de la file d’attente…": "Loading the queue…",
  "Vos travaux en cours": "Your running jobs",
  "Vos travaux en attente": "Your waiting jobs",
  "Priorité maximale": "Highest priority",
  "{count} au plus": "{count} at most",
  "sans limite": "no limit",
  "Aucun travail en cours.": "No running job.",
  "Aucun travail en attente.": "No waiting job.",
  "Les travaux lancés apparaissent ici tant qu’ils attendent leur tour.":
    "Launched jobs appear here while they wait for their turn.",
  Basse: "Low",
  Normale: "Normal",
  Haute: "High",
  Priorité: "Priority",
  "Priorité de {title}": "Priority of {title}",
  "relevée par l’attente": "raised by waiting",
  "Position {position}": "Position {position}",
  "Démarre dans quelques secondes.": "Starts within seconds.",
  "Le fournisseur {provider} est occupé : ce travail démarre quand une place se libère.":
    "The provider {provider} is busy: this job starts when a slot frees up.",
  "Le compte a atteint son nombre de travaux simultanés : ce travail démarre quand l’un d’eux se termine.":
    "The account has reached its number of simultaneous jobs: this job starts when one of them ends.",
  "Le jeton d’API a atteint son nombre de travaux simultanés : ce travail démarre quand l’un d’eux se termine.":
    "The API token has reached its number of simultaneous jobs: this job starts when one of them ends.",
  "Fournisseur indisponible : nouvel essai le {date}.": "Provider unavailable: next attempt on {date}.",
  "Aucun fournisseur : choisissez-en un dans les réglages du livre.":
    "No provider: choose one in the book's settings.",
  "En file depuis le {date}": "Queued since {date}",
  "de {owner}": "by {owner}",
  "Actualiser la file d’attente": "Refresh the queue",
  "Ce travail attend son tour dans la file d’attente.": "This job is waiting for its turn in the queue.",
  "Voir la file d’attente": "See the queue",
  "Priorité enregistrée.": "Priority saved.",
  // Administration
  "File d’attente équitable": "Fair queue",
  "Quotas appliqués à chaque compte et délai qui relève la priorité d’un travail qui attend. Sans valeur enregistrée ici, les variables QUEUE_* s’appliquent.":
    "Quotas applied to each account and the delay that raises the priority of a waiting job. Without a value saved here, the QUEUE_* variables apply.",
  "Travaux simultanés par compte": "Simultaneous jobs per account",
  "Travaux en attente par compte": "Waiting jobs per account",
  "Relèvement de priorité (minutes)": "Priority raise (minutes)",
  "0 : sans limite. Au-delà, les travaux suivants attendent leur tour.":
    "0: no limit. Beyond it, the next jobs wait for their turn.",
  "0 : sans limite. Au-delà, un nouveau travail ou une requête d’API est refusé (HTTP 429).":
    "0: no limit. Beyond it, a new job or API request is refused (HTTP 429).",
  "Un travail monte d’un niveau de priorité par durée d’attente. 0 : jamais.":
    "A job rises by one priority level per waiting period. 0: never.",
  "Quotas par compte": "Per-account quotas",
  "Vide : la valeur de l’installation. 0 : sans limite pour ce compte. La priorité haute reste réservée aux administrateurs, sauf pour les comptes autorisés ici.":
    "Empty: the installation's value. 0: no limit for this account. High priority stays reserved to administrators, except for the accounts allowed here.",
  "Ajouter un compte": "Add an account",
  Compte: "Account",
  "Retirer {name}": "Remove {name}",
  "Valeur de l’installation": "Installation value",
  "Par défaut": "Default",
  "Enregistrer la file d’attente": "Save the queue",
  "Ce serveur ne permet pas encore de régler la file d’attente depuis l’interface.":
    "This server does not let the interface set the queue yet.",
});

export const PRIORITY_LABELS: Record<string, string> = { low: "Basse", normal: "Normale", high: "Haute" };
const PRIORITY_TONES: Record<string, Tone> = { low: "neutral", normal: "info", high: "accent" };
const PRIORITIES = ["low", "normal", "high"];

export interface QueueEntry {
  job_id: string;
  project_id: string;
  title: string;
  owner: string;
  mine: boolean;
  provider_id: string | null;
  provider: string;
  operation: string;
  status: string;
  priority: string;
  effective_priority: string;
  queued_at: number;
  next_attempt: number;
  position: number | null;
  reason: string;
}
export interface QueueView {
  running: QueueEntry[];
  waiting: QueueEntry[];
  totals: { waiting: number; running: number };
  account: { running: number; waiting: number; max_running: number; max_queued: number; max_priority: string };
  aging_minutes: number;
}

/** Why a waiting job has not started, in one sentence. */
export function useQueueReason() {
  const { t } = useI18n();
  return (entry: QueueEntry) => {
    switch (entry.reason) {
      case "starting":
        return t("Démarre dans quelques secondes.");
      case "provider_busy":
        return t("Le fournisseur {provider} est occupé : ce travail démarre quand une place se libère.", {
          provider: entry.provider || "—",
        });
      case "account_limit":
        return t("Le compte a atteint son nombre de travaux simultanés : ce travail démarre quand l’un d’eux se termine.");
      case "token_limit":
        return t("Le jeton d’API a atteint son nombre de travaux simultanés : ce travail démarre quand l’un d’eux se termine.");
      case "retry_scheduled":
        return t("Fournisseur indisponible : nouvel essai le {date}.", { date: formatDateTime(entry.next_attempt) });
      case "provider_missing":
        return t("Aucun fournisseur : choisissez-en un dans les réglages du livre.");
      default:
        return "";
    }
  };
}

function PriorityBadge({ entry }: { entry: QueueEntry }) {
  const { t } = useI18n();
  const raised = entry.effective_priority !== entry.priority;
  return (
    <Badge tone={PRIORITY_TONES[entry.effective_priority] || "neutral"}>
      {t(PRIORITY_LABELS[entry.priority] || entry.priority)}
      {raised && ` → ${t(PRIORITY_LABELS[entry.effective_priority])} (${t("relevée par l’attente")})`}
    </Badge>
  );
}

function QueueRow({
  entry,
  ceiling,
  admin,
  busy,
  onPriority,
}: {
  entry: QueueEntry;
  ceiling: string;
  admin: boolean;
  busy: boolean;
  onPriority: (entry: QueueEntry, priority: string) => void;
}) {
  const { t } = useI18n();
  const reason = useQueueReason()(entry);
  const allowed = PRIORITIES.slice(0, PRIORITIES.indexOf(admin ? "high" : ceiling) + 1);
  return (
    <li>
      <div className="grow queue-main">
        {entry.position !== null && entry.reason !== "running" && (
          <Badge tone="neutral">{t("Position {position}", { position: String(entry.position) })}</Badge>
        )}
        <a href={`#project/${entry.project_id}`}>
          <strong>{entry.title || entry.project_id}</strong>
        </a>
        <PriorityBadge entry={entry} />
        <small className="subtle">
          {[
            entry.owner && !entry.mine ? t("de {owner}", { owner: entry.owner }) : "",
            entry.provider,
            t("En file depuis le {date}", { date: formatDateTime(entry.queued_at) }),
          ]
            .filter(Boolean)
            .join(" · ")}
        </small>
        {reason && <small className="queue-reason">{reason}</small>}
      </div>
      {(entry.mine || admin) && (
        <Field label={t("Priorité")} className="queue-priority">
          <Select
            aria-label={t("Priorité de {title}", { title: entry.title })}
            value={entry.priority}
            disabled={busy}
            onChange={(event) => onPriority(entry, event.target.value)}
          >
            {PRIORITIES.map((value) => (
              <option key={value} value={value} disabled={!allowed.includes(value) && value !== entry.priority}>
                {t(PRIORITY_LABELS[value])}
              </option>
            ))}
          </Select>
        </Field>
      )}
    </li>
  );
}

/** Running and waiting jobs the person may see, with each waiting job's place and reason. */
export function QueuePage({ run, user }: { run: Run; user: User }) {
  const { t } = useI18n();
  const [view, setView] = useState<QueueView | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const load = useCallback(() => run.background(async () => setView(await api<QueueView>("/queue"))), [run]);
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 10000);
    return () => clearInterval(timer);
  }, [load]);
  const change = (entry: QueueEntry, priority: string) => {
    setBusy(true);
    setStatus("");
    void run(async () => {
      await send(`/queue/${entry.job_id}/priority`, { priority }, "PUT");
      setView(await api<QueueView>("/queue"));
      setStatus(t("Priorité enregistrée."));
    }).finally(() => setBusy(false));
  };
  const limit = (value: number) => (value ? t("{count} au plus", { count: formatNumber(value) }) : t("sans limite"));
  const list = (entries: QueueEntry[], label: string, empty: string) =>
    entries.length ? (
      <ul className="queue-list" aria-label={label}>
        {entries.map((entry) => (
          <QueueRow
            key={entry.job_id}
            entry={entry}
            ceiling={view!.account.max_priority}
            admin={user.admin}
            busy={busy}
            onPriority={change}
          />
        ))}
      </ul>
    ) : (
      <p className="card-inset subtle">{empty}</p>
    );
  return (
    <Page>
      <PageHeader
        title={t("File d’attente")}
        description={t(
          "Les travaux sont pris à tour de rôle entre les comptes, par priorité. La position est celle du travail dans la file de son fournisseur.",
        )}
        actions={
          <IconButton icon="refresh" variant="secondary" label={t("Actualiser la file d’attente")} onClick={() => void load()} />
        }
      />
      {view === null ? (
        <LoadingBlock label={t("Chargement de la file d’attente…")} lines={4} />
      ) : (
        <div className="stack">
          <div className="stat-grid">
            <Stat label={t("Vos travaux en cours")} value={formatNumber(view.account.running)} hint={limit(view.account.max_running)} />
            <Stat label={t("Vos travaux en attente")} value={formatNumber(view.account.waiting)} hint={limit(view.account.max_queued)} />
            <Stat label={t("Priorité maximale")} value={t(PRIORITY_LABELS[view.account.max_priority])} />
          </div>
          {status && (
            <p role="status" className="form-status tone-text-success">
              {status}
            </p>
          )}
          <Card title={`${t("En attente")} · ${formatNumber(view.waiting.length)}`} padded={false}>
            {view.waiting.length || view.running.length
              ? list(view.waiting, t("En attente"), t("Aucun travail en attente."))
              : (
                <EmptyState
                  icon="list"
                  title={t("Aucun travail en attente.")}
                  description={t("Les travaux lancés apparaissent ici tant qu’ils attendent leur tour.")}
                />
              )}
          </Card>
          <Card title={`${t("En cours")} · ${formatNumber(view.running.length)}`} padded={false}>
            {list(view.running, t("En cours"), t("Aucun travail en cours."))}
          </Card>
        </div>
      )}
    </Page>
  );
}

/** In a book: why its job has not started yet, with a link to the queue. Nothing once it runs. */
export function QueueHint({ projectId, job, refresh }: { projectId: string; job?: Job; refresh: number }) {
  const { t } = useI18n();
  const reasonOf = useQueueReason();
  const [entry, setEntry] = useState<QueueEntry | null>(null);
  const waiting = !!job && ["pending", "waiting"].includes(job.status);
  useEffect(() => {
    if (!waiting) {
      setEntry(null);
      return;
    }
    let current = true;
    api<QueueView>(`/queue?project_id=${encodeURIComponent(projectId)}`)
      .then((view) => current && setEntry(view.waiting.find((item) => item.job_id === job!.id) || null))
      // Servers before 0.7 have no queue: the book simply shows its status, as before.
      .catch(() => current && setEntry(null));
    return () => {
      current = false;
    };
  }, [projectId, job, waiting, refresh]);
  if (!waiting || !entry || entry.reason === "starting" || entry.reason === "retry_scheduled") return null;
  return (
    <Callout
      tone="info"
      role="status"
      title={t("Ce travail attend son tour dans la file d’attente.")}
      actions={
        <a className="btn btn-sm btn-secondary" href="#queue">
          {t("Voir la file d’attente")}
        </a>
      }
    >
      {[entry.position !== null ? t("Position {position}", { position: String(entry.position) }) : "", reasonOf(entry)]
        .filter(Boolean)
        .join(" · ")}
    </Callout>
  );
}

interface QueueValues {
  max_running_per_account: number;
  max_queued_per_account: number;
  aging_minutes: number;
}
interface AccountQuota {
  user_id: string;
  username?: string;
  max_running: number | null;
  max_queued: number | null;
  max_priority: string | null;
}
interface QueueAdmin {
  values: QueueValues;
  defaults: QueueValues;
  accounts: AccountQuota[];
  saved: boolean;
}

const optional = (value: string) => (value === "" ? null : Number(value));

/** Settings › Queue: installation quotas, aging delay and per-account overrides. */
export function QueueSettings({ run }: { run: Run }) {
  const { t } = useI18n();
  const { confirm } = useDialogs();
  const [view, setView] = useState<QueueAdmin | null>(null);
  const [draft, setDraft] = useState<QueueValues | null>(null);
  const [accounts, setAccounts] = useState<AccountQuota[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [unsupported, setUnsupported] = useState(false);
  const load = (next: QueueAdmin) => {
    setView(next);
    setDraft(next.values);
    setAccounts(next.accounts);
  };
  useEffect(() => {
    void run.background(async () => {
      const [value, list] = await Promise.all([
        api<QueueAdmin>("/settings/queue").catch((error: unknown) => {
          if (error instanceof ApiError && error.status === 404) return null;
          throw error;
        }),
        api<User[]>("/users"),
      ]);
      setUsers(list);
      if (value) load(value);
      else setUnsupported(true);
    });
  }, [run]);
  if (unsupported)
    return <Callout tone="info">{t("Ce serveur ne permet pas encore de régler la file d’attente depuis l’interface.")}</Callout>;
  if (!view || !draft) return <LoadingBlock label={t("Chargement…")} />;
  const set = (change: Partial<QueueValues>) => {
    setStatus("");
    setDraft({ ...draft, ...change });
  };
  const setAccount = (index: number, change: Partial<AccountQuota>) => {
    setStatus("");
    setAccounts(accounts.map((item, position) => (position === index ? { ...item, ...change } : item)));
  };
  const free = users.filter((u) => !accounts.some((item) => item.user_id === u.id));
  async function save() {
    setBusy(true);
    await run(async () => {
      load(
        await send<QueueAdmin>(
          "/settings/queue",
          {
            ...draft,
            accounts: accounts.map(({ user_id, max_running, max_queued, max_priority }) => ({
              user_id,
              max_running,
              max_queued,
              max_priority,
            })),
          },
          "PUT",
        ),
      );
      setStatus(t("Réglages enregistrés. Ils s’appliquent aux prochaines décisions, sans redémarrage."));
    }).finally(() => setBusy(false));
  }
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
      load(await api<QueueAdmin>("/settings/queue", { method: "DELETE" }));
      setStatus(t("Valeurs de l’environnement rétablies."));
    }).finally(() => setBusy(false));
  }
  const numberField = (name: keyof QueueValues, label: string, hint: string, max: number) => (
    <Field label={t(label)} hint={`${t(hint)} ${t("Valeur par défaut : {value}", { value: String(view.defaults[name]) })}`}>
      <Input
        type="number"
        min={0}
        max={max}
        required
        inputMode="numeric"
        disabled={busy}
        value={draft[name]}
        onChange={(event) => set({ [name]: Number(event.target.value || 0) })}
      />
    </Field>
  );
  return (
    <Card
      className="settings-card"
      title={t("File d’attente équitable")}
      description={t(
        "Quotas appliqués à chaque compte et délai qui relève la priorité d’un travail qui attend. Sans valeur enregistrée ici, les variables QUEUE_* s’appliquent.",
      )}
      actions={
        <Badge tone={view.saved ? "accent" : "neutral"}>
          {view.saved ? t("Valeurs enregistrées ici") : t("Valeurs de l’environnement")}
        </Badge>
      }
    >
      <form
        className="stack"
        aria-label={t("File d’attente équitable")}
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <FormGrid columns={3}>
          {numberField(
            "max_running_per_account",
            "Travaux simultanés par compte",
            "0 : sans limite. Au-delà, les travaux suivants attendent leur tour.",
            1000,
          )}
          {numberField(
            "max_queued_per_account",
            "Travaux en attente par compte",
            "0 : sans limite. Au-delà, un nouveau travail ou une requête d’API est refusé (HTTP 429).",
            100000,
          )}
          {numberField(
            "aging_minutes",
            "Relèvement de priorité (minutes)",
            "Un travail monte d’un niveau de priorité par durée d’attente. 0 : jamais.",
            7 * 24 * 60,
          )}
        </FormGrid>
        <fieldset className="queue-accounts">
          <legend>{t("Quotas par compte")}</legend>
          <p className="field-hint">
            {t(
              "Vide : la valeur de l’installation. 0 : sans limite pour ce compte. La priorité haute reste réservée aux administrateurs, sauf pour les comptes autorisés ici.",
            )}
          </p>
          {accounts.map((item, index) => {
            const name = item.username || users.find((u) => u.id === item.user_id)?.username || item.user_id;
            return (
              <div className="queue-account" key={item.user_id}>
                <strong className="queue-account-name">{name}</strong>
                <Field label={t("Travaux simultanés par compte")}>
                  <Input
                    type="number"
                    min={0}
                    max={1000}
                    inputMode="numeric"
                    placeholder={t("Par défaut")}
                    disabled={busy}
                    value={item.max_running ?? ""}
                    onChange={(event) => setAccount(index, { max_running: optional(event.target.value) })}
                  />
                </Field>
                <Field label={t("Travaux en attente par compte")}>
                  <Input
                    type="number"
                    min={0}
                    max={100000}
                    inputMode="numeric"
                    placeholder={t("Par défaut")}
                    disabled={busy}
                    value={item.max_queued ?? ""}
                    onChange={(event) => setAccount(index, { max_queued: optional(event.target.value) })}
                  />
                </Field>
                <Field label={t("Priorité maximale")}>
                  <Select
                    disabled={busy}
                    value={item.max_priority ?? ""}
                    onChange={(event) => setAccount(index, { max_priority: event.target.value || null })}
                  >
                    <option value="">{t("Valeur de l’installation")}</option>
                    {PRIORITIES.map((value) => (
                      <option key={value} value={value}>
                        {t(PRIORITY_LABELS[value])}
                      </option>
                    ))}
                  </Select>
                </Field>
                <IconButton
                  icon="trash"
                  variant="ghost"
                  label={t("Retirer {name}", { name })}
                  disabled={busy}
                  onClick={() => setAccounts(accounts.filter((_, position) => position !== index))}
                />
              </div>
            );
          })}
          {free.length > 0 && (
            <Field label={t("Ajouter un compte")} className="queue-add">
              <Select
                value=""
                disabled={busy}
                onChange={(event) => {
                  const chosen = free.find((u) => u.id === event.target.value);
                  if (chosen)
                    setAccounts([
                      ...accounts,
                      { user_id: chosen.id, username: chosen.username, max_running: null, max_queued: null, max_priority: null },
                    ]);
                }}
              >
                <option value="">{t("Compte")}…</option>
                {free.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.username}
                  </option>
                ))}
              </Select>
            </Field>
          )}
        </fieldset>
        <div className="form-actions">
          <Button type="submit" variant="primary" loading={busy}>
            {t("Enregistrer la file d’attente")}
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
