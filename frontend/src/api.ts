export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    credentials: "same-origin",
    headers: {
      ...(options.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...options.headers,
    },
  });
  const data: unknown = await response.json();
  if (!response.ok) {
    const detail = (data as { detail?: unknown }).detail;
    throw new ApiError(
      response.status,
      typeof detail === "string" ? detail : JSON.stringify(detail ?? data),
    );
  }
  return data as T;
}
export function send<T>(
  path: string,
  body: unknown = {},
  method = "POST",
): Promise<T> {
  return api<T>(path, { method, body: JSON.stringify(body) });
}
export function download(name: string, value: unknown) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function date(timestamp: number) {
  return new Date(timestamp * 1000).toLocaleString("fr-FR");
}
export function number(value: number) {
  return value.toLocaleString("fr-FR", { maximumFractionDigits: 1 });
}
export const labels: Record<string, string> = {
  waiting: "Service indisponible · reprise prévue",
  blocked: "Intervention requise",
  refused: "Refus du provider",
  source_retained: "Original conservé",
  interrupted: "Interrompue",
  abandoned: "Interrompue",
  pending: "En attente",
  ready: "Prêt",
  analyzing: "Analyse",
  translating: "Traduction",
  reviewing: "Relecture",
  completed: "Terminé",
  paused: "En pause",
  cancelled: "Annulé",
  failed: "Échec",
  error: "Erreur",
  ok: "Contrôles OK",
  check: "À vérifier",
  success: "Réussie",
  running: "En cours",
  syncing: "Synchronisation",
};
