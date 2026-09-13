import { Component } from "react";
import type { ReactNode } from "react";
import { registerTranslations, useI18n } from "./i18n";

const translations: Record<string, string> = {
  "L’interface doit être rechargée": "The interface needs to be reloaded",
  "Un écran n’a pas pu se charger, notamment après une mise à jour. Les résultats enregistrés côté serveur sont conservés.": "A screen could not load, especially after an update. Results saved on the server are preserved.",
  "Recharger l’interface": "Reload the interface",
};

registerTranslations(translations);

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
        <ScreenBoundaryError error={this.state.error} />
      </main>
    );
  }
}

function ScreenBoundaryError({ error }: { error: Error }) {
  const { t } = useI18n();
  return (
    <>
      <h1>{t("L’interface doit être rechargée")}</h1>
      <p>
        {t("Un écran n’a pas pu se charger, notamment après une mise à jour. Les résultats enregistrés côté serveur sont conservés.")}
      </p>
      <p className="muted">{error.message}</p>
      <button className="primary" onClick={() => location.reload()}>
        {t("Recharger l’interface")}
      </button>
    </>
  );
}
