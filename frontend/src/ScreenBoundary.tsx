import { Component } from "react";
import type { ReactNode } from "react";

export class ScreenBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <main className="login" role="alert">
        <h1>L’interface doit être rechargée</h1>
        <p>
          Un écran n’a pas pu se charger, notamment après une mise à jour. Les
          résultats enregistrés côté serveur sont conservés.
        </p>
        <p className="muted">{this.state.error.message}</p>
        <button className="primary" onClick={() => location.reload()}>
          Recharger l’interface
        </button>
      </main>
    );
  }
}
