import { formatDateTime, formatNumber, getLocale, message } from "./i18n";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    /** The answer's `detail`, for callers that act on a structured refusal (conflicts to confirm…). */
    public detail: unknown = null,
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
      "Accept-Language": getLocale(),
      ...(options.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...options.headers,
    },
  });
  if (!response.ok) throw await responseError(response);
  return (await response.json()) as T;
}
/** One readable line per failure; never echoes submitted values back to the screen. */
export function errorMessage(status: number, body: unknown): string {
  const detail =
    body && typeof body === "object" && "detail" in body
      ? (body as { detail: unknown }).detail
      : body;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const lines = detail.map((item: { loc?: unknown[]; msg?: unknown }) => {
      const field = Array.isArray(item?.loc) ? item.loc.at(-1) : undefined;
      const text = typeof item?.msg === "string" ? item.msg : "";
      return typeof field === "string" && text ? `${field} : ${text}` : text;
    });
    if (lines.some(Boolean)) return lines.filter(Boolean).join(" · ");
  }
  if (detail && typeof detail === "object") {
    const { message: text, errors } = detail as {
      message?: unknown;
      errors?: unknown;
    };
    if (typeof text === "string" && text) {
      const list = Array.isArray(errors)
        ? errors.filter((item): item is string => typeof item === "string")
        : [];
      return [text, ...list].join("\n");
    }
  }
  return `${message("app.error")} (HTTP ${status})`;
}
export async function responseError(response: Response): Promise<ApiError> {
  // A reverse proxy answers HTML on 502/504/413: the body is not always JSON.
  const body: unknown = await response.json().catch(() => null);
  const detail = body && typeof body === "object" && "detail" in body ? (body as { detail: unknown }).detail : null;
  return new ApiError(response.status, errorMessage(response.status, body), detail);
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
export async function downloadApi(path: string, name: string, body: unknown) {
  const response = await fetch(`/api${path}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "Accept-Language": getLocale() },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw await responseError(response);
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function date(timestamp: number) {
  return formatDateTime(timestamp);
}
export function number(value: number) {
  return formatNumber(value);
}
/** Downloads a GET endpoint as a file, keeping API errors readable instead of opening an error page. */
export async function downloadGet(path: string, name: string) {
  const response = await fetch(`/api${path}`, {
    credentials: "same-origin",
    headers: { "Accept-Language": getLocale() },
  });
  if (!response.ok) throw await responseError(response);
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}
export const labels: Record<string, string> = new Proxy({}, {
  get: (_target, status: string) => {
    const key = `status.${status}`;
    const text = message(key);
    // An unknown status shows as itself, never as the raw "status.xxx" lookup key.
    return text === key ? status : text;
  },
});
