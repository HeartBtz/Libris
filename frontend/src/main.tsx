import React from "react";
import ReactDOM from "react-dom/client";
import "@fontsource-variable/inter";
import "./styles/index.css";
import { App } from "./App";
import { ScreenBoundary } from "./ScreenBoundary";
import { I18nProvider } from "./i18n";
import { applyStoredTheme } from "./theme";
import { DialogProvider, ToastProvider } from "./ui";

applyStoredTheme();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <I18nProvider>
      <ScreenBoundary>
        <DialogProvider>
          <ToastProvider>
            <App />
          </ToastProvider>
        </DialogProvider>
      </ScreenBoundary>
    </I18nProvider>
  </React.StrictMode>,
);
