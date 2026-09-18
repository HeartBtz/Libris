import { createContext, useCallback, useContext, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useI18n } from "../i18n";
import { IconButton, cx } from "./Button";
import type { Tone } from "./Badge";
import { Icon } from "./icons";

interface Toast {
  id: number;
  tone: Tone;
  message: ReactNode;
}

const ToastContext = createContext<(message: ReactNode, tone?: Tone) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const [toasts, setToasts] = useState<Toast[]>([]);
  const counter = useRef(0);
  const dismiss = useCallback((id: number) => setToasts((all) => all.filter((item) => item.id !== id)), []);
  const show = useCallback(
    (message: ReactNode, tone: Tone = "success") => {
      const id = ++counter.current;
      setToasts((all) => [...all.slice(-2), { id, tone, message }]);
      setTimeout(() => dismiss(id), tone === "danger" ? 9000 : 4500);
    },
    [dismiss],
  );
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className="toast-region" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} className={cx("toast", `tone-${toast.tone}`)}>
            <Icon name={toast.tone === "danger" || toast.tone === "warning" ? "alert" : "check"} />
            <span className="toast-message">{toast.message}</span>
            <IconButton icon="x" size="sm" label={t("Fermer")} tooltip={false} onClick={() => dismiss(toast.id)} />
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
