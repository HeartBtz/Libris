function required(value: string | undefined, name: string) {
  if (!value) throw new Error(`${name} is required for integration tests.`);
  return value;
}

const configuredBase = required(process.env.LIBRIS_E2E_URL, "LIBRIS_E2E_URL");
const url = new URL(configuredBase);
if (
  !["127.0.0.1", "localhost", "::1"].includes(url.hostname) ||
  process.env.LIBRIS_E2E_CONFIRM_DISPOSABLE !== "1"
) {
  throw new Error(
    "Integration tests require a loopback LIBRIS_E2E_URL and LIBRIS_E2E_CONFIRM_DISPOSABLE=1.",
  );
}

export const base = url.origin;
export const username = required(
  process.env.LIBRIS_E2E_USERNAME,
  "LIBRIS_E2E_USERNAME",
);
export const password = required(
  process.env.LIBRIS_E2E_PASSWORD,
  "LIBRIS_E2E_PASSWORD",
);
