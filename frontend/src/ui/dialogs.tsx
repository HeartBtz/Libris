import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { registerTranslations, useI18n } from "../i18n";
import { Button } from "./Button";
import { Dialog } from "./Dialog";
import { Field, TextArea } from "./Field";

registerTranslations({
  Fermer: "Close",
  Annuler: "Cancel",
  Confirmer: "Confirm",
  Enregistrer: "Save",
});

interface ConfirmOptions {
  title: string;
  message?: ReactNode;
  /** Extra content under the message, such as a cost estimate loaded on demand. */
  details?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "primary" | "danger";
}

interface PromptOptions {
  title: string;
  message?: ReactNode;
  label: string;
  initialValue?: string;
  placeholder?: string;
  confirmLabel?: string;
  required?: boolean;
  rows?: number;
}

type Pending =
  | ({ kind: "confirm"; resolve: (value: boolean) => void } & ConfirmOptions)
  | ({ kind: "prompt"; resolve: (value: string | null) => void } & PromptOptions);

const DialogsContext = createContext<{
  confirm: (options: ConfirmOptions) => Promise<boolean>;
  prompt: (options: PromptOptions) => Promise<string | null>;
} | null>(null);

/** Promise-based replacements for window.confirm / window.prompt, rendered as accessible dialogs. */
export function DialogProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const [value, setValue] = useState("");
  const pendingRef = useRef<Pending | null>(null);
  pendingRef.current = pending;
  const confirm = useCallback(
    (options: ConfirmOptions) =>
      new Promise<boolean>((resolve) => setPending({ kind: "confirm", resolve, ...options })),
    [],
  );
  const prompt = useCallback(
    (options: PromptOptions) =>
      new Promise<string | null>((resolve) => {
        setValue(options.initialValue || "");
        setPending({ kind: "prompt", resolve, ...options });
      }),
    [],
  );
  const api = useMemo(() => ({ confirm, prompt }), [confirm, prompt]);
  const close = () => {
    const current = pendingRef.current;
    if (!current) return;
    if (current.kind === "confirm") current.resolve(false);
    else current.resolve(null);
    setPending(null);
  };
  const accept = () => {
    const current = pendingRef.current;
    if (!current) return;
    if (current.kind === "confirm") current.resolve(true);
    else current.resolve(value);
    setPending(null);
  };
  return (
    <DialogsContext.Provider value={api}>
      {children}
      {pending && <PendingDialog pending={pending} value={value} setValue={setValue} close={close} accept={accept} />}
    </DialogsContext.Provider>
  );
}

function PendingDialog({
  pending,
  value,
  setValue,
  close,
  accept,
}: {
  pending: Pending;
  value: string;
  setValue: (value: string) => void;
  close: () => void;
  accept: () => void;
}) {
  const { t } = useI18n();
  const danger = pending.kind === "confirm" && pending.tone === "danger";
  return (
    <Dialog
      open
      onClose={close}
      title={pending.title}
      size="sm"
      className={danger ? "dialog-danger" : undefined}
    >
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          accept();
        }}
      >
        {pending.message && <div className="dialog-message">{pending.message}</div>}
        {pending.kind === "confirm" && pending.details}
        {pending.kind === "prompt" && (
          <Field label={pending.label}>
            <TextArea
              data-autofocus
              rows={pending.rows || 4}
              value={value}
              placeholder={pending.placeholder}
              required={pending.required}
              onChange={(event) => setValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                  event.preventDefault();
                  accept();
                }
              }}
            />
          </Field>
        )}
        <div className="dialog-actions">
          <Button variant="ghost" onClick={close} data-autofocus={danger ? true : undefined}>
            {(pending.kind === "confirm" && pending.cancelLabel) || t("Annuler")}
          </Button>
          <Button
            type="submit"
            variant={danger ? "danger" : "primary"}
            data-autofocus={pending.kind === "confirm" && !danger ? true : undefined}
          >
            {pending.confirmLabel || t("Confirmer")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

export function useDialogs() {
  const context = useContext(DialogsContext);
  if (!context) throw new Error("useDialogs must be used inside DialogProvider");
  return context;
}
