import { Component } from "react";
import type { ReactNode } from "react";
import { registerTranslations, useI18n } from "./i18n";
import { Button, EmptyState } from "./ui";

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
      <main className="app-loading" role="alert">
        <ScreenBoundaryError error={this.state.error} />
      </main>
    );
  }
}

function ScreenBoundaryError({ error }: { error: Error }) {
  const { t } = useI18n();
  return (
    <EmptyState
      icon="alert"
      title={t("L’interface doit être rechargée")}
      description={
        <>
          {t(
            "Un écran n’a pas pu se charger, notamment après une mise à jour. Les résultats enregistrés côté serveur sont conservés.",
          )}
          <br />
          <span className="subtle">{error.message}</span>
        </>
      }
      action={
        <Button variant="primary" icon="refresh" onClick={() => location.reload()}>
          {t("Recharger l’interface")}
        </Button>
      }
    />
  );
}
