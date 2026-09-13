import { useCallback, useEffect, useState } from "react";
import { api, date, labels, number, send } from "./api";
import type { Project, Run, User } from "./types";
import { Settings } from "./features/Settings";
import { Workspace } from "./features/Workspace";
import { BatchActions } from "./features/BatchActions";
import { BookProgress } from "./features/BookProgress";
import { locales, useI18n } from "./i18n";

export function App() {
  const { locale, setLocale, t } = useI18n();
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  const [route, setRoute] = useState(location.hash.slice(1) || "library");
  const [error, setError] = useState("");
  const [theme, setTheme] = useState(localStorage.getItem("theme") || "dark");
  const run: Run = useCallback(async (task) => {
    setError("");
    try {
      await task();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);
  useEffect(() => {
    api<User>("/auth/me")
      .then(setUser)
      .catch(() => {})
      .finally(() => setChecking(false));
  }, []);
  useEffect(() => {
    const change = () => setRoute(location.hash.slice(1) || "library");
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("theme", theme);
  }, [theme]);
  useEffect(() => {
    document.title = `${route === "settings" ? t("app.settings") : route === "library" ? t("app.library") : t("app.project")} · Libris`;
  }, [route, t]);
  if (checking)
    return (
      <main className="loading">
        <img src="/assets/libris-icon.png" alt="" />
        {t("app.loading")}
      </main>
    );
  return (
    <>
      <header className="app-header">
        <a className="brand" href="#library">
          <img src="/assets/libris-icon.png" alt="" />
          <span>Libris</span>
        </a>
        <nav>
          {user && (
            <>
              <a
                href="#library"
                aria-current={route === "library" ? "page" : undefined}
              >
                {t("app.library")}
              </a>
              {user.admin && (
                <a
                  href="#settings"
                  aria-current={route === "settings" ? "page" : undefined}
                >
                  {t("app.settings")}
                </a>
              )}
              <span className="muted">{user.username}</span>
            </>
          )}
          <button
            className="quiet"
            aria-label={t("app.theme")}
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          >
            {theme === "dark" ? t("app.light") : t("app.dark")}
          </button>
          <label className="locale-select">
            <span className="sr-only">{t("app.language")}</span>
            <select value={locale} onChange={(event) => setLocale(event.target.value as typeof locale)}>
              {locales.map((value) => (
                <option key={value} value={value}>
                  {t(value === "fr" ? "language.french" : "language.english")}
                </option>
              ))}
            </select>
          </label>
          {user && (
            <button
              className="quiet"
              onClick={() =>
                void run(async () => {
                  await send("/auth/logout");
                  setUser(null);
                })
              }
            >
              {t("app.logout")}
            </button>
          )}
        </nav>
      </header>
      {error && (
        <div className="error-banner" role="alert">
          <strong>{t("app.error")}</strong> {error}
          <button onClick={() => setError("")} aria-label={t("app.closeError")}>
            ×
          </button>
        </div>
      )}
      {!user ? (
        <Login run={run} onLogin={setUser} />
      ) : route === "settings" && user.admin ? (
        <Settings run={run} />
      ) : route.startsWith("project/") ? (
        <Workspace key={route} id={route.split("/")[1]} user={user} run={run} />
      ) : (
        <Library run={run} user={user} />
      )}
    </>
  );
}

function Login({ run, onLogin }: { run: Run; onLogin: (user: User) => void }) {
  const { t } = useI18n();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <main className="login">
      <div className="login-logo-panel">
        <img
          className="login-logo"
          src="/assets/libris-logo.png"
          alt="Libris"
        />
      </div>
      <div className="login-card">
        <p className="eyebrow">{t("login.eyebrow")}</p>
        <h1>{t("login.title")}</h1>
        <p className="muted">
          {t("login.description")}
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setBusy(true);
            void run(async () => {
              onLogin(await send<User>("/auth/login", { username, password }));
            }).finally(() => setBusy(false));
          }}
        >
          <label>
            {t("login.username")}
            <input
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
          </label>
          <label>
            {t("login.password")}
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          <button className="primary" disabled={busy}>
            {busy ? t("login.submitting") : t("login.submit")}
          </button>
        </form>
        <small className="muted">
          {t("login.firstAccess")}
        </small>
      </div>
    </main>
  );
}

function Library({ run, user }: { run: Run; user: User }) {
  const { locale } = useI18n();
  const [books, setBooks] = useState<Project[]>([]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [sort, setSort] = useState("recent");
  const [seriesFilter, setSeriesFilter] = useState("all");
  const active = (p: Project) =>
    ["pending", "analyzing", "translating", "reviewing", "syncing"].includes(
      p.status,
    );
  const attention = (p: Project) =>
    !!(p.stats.flagged || p.stats.errors || p.stats.refused) ||
    ["failed", "blocked", "waiting"].includes(p.status);
  const normalize = (value: string) =>
    value
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLocaleLowerCase(locale);
  const availableBooks = books.filter((project) => !project.archived_at);
  const archivedBooks = books.filter((project) => !!project.archived_at);
  const seriesNames = Array.from(
    new Set(books.map((project) => project.series_name).filter(Boolean)),
  ).sort((a, b) => a.localeCompare(b, locale, { numeric: true }));
  const visibleBooks = books
    .filter(
      (p) =>
        normalize(`${p.title} ${p.author} ${p.series_name}`).includes(
          normalize(query),
        ) &&
        (seriesFilter === "all" || p.series_name === seriesFilter) &&
        ((filter === "archived" && !!p.archived_at) ||
          (!p.archived_at &&
            (filter === "all" ||
              (filter === "active" && active(p)) ||
              (filter === "attention" && attention(p)) ||
              (filter === "complete" &&
                p.stats.total > 0 &&
                p.stats.translated === p.stats.total)))),
    )
    .sort((a, b) =>
      sort === "title"
        ? a.title.localeCompare(b.title, locale, { numeric: true })
        : sort === "series"
          ? (a.series_name || a.title).localeCompare(
              b.series_name || b.title,
               locale,
              { numeric: true },
            ) ||
            (a.volume_number ?? Number.MAX_SAFE_INTEGER) -
              (b.volume_number ?? Number.MAX_SAFE_INTEGER) ||
             a.title.localeCompare(b.title, locale, { numeric: true })
          : b.updated_at - a.updated_at,
    );
  const selectableBooks = visibleBooks.filter(
    (project) => !project.archived_at,
  );
  const seriesBooks =
    seriesFilter === "all"
      ? []
      : availableBooks
          .filter((project) => project.series_name === seriesFilter)
          .sort(
            (a, b) =>
              (a.volume_number ?? Number.MAX_SAFE_INTEGER) -
                (b.volume_number ?? Number.MAX_SAFE_INTEGER) ||
               a.title.localeCompare(b.title, locale, { numeric: true }),
          );
  const numberedVolumes = seriesBooks
    .map((project) => project.volume_number)
    .filter((value): value is number => value !== null);
  const duplicateVolumes = Array.from(
    new Set(
      numberedVolumes.filter(
        (volume, index) => numberedVolumes.indexOf(volume) !== index,
      ),
    ),
  );
  const missingVolumes = numberedVolumes.length
      ? Array.from(
        {
          length: Math.max(...numberedVolumes),
        },
        (_, index) => index + 1,
      ).filter((volume) => !numberedVolumes.includes(volume))
    : [];
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [imports, setImports] = useState<{ name: string; state: string }[]>([]);
  const load = useCallback(
    async () =>
      setBooks(await api<Project[]>("/projects?include_archived=true")),
    [],
  );
  useEffect(() => {
    void run(load);
    const timer = setInterval(() => {
      void run(load);
    }, 5000);
    return () => clearInterval(timer);
  }, [run, load]);
  async function upload(files: File[], restore = false) {
    setBusy(true);
    setImports(files.map((f) => ({ name: f.name, state: "En attente" })));
    let cursor = 0;
    const created: string[] = [];
    const workers = Array.from(
      { length: Math.min(2, files.length) },
      async () => {
        while (cursor < files.length) {
          const index = cursor++;
          setImports((values) =>
            values.map((v, i) =>
              i === index ? { ...v, state: "Import / validation…" } : v,
            ),
          );
          const form = new FormData();
          form.append("file", files[index]);
          try {
            const p = await api<Project>(
              restore ? "/projects/import" : "/projects",
              { method: "POST", body: form },
            );
            created.push(p.id);
            setImports((values) =>
              values.map((v, i) =>
                i === index ? { ...v, state: "Importé" } : v,
              ),
            );
          } catch (e) {
            setImports((values) =>
              values.map((v, i) =>
                i === index
                  ? { ...v, state: e instanceof Error ? e.message : String(e) }
                  : v,
              ),
            );
          }
        }
      },
    );
    await Promise.all(workers);
    await run(load);
    setSelected(new Set(created));
    setBusy(false);
    if (files.length === 1 && created.length === 1)
      location.hash = `project/${created[0]}`;
  }
  return (
    <main className="library">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Votre atelier de traduction</p>
          <h1>Bibliothèque</h1>
          <p className="page-lede">
            Retrouvez vos livres et reprenez là où vous en étiez.
          </p>
        </div>
        <div className="actions">
          <label
            className="button"
            role="button"
            tabIndex={busy ? -1 : 0}
            aria-disabled={busy}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                if (!busy) e.currentTarget.querySelector("input")?.click();
              }
            }}
          >
            Réimporter un projet
            <input
              type="file"
              accept=".zip"
              hidden
              disabled={busy}
              onChange={(e) => {
                if (e.target.files?.[0]) void upload([e.target.files[0]], true);
              }}
            />
          </label>
          <label
            className="button primary"
            role="button"
            tabIndex={busy ? -1 : 0}
            aria-disabled={busy}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                if (!busy) e.currentTarget.querySelector("input")?.click();
              }
            }}
          >
            {busy ? "Import en cours…" : "+ Importer des EPUB"}
            <input
              type="file"
              accept=".epub"
              multiple
              hidden
              disabled={busy}
              onChange={(e) => {
                if (e.target.files?.length)
                  void upload(Array.from(e.target.files));
              }}
            />
          </label>
        </div>
      </div>
      <div
        className="library-overview"
        aria-label="Vue d’ensemble de la bibliothèque"
      >
        {[
          ["all", "Tous les livres", availableBooks.length],
          ["active", "En cours", availableBooks.filter(active).length],
          ["attention", "À examiner", availableBooks.filter(attention).length],
          [
            "complete",
            "Traduction complète",
            availableBooks.filter(
              (p) => p.stats.total > 0 && p.stats.translated === p.stats.total,
            ).length,
          ],
          ["archived", "Archives", archivedBooks.length],
        ].map(([key, title, count]) => (
          <button
            key={key}
            aria-pressed={filter === key}
            onClick={() => setFilter(String(key))}
          >
            <strong>{count}</strong>
            <span>{title}</span>
          </button>
        ))}
      </div>
      <div className="library-toolbar">
        <label>
          Rechercher un livre
          <input
            type="search"
            placeholder="Titre, auteur ou série…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <label>
          Trier par
          <select value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="recent">Dernière activité</option>
            <option value="title">Titre</option>
            <option value="series">Série et volume</option>
          </select>
        </label>
        <label>
          Série
          <select
            value={seriesFilter}
            onChange={(e) => setSeriesFilter(e.target.value)}
          >
            <option value="all">Toutes les séries</option>
            {seriesNames.map((series) => (
              <option key={series} value={series}>
                {series}
              </option>
            ))}
          </select>
        </label>
        <span role="status">{visibleBooks.length} livre(s) affiché(s)</span>
      </div>
      {!!seriesBooks.length && (
        <section
          className="series-workspace"
          aria-label={`Série ${seriesFilter}`}
        >
          <div>
            <p className="eyebrow">Collection active</p>
            <h2>{seriesFilter}</h2>
            <p className="muted">
              {seriesBooks.length} volume(s), classés dans l’ordre de lecture.
              Les conventions acceptées et décisions humaines des volumes
              antérieurs alimentent les volumes suivants, sans importer leur
              narration.
            </p>
          </div>
          <ol className="series-volumes">
            {seriesBooks.map((project) => (
              <li key={project.id}>
                <strong>{project.volume_number ?? "?"}</strong>
                <span>{project.title}</span>
              </li>
            ))}
          </ol>
          {(missingVolumes.length > 0 || duplicateVolumes.length > 0) && (
            <p className="series-warning" role="status">
              {missingVolumes.length > 0 &&
                `Volumes manquants dans cette bibliothèque : ${missingVolumes.join(", ")}. `}
              {duplicateVolumes.length > 0 &&
                `Numéros dupliqués : ${duplicateVolumes.join(", ")}.`}
            </p>
          )}
          <button
            onClick={() =>
              setSelected(new Set(seriesBooks.map((project) => project.id)))
            }
          >
            Sélectionner toute la série
          </button>
        </section>
      )}
      {!!imports.length && (
        <ul className="muted" role="status">
          {imports.map((item, i) => (
            <li key={i}>
              {item.name} : {item.state}
            </li>
          ))}
        </ul>
      )}
      {selected.size > 0 && (
        <BatchActions
          books={books.filter((p) => selected.has(p.id))}
          run={run}
          refresh={load}
          onDeleted={(ids) =>
            setSelected((previous) => {
              const next = new Set(previous);
              ids.forEach((id) => next.delete(id));
              return next;
            })
          }
          scopeLabel={
            seriesFilter !== "all" ? `Série ${seriesFilter}` : undefined
          }
        />
      )}
      {books.length ? (
        <div className="table-wrap library-table-wrap">
          <table className="library-table">
            <thead>
              <tr>
                <th>
                  <input
                    type="checkbox"
                    aria-label={
                      query || filter !== "all"
                        ? "Sélectionner les livres affichés"
                        : "Sélectionner tous les livres actifs"
                    }
                    checked={
                      !!selectableBooks.length &&
                      selectableBooks.every((p) => selected.has(p.id))
                    }
                    onChange={(e) =>
                      setSelected(
                        e.target.checked
                          ? new Set(selectableBooks.map((p) => p.id))
                          : new Set(),
                      )
                    }
                  />
                </th>
                <th>Livre / auteur</th>
                <th>Langues</th>
                <th>Avancement</th>
                <th>Statut</th>
                <th>Modifié</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {visibleBooks.map((p) => (
                <tr
                  key={p.id}
                  className={p.archived_at ? "archived-row" : undefined}
                >
                  <td className="select-cell">
                    <input
                      type="checkbox"
                      aria-label={`Sélectionner ${p.title}`}
                      disabled={!!p.archived_at}
                      checked={selected.has(p.id)}
                      onChange={(e) =>
                        setSelected((previous) => {
                          const next = new Set(previous);
                          if (e.target.checked) next.add(p.id);
                          else next.delete(p.id);
                          return next;
                        })
                      }
                    />
                  </td>
                  <td data-label="Livre">
                    <a className="book-title" href={`#project/${p.id}`}>
                      {p.title}
                    </a>
                    <div className="muted">
                      {p.author || "Auteur non renseigné"}
                    </div>
                    {p.series_name && (
                      <div className="series-meta">
                        {p.series_name}
                        {p.volume_number && ` · volume ${p.volume_number}`}
                      </div>
                    )}
                  </td>
                  <td data-label="Langues">
                    {p.source_language} → {p.target_language}
                  </td>
                  <td data-label="Avancement">
                    <BookProgress project={p} />
                  </td>
                  <td data-label="Statut">
                    <span
                      className={`badge ${p.archived_at ? "archived" : p.status}`}
                    >
                      {p.archived_at ? "Archivé" : labels[p.status] || p.status}
                    </span>
                  </td>
                  <td className="muted" data-label="Modifié">
                    {date(p.updated_at)}
                  </td>
                  <td className="book-actions">
                    <a href={`#project/${p.id}`}>Ouvrir →</a>
                    {p.owner_id === user.id && (
                      <button
                        className="quiet"
                        onClick={() =>
                          void run(async () => {
                            await send(
                              `/projects/${p.id}/${p.archived_at ? "restore" : "archive"}`,
                            );
                            setSelected((previous) => {
                              const next = new Set(previous);
                              next.delete(p.id);
                              return next;
                            });
                            await load();
                          })
                        }
                      >
                        {p.archived_at ? "Restaurer" : "Archiver"}
                      </button>
                    )}
                    {p.owner_id === user.id && p.archived_at && (
                      <button
                        className="quiet danger"
                        aria-label={`Supprimer ${p.title}`}
                        onClick={() => {
                          if (
                            confirm(
                              `Supprimer définitivement le projet local « ${p.title} » et arrêter ses travaux ? La mémoire OpenViking distante reste séparée.`,
                            )
                          )
                            void run(async () => {
                              await api(`/projects/${p.id}?stop_jobs=true`, {
                                method: "DELETE",
                              });
                              setSelected((previous) => {
                                const next = new Set(previous);
                                next.delete(p.id);
                                return next;
                              });
                              await load();
                            });
                        }}
                      >
                        Supprimer
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!visibleBooks.length && (
            <div className="empty">
              <h2>Aucun livre ne correspond.</h2>
              <p>Essayez un autre titre ou affichez tous vos livres.</p>
              <button
                onClick={() => {
                  setQuery("");
                  setFilter("all");
                  setSeriesFilter("all");
                }}
              >
                Effacer les filtres
              </button>
            </div>
          )}
        </div>
      ) : (
        <div className="empty">
          <h2>Votre premier livre commence ici.</h2>
          <p>
            Importez un EPUB pour examiner sa structure, préparer sa mémoire et
            traduire avec continuité.
          </p>
          <p className="muted">
            EPUB 2 & 3 · Images et balises préservées · Endpoint
            OpenAI-compatible
          </p>
        </div>
      )}
      <footer className="library-footer">
        {availableBooks.length} projet{availableBooks.length > 1 ? "s" : ""}
        {archivedBooks.length
          ? ` · ${archivedBooks.length} archivé(s)`
          : ""} ·{" "}
        {number(availableBooks.reduce((n, p) => n + p.stats.translated, 0))}{" "}
        passages traduits
      </footer>
    </main>
  );
}
