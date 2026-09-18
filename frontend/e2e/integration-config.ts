// Specs tagged @integration or @journey need a disposable backend. Without LIBRIS_E2E_URL,
// playwright.config.ts filters them out, so this module must stay importable.
function required(value: string | undefined, name: string) {
  if (!value) throw new Error(`${name} is required for integration tests.`);
  return value;
}

function disposableOrigin(configured: string) {
  const url = new URL(configured);
  if (
    !["127.0.0.1", "localhost", "::1"].includes(url.hostname) ||
    process.env.LIBRIS_E2E_CONFIRM_DISPOSABLE !== "1"
  ) {
    throw new Error(
      "Integration tests require a loopback LIBRIS_E2E_URL and LIBRIS_E2E_CONFIRM_DISPOSABLE=1.",
    );
  }
  return url.origin;
}

const configuredBase = process.env.LIBRIS_E2E_URL;

export const base = configuredBase ? disposableOrigin(configuredBase) : "";
export const username = configuredBase
  ? required(process.env.LIBRIS_E2E_USERNAME, "LIBRIS_E2E_USERNAME")
  : "";
export const password = configuredBase
  ? required(process.env.LIBRIS_E2E_PASSWORD, "LIBRIS_E2E_PASSWORD")
  : "";
