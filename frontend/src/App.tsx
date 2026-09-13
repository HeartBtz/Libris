import { useCallback, useEffect, useState } from "react";
import { api, date, labels, number, send } from "./api";
import type { Project, Run, User } from "./types";
import { Settings } from "./features/Settings";
import { Workspace } from "./features/Workspace";
import { BatchActions } from "./features/BatchActions";
import { BookProgress } from "./features/BookProgress";

export function App() {
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
    document.title = `${route === "settings" ? "Paramètres" : route === "library" ? "Bibliothèque" : "Projet"} · Libris`;
  }, [route]);
  if (checking)
    return (
      <main className="loading">
        <img src="/assets/libris-icon.png" alt="" />
        Ouverture de Libris…
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
                Bibliothèque
              </a>
              {user.admin && (
                <a
                  href="#settings"
                  aria-current={route === "settings" ? "page" : undefined}
                >
                  Paramètres
                </a>
              )}
              <span className="muted">{user.username}</span>
            </>
          )}
          <button
            className="quiet"
            aria-label="Changer de thème"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          >
            {theme === "dark" ? "Clair" : "Sombre"}
          </button>
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
              Déconnexion
            </button>
          )}
        </nav>
      </header>
      {error && (
        <div className="error-banner" role="alert">
          <strong>L’opération n’a pas abouti.</strong> {error}
          <button onClick={() => setError("")} aria-label="Fermer l’erreur">
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
        <p className="eyebrow">Traduction littéraire · Mémoire contextuelle</p>
        <h1>Retrouvez vos livres</h1>
        <p className="muted">
          Traduisez avec continuité, de la première page au dernier volume.
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
            Utilisateur
            <input
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
          </label>
          <label>
            Mot de passe
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          <button className="primary" disabled={busy}>
            {busy ? "Connexion…" : "Se connecter"}
          </button>
        </form>
        <small className="muted">
          Premier accès : identifiants définis dans le fichier .env de
          l’installation.
        </small>
      </div>
    </main>
  );
}

function Library({ run, user }: { run: Run; user: User }) {
  const [books, setBooks] = useState<Project[]>([]);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [imports, setImports] = useState<{ name: string; state: string }[]>([]);
  const load = useCallback(
    async () => setBooks(await api<Project[]>("/projects")),
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
          <p className="eyebrow">Libris · Vos projets</p>
          <h1>Bibliothèque</h1>
          <p className="page-lede">
            Une continuité de traduction pensée à l’échelle de la collection.
          </p>
        </div>
        <div className="actions">
          <label className="button">
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
          <label className="button primary">
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
                    aria-label="Sélectionner tous les livres"
                    checked={!!books.length && selected.size === books.length}
                    onChange={(e) =>
                      setSelected(
                        e.target.checked
                          ? new Set(books.map((p) => p.id))
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
              {books.map((p) => (
                <tr key={p.id}>
                  <td className="select-cell">
                    <input
                      type="checkbox"
                      aria-label={`Sélectionner ${p.title}`}
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
                  </td>
                  <td data-label="Langues">
                    {p.source_language} → {p.target_language}
                  </td>
                  <td data-label="Avancement">
                    <BookProgress project={p} />
                  </td>
                  <td data-label="Statut">
                    <span className={`badge ${p.status}`}>
                      {labels[p.status] || p.status}
                    </span>
                  </td>
                  <td className="muted" data-label="Modifié">
                    {date(p.updated_at)}
                  </td>
                  <td className="book-actions">
                    <a href={`#project/${p.id}`}>Ouvrir →</a>
                    {p.owner_id === user.id && (
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
        {books.length} projet{books.length > 1 ? "s" : ""} ·{" "}
        {number(books.reduce((n, p) => n + p.stats.translated, 0))} passages
        traduits
      </footer>
    </main>
  );
}
