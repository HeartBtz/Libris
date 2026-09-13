import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { ScreenBoundary } from "./ScreenBoundary";
import "./style.css";
import "./editorial.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ScreenBoundary>
      <App />
    </ScreenBoundary>
  </React.StrictMode>,
);
