import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { ScreenBoundary } from "./ScreenBoundary";
import { I18nProvider } from "./i18n";
import "./style.css";
import "./editorial.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <I18nProvider>
      <ScreenBoundary>
        <App />
      </ScreenBoundary>
    </I18nProvider>
  </React.StrictMode>,
);
