import { createContext, useContext, useEffect, useMemo, useState } from "react";

export const locales = ["fr", "en"] as const;
export type Locale = (typeof locales)[number];

const french = {
  "language.french": "Français",
  "language.english": "English",
  "app.library": "Bibliothèque",
  "app.settings": "Paramètres",
  "app.statistics": "Statistiques",
  "app.project": "Projet",
  "app.loading": "Ouverture de Libris…",
  "app.theme": "Changer de thème",
  "app.language": "Langue",
  "app.light": "Clair",
  "app.dark": "Sombre",
  "app.logout": "Déconnexion",
  "app.error": "L’opération n’a pas abouti.",
  "app.closeError": "Fermer l’erreur",
  "app.sessionExpired": "Votre session a expiré ou a été révoquée. Reconnectez-vous.",
  "login.eyebrow": "Traduction littéraire · Mémoire contextuelle",
  "login.title": "Retrouvez vos livres",
  "login.description": "Traduisez avec continuité, de la première page au dernier volume.",
  "login.username": "Utilisateur",
  "login.password": "Mot de passe",
  "login.submit": "Se connecter",
  "login.submitting": "Connexion…",
  "login.firstAccess": "Premier accès : identifiants définis dans le fichier .env de l’installation.",
  "status.waiting": "Service indisponible · reprise prévue",
  "status.blocked": "Intervention requise",
  "status.refused": "Refus du provider",
  "status.source_retained": "Original conservé",
  "status.interrupted": "Interrompue",
  "status.abandoned": "Interrompue",
  "status.pending": "En attente",
  "status.ready": "Prêt",
  "status.analyzing": "Analyse",
  "status.translating": "Traduction",
  "status.reviewing": "Relecture",
  "status.completed": "Terminé",
  "status.paused": "En pause",
  "status.cancelled": "Annulé",
  "status.failed": "Échec",
  "status.error": "Erreur",
  "status.ok": "Contrôles OK",
  "status.check": "Relecture facultative",
  "status.success": "Réussie",
  "status.running": "En cours",
  "status.syncing": "Synchronisation",
  "status.none": "Aucun",
  "status.archived": "Archivé",
};

type MessageKey = keyof typeof french;
type Catalog = Record<MessageKey, string>;
const featureTranslations: Record<string, string> = {};

const english: Catalog = {
  "language.french": "Français",
  "language.english": "English",
  "app.library": "Library",
  "app.settings": "Settings",
  "app.statistics": "Statistics",
  "app.project": "Project",
  "app.loading": "Opening Libris…",
  "app.theme": "Change theme",
  "app.language": "Language",
  "app.light": "Light",
  "app.dark": "Dark",
  "app.logout": "Sign out",
  "app.error": "The operation did not complete.",
  "app.closeError": "Dismiss error",
  "app.sessionExpired": "Your session expired or was revoked. Please sign in again.",
  "login.eyebrow": "Literary translation · Contextual memory",
  "login.title": "Find your books",
  "login.description": "Translate consistently, from the first page to the final volume.",
  "login.username": "Username",
  "login.password": "Password",
  "login.submit": "Sign in",
  "login.submitting": "Signing in…",
  "login.firstAccess": "First access: credentials are defined in the installation .env file.",
  "status.waiting": "Service unavailable · retry scheduled",
  "status.blocked": "Action required",
  "status.refused": "Provider refusal",
  "status.source_retained": "Source retained",
  "status.interrupted": "Interrupted",
  "status.abandoned": "Interrupted",
  "status.pending": "Pending",
  "status.ready": "Ready",
  "status.analyzing": "Analysis",
  "status.translating": "Translation",
  "status.reviewing": "Review",
  "status.completed": "Complete",
  "status.paused": "Paused",
  "status.cancelled": "Cancelled",
  "status.failed": "Failed",
  "status.error": "Error",
  "status.ok": "Checks passed",
  "status.check": "Optional review",
  "status.success": "Successful",
  "status.running": "Running",
  "status.syncing": "Synchronizing",
  "status.none": "None",
  "status.archived": "Archived",
};

let activeLocale: Locale = "fr";

export type Vars = Record<string, string | number>;

export function registerTranslations(translations: Record<string, string>) {
  Object.assign(featureTranslations, translations);
}

function lookup(locale: Locale, key: string): string {
  if (locale === "en") return featureTranslations[key] || english[key as MessageKey] || key;
  return french[key as MessageKey] || key;
}

function interpolate(locale: Locale, text: string, vars?: Vars): string {
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (whole, name: string) => {
    const value = vars[name];
    if (value === undefined) return whole;
    return typeof value === "number" ? formatNumber(value, undefined, locale) : value;
  });
}

function translate(locale: Locale, key: string, vars?: Vars): string {
  return interpolate(locale, lookup(locale, key), vars);
}

/** Chooses the grammatical form from the interface language, then translates that French form. */
function translatePlural(locale: Locale, count: number, one: string, other: string, vars?: Vars): string {
  const form = new Intl.PluralRules(locale).select(count) === "one" ? one : other;
  return translate(locale, form, { count, ...vars });
}

export function message(key: string, vars?: Vars): string {
  return translate(activeLocale, key, vars);
}

export function plural(count: number, one: string, other: string, vars?: Vars): string {
  return translatePlural(activeLocale, count, one, other, vars);
}

export function getLocale(): Locale {
  return activeLocale;
}

export function formatNumber(
  value: number,
  options: Intl.NumberFormatOptions = { maximumFractionDigits: 1 },
  locale: Locale = activeLocale,
): string {
  return new Intl.NumberFormat(locale, options).format(value);
}

/** `value` is already a percentage (0–100), as served by the API. */
export function formatPercent(value: number, locale: Locale = activeLocale): string {
  return new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 0 }).format(value / 100);
}

export function formatCompact(value: number, locale: Locale = activeLocale): string {
  return new Intl.NumberFormat(locale, { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function formatDateTime(timestamp: number, options?: Intl.DateTimeFormatOptions): string {
  return new Date(timestamp * 1000).toLocaleString(
    activeLocale,
    options || { dateStyle: "medium", timeStyle: "short" },
  );
}

type Translate = (key: string, vars?: Vars) => string;

const I18nContext = createContext<{
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: Translate;
  tp: (count: number, one: string, other: string, vars?: Vars) => string;
}>({ locale: "fr", setLocale: () => {}, t: message, tp: plural });

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocale] = useState<Locale>(() => {
    const saved = localStorage.getItem("locale");
    const initial = locales.includes(saved as Locale) ? (saved as Locale) : "fr";
    activeLocale = initial;
    return initial;
  });
  useEffect(() => {
    activeLocale = locale;
    document.documentElement.lang = locale;
    localStorage.setItem("locale", locale);
  }, [locale]);
  const value = useMemo(
    () => ({
      locale,
      setLocale: (next: Locale) => {
        activeLocale = next;
        setLocale(next);
      },
      t: (key: string, vars?: Vars) => translate(locale, key, vars),
      tp: (count: number, one: string, other: string, vars?: Vars) =>
        translatePlural(locale, count, one, other, vars),
    }),
    [locale],
  );
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  return useContext(I18nContext);
}
