import { useCallback, useEffect } from "react";
import { registerTranslations, useI18n } from "./i18n";
import { useDialogs } from "./ui";

registerTranslations({
  "Modifications non enregistrées": "Unsaved changes",
  "Une traduction modifiée n’a pas été enregistrée. La quitter abandonnera ces modifications.":
    "A translation you edited has not been saved. Leaving will discard these changes.",
  "Quitter sans enregistrer": "Leave without saving",
  "Rester": "Stay",
});

// Editors register their unsaved drafts here so any navigation can ask before discarding them.
const drafts = new Set<string>();

export function hasUnsavedChanges() {
  return drafts.size > 0;
}

export function discardUnsavedChanges() {
  drafts.clear();
}

export function useUnsavedDraft(id: string, dirty: boolean) {
  useEffect(() => {
    if (dirty) drafts.add(id);
    else drafts.delete(id);
    return () => {
      drafts.delete(id);
    };
  }, [id, dirty]);
}

/** Resolves true when nothing is pending or the user accepts to discard the drafts. */
export function useLeaveGuard() {
  const { confirm } = useDialogs();
  const { t } = useI18n();
  return useCallback(async () => {
    if (!hasUnsavedChanges()) return true;
    const leave = await confirm({
      title: t("Modifications non enregistrées"),
      message: t(
        "Une traduction modifiée n’a pas été enregistrée. La quitter abandonnera ces modifications.",
      ),
      confirmLabel: t("Quitter sans enregistrer"),
      cancelLabel: t("Rester"),
      tone: "danger",
    });
    if (leave) discardUnsavedChanges();
    return leave;
  }, [confirm, t]);
}
