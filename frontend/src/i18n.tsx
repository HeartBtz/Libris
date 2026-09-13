import { createContext, useContext, useEffect, useState } from "react";

export const locales = ["fr", "en"] as const;
export type Locale = (typeof locales)[number];

const french = {
  "language.french": "Français",
  "language.english": "English",
  "app.library": "Bibliothèque",
  "app.settings": "Paramètres",
  "app.project": "Projet",
  "app.loading": "Ouverture de Libris…",
  "app.theme": "Changer de thème",
  "app.language": "Langue",
  "app.light": "Clair",
  "app.dark": "Sombre",
  "app.logout": "Déconnexion",
  "app.error": "L’opération n’a pas abouti.",
  "app.closeError": "Fermer l’erreur",
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
  "status.check": "À vérifier",
  "status.success": "Réussie",
  "status.running": "En cours",
  "status.syncing": "Synchronisation",
};

type MessageKey = keyof typeof french;
type Catalog = Record<MessageKey, string>;

const english: Catalog = {
  "language.french": "Français",
  "language.english": "English",
  "app.library": "Library",
  "app.settings": "Settings",
  "app.project": "Project",
  "app.loading": "Opening Libris…",
  "app.theme": "Change theme",
  "app.language": "Language",
  "app.light": "Light",
  "app.dark": "Dark",
  "app.logout": "Sign out",
  "app.error": "The operation did not complete.",
  "app.closeError": "Dismiss error",
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
  "status.check": "Needs review",
  "status.success": "Successful",
  "status.running": "Running",
  "status.syncing": "Synchronizing",
};

const catalogs: Record<Locale, Catalog> = { fr: french, en: english };
let activeLocale: Locale = "fr";

export function message(key: MessageKey): string {
  return catalogs[activeLocale][key] || french[key];
}

export function getLocale(): Locale {
  return activeLocale;
}

const I18nContext = createContext<{
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: MessageKey) => string;
}>({ locale: "fr", setLocale: () => {}, t: message });

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocale] = useState<Locale>(() => {
    const saved = localStorage.getItem("locale");
    return locales.includes(saved as Locale) ? (saved as Locale) : "fr";
  });
  useEffect(() => {
    activeLocale = locale;
    document.documentElement.lang = locale;
    localStorage.setItem("locale", locale);
  }, [locale]);
  const t = (key: MessageKey) => catalogs[locale][key] || french[key];
  return (
    <I18nContext.Provider value={{ locale, setLocale, t }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n() {
  return useContext(I18nContext);
}
