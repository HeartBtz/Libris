import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, send } from "./api";
import type { Run, User } from "./types";
import { Settings } from "./features/Settings";
import { Account } from "./features/Account";
import { Workspace } from "./features/Workspace";
import { Library } from "./features/Library";
import { SeriesPage } from "./features/SeriesPage";
import { Login } from "./features/Login";
import { Statistics } from "./features/Statistics";
import { SharedGlossaries } from "./features/SharedGlossaries";
import { QueuePage } from "./features/Queue";
import { message, registerTranslations, useI18n } from "./i18n";
import type { Locale } from "./i18n";
import { useTheme } from "./theme";
import type { ThemePreference } from "./theme";
import { hasUnsavedChanges, useLeaveGuard } from "./unsaved";
import { Icon, IconButton, Menu, Tooltip, cx, useFocusTrap } from "./ui";
import type { IconName } from "./ui";

registerTranslations({
  "Aller au contenu": "Skip to content",
  "Navigation principale": "Primary navigation",
  Menu: "Menu",
  "Fermer le menu": "Close menu",
  "Mon compte": "My account",
  "Menu du compte": "Account menu",
  "Réduire la barre latérale": "Collapse sidebar",
  "Déployer la barre latérale": "Expand sidebar",
  Thème: "Theme",
  Système: "System",
  Administrateur: "Administrator",
  Utilisateur: "User",
  Série: "Series",
  "Glossaires partagés": "Shared glossaries",
  "File d’attente": "Queue",
});

export function App() {
  const { locale, setLocale, t } = useI18n();
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  const [route, setRoute] = useState(location.hash.slice(1) || "library");
  const [error, setError] = useState("");
  const [theme, setTheme] = useTheme();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("sidebar") === "collapsed");
  const confirmLeave = useLeaveGuard();
  const routeRef = useRef(route);
  const focusContent = () =>
    requestAnimationFrame(() => document.getElementById("main-content")?.focus());
  const signedIn = useRef(false);
  useEffect(() => {
    signedIn.current = !!user;
  }, [user]);
  const run: Run = useMemo(() => {
    const execute = (background: boolean) => async (task: () => Promise<void>) => {
      if (!background) setError("");
      try {
        await task();
      } catch (e) {
        if (e instanceof ApiError && e.status === 401 && signedIn.current) {
          // Expired or revoked session: back to the login screen, not a stuck page.
          setUser(null);
          setError(message("app.sessionExpired"));
          return;
        }
        const text = e instanceof Error ? e.message : String(e);
        // A periodic refresh must neither erase nor replace the error of a user action.
        setError((shown) => (background && shown ? shown : text));
      }
    };
    return Object.assign(execute(false), { background: execute(true) });
  }, []);
  useEffect(() => {
    api<User>("/auth/me")
      .then(setUser)
      .catch(() => {})
      .finally(() => setChecking(false));
  }, []);
  useEffect(() => {
    const change = () => {
      const next = location.hash.slice(1) || "library";
      if (next === routeRef.current) return;
      if (hasUnsavedChanges()) {
        // The hash already moved: put it back while the user decides about the drafts.
        history.replaceState(null, "", `#${routeRef.current}`);
        void confirmLeave().then((leave) => {
          if (leave) location.hash = next;
        });
        return;
      }
      routeRef.current = next;
      setRoute(next);
      setDrawerOpen(false);
      focusContent();
    };
    const unload = (event: BeforeUnloadEvent) => {
      if (!hasUnsavedChanges()) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("hashchange", change);
    window.addEventListener("beforeunload", unload);
    return () => {
      window.removeEventListener("hashchange", change);
      window.removeEventListener("beforeunload", unload);
    };
  }, [confirmLeave]);
  useEffect(() => {
    localStorage.setItem("sidebar", collapsed ? "collapsed" : "expanded");
  }, [collapsed]);
  useEffect(() => {
    document.title = `${route === "settings" ? t("app.settings") : route === "statistics" ? t("app.statistics") : route === "account" ? t("Mon compte") : route === "queue" ? t("File d’attente") : route === "library" ? t("app.library") : route.startsWith("series/") ? t("Série") : route === "glossaries" ? t("Glossaires partagés") : t("app.project")} · Libris`;
  }, [route, t]);
  const drawer = useFocusTrap<HTMLElement>(drawerOpen, () => setDrawerOpen(false));
  if (checking)
    return (
      <main className="app-loading" aria-busy="true">
        <img src="/assets/libris-icon.png" alt="" />
        <span>{t("app.loading")}</span>
      </main>
    );
  const errorBanner = error && (
    <div className="error-banner" role="alert">
      <Icon name="alert" />
      <div className="error-banner-text">
        <strong>{t("app.error")}</strong> <span className="error-text">{error}</span>
      </div>
      <IconButton icon="x" size="sm" label={t("app.closeError")} tooltip={false} onClick={() => setError("")} />
    </div>
  );
  if (!user)
    return (
      <Login
        run={run}
        onLogin={setUser}
        error={errorBanner}
        theme={theme}
        setTheme={setTheme}
      />
    );
  const logout = () =>
    void run(async () => {
      // Logging out must always work, even once the session is gone server-side.
      signedIn.current = false;
      try {
        await send("/auth/logout");
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 401)) throw e;
      } finally {
        setUser(null);
      }
    });
  const section = route.startsWith("project/") || route.startsWith("series/") ? "library" : route;
  const links: [string, string, IconName][] = [
    ["library", t("app.library"), "book"],
    ["glossaries", t("Glossaires partagés"), "languages"],
    ["queue", t("File d’attente"), "list"],
    ...(user.admin
      ? ([
          ["statistics", t("app.statistics"), "chart"],
          ["settings", t("app.settings"), "settings"],
        ] as [string, string, IconName][])
      : []),
  ];
  return (
    <div className={cx("app-shell", collapsed && "sidebar-collapsed", drawerOpen && "drawer-open")}>
      <a
        className="skip-link"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("main-content")?.focus();
        }}
      >
        {t("Aller au contenu")}
      </a>
      <header className="mobile-topbar">
        <IconButton
          icon="menu"
          label={t("Menu")}
          aria-expanded={drawerOpen}
          aria-controls="sidebar"
          onClick={() => setDrawerOpen(true)}
          tooltip={false}
        />
        <a className="brand" href="#library">
          <img src="/assets/libris-icon.png" alt="" />
          <span>Libris</span>
        </a>
      </header>
      {drawerOpen && <div className="drawer-backdrop" onClick={() => setDrawerOpen(false)} />}
      <aside id="sidebar" ref={drawer} className="sidebar">
        <div className="sidebar-top">
          <a className="brand" href="#library" aria-label="Libris">
            <img src="/assets/libris-icon.png" alt="" />
            <span className="sidebar-text">Libris</span>
          </a>
          <IconButton
            className="sidebar-collapse"
            icon="sidebar"
            size="sm"
            label={collapsed ? t("Déployer la barre latérale") : t("Réduire la barre latérale")}
            onClick={() => setCollapsed((value) => !value)}
          />
          <IconButton
            className="drawer-close"
            icon="x"
            label={t("Fermer le menu")}
            onClick={() => setDrawerOpen(false)}
            tooltip={false}
          />
        </div>
        <nav id="primary-navigation" className="sidebar-nav" aria-label={t("Navigation principale")}>
          {links.map(([key, label, icon]) => (
            <SidebarLink
              key={key}
              href={`#${key}`}
              label={label}
              icon={icon}
              current={section === key}
              collapsed={collapsed}
              onNavigate={() => {
                if (location.hash === `#${key}`) setDrawerOpen(false);
              }}
            />
          ))}
        </nav>
        <div className="sidebar-bottom">
          <AccountMenu
            user={user}
            collapsed={collapsed}
            current={route === "account"}
            locale={locale}
            setLocale={setLocale}
            theme={theme}
            setTheme={setTheme}
            logout={logout}
          />
        </div>
      </aside>
      <div className="app-main">
        {errorBanner}
        <div id="main-content" tabIndex={-1}>
          {route === "account" ? (
            <Account user={user} run={run} onUserChange={setUser} onLogout={() => setUser(null)} />
          ) : route === "settings" && user.admin ? (
            <Settings run={run} />
          ) : route === "queue" ? (
            <QueuePage run={run} user={user} />
          ) : route === "statistics" && user.admin ? (
            <Statistics run={run} />
          ) : route === "glossaries" ? (
            <SharedGlossaries run={run} />
          ) : route.startsWith("series/") ? (
            <SeriesPage key={route} id={route.split("/")[1]} user={user} run={run} />
          ) : route.startsWith("project/") ? (
            <Workspace
              key={route}
              id={route.split("/")[1]}
              passage={route.split("/")[2] === "passage" ? route.split("/")[3] : undefined}
              user={user}
              run={run}
            />
          ) : (
            <Library run={run} user={user} />
          )}
        </div>
      </div>
    </div>
  );
}

function SidebarLink({
  href,
  label,
  icon,
  current,
  collapsed,
  onNavigate,
}: {
  href: string;
  label: string;
  icon: IconName;
  current: boolean;
  collapsed: boolean;
  onNavigate: () => void;
}) {
  const link = (
    <a
      className="sidebar-link"
      href={href}
      aria-current={current ? "page" : undefined}
      onClick={onNavigate}
    >
      <Icon name={icon} size={18} />
      <span className="sidebar-text">{label}</span>
    </a>
  );
  return collapsed ? (
    <Tooltip content={label} placement="right">
      {link}
    </Tooltip>
  ) : (
    link
  );
}

function AccountMenu({
  user,
  collapsed,
  current,
  locale,
  setLocale,
  theme,
  setTheme,
  logout,
}: {
  user: User;
  collapsed: boolean;
  current: boolean;
  locale: Locale;
  setLocale: (locale: Locale) => void;
  theme: ThemePreference;
  setTheme: (theme: ThemePreference) => void;
  logout: () => void;
}) {
  const { t } = useI18n();
  return (
    <Menu
      label={t("Menu du compte")}
      align="start"
      placement="up"
      className="account-menu"
      trigger={(props) => (
        <button
          {...props}
          type="button"
          className={cx("account-trigger", current && "is-current")}
          aria-label={`${t("Menu du compte")} · ${user.username}`}
        >
          <span className="avatar" aria-hidden="true">
            {user.username.slice(0, 1).toUpperCase()}
          </span>
          <span className="sidebar-text account-name">
            <span>{user.username}</span>
            <small>{user.admin ? t("Administrateur") : t("Utilisateur")}</small>
          </span>
          {!collapsed && <Icon name="chevronDown" className="sidebar-text" />}
        </button>
      )}
      items={[
        { label: t("Mon compte"), icon: "user", href: "#account" },
        { kind: "separator" },
        { kind: "label", label: t("app.language") },
        ...(["fr", "en"] as Locale[]).map((value) => ({
          kind: "radio" as const,
          label: t(value === "fr" ? "language.french" : "language.english"),
          checked: locale === value,
          onSelect: () => setLocale(value),
        })),
        { kind: "separator" },
        { kind: "label", label: t("Thème") },
        ...(
          [
            ["light", t("app.light"), "sun"],
            ["dark", t("app.dark"), "moon"],
            ["system", t("Système"), "monitor"],
          ] as [ThemePreference, string, IconName][]
        ).map(([value, label, icon]) => ({
          kind: "radio" as const,
          label,
          icon,
          checked: theme === value,
          onSelect: () => setTheme(value),
        })),
        { kind: "separator" },
        { label: t("app.logout"), icon: "logout", onSelect: logout },
      ]}
    />
  );
}
