import { expect, test } from "@playwright/test";

interface Token {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  created_at: number;
  expires_at: number | null;
  revoked_at: number | null;
  last_used_at: number | null;
  state: string;
}

const SECRET = "lbr_ab12cd34_synthetic-secret-shown-once-only-0123456789";
const SIGNING = "synthetic-webhook-signing-secret-0123456789abcdef";

for (const width of [1440, 390]) {
  test(`API tokens are listed, created once and revoked at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.addInitScript(() => localStorage.setItem("locale", "en"));
    const created: unknown[] = [];
    const revoked: string[] = [];
    const tokens: Token[] = [
      {
        id: "t1",
        name: "Nightly import",
        prefix: "lbr_zz99yy88",
        scopes: ["content:write", "pipeline:start"],
        created_at: 1_800_000_000,
        expires_at: null,
        revoked_at: null,
        last_used_at: 1_800_100_000,
        state: "active",
      },
    ];
    await page.route("**/api/**", async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      let body: unknown = [];
      if (path === "/api/auth/me") body = { id: "admin", username: "owner", admin: true, active: true };
      else if (path === "/api/tokens" && request.method() === "POST") {
        const input = request.postDataJSON() as { name: string; scopes: string[]; webhook_secret?: boolean };
        created.push(input);
        const token = {
          id: "t2",
          name: input.name,
          prefix: "lbr_ab12cd34",
          scopes: input.scopes,
          created_at: 1_800_200_000,
          expires_at: 1_807_976_000,
          revoked_at: null,
          last_used_at: null,
          state: "active",
          webhook_secret: !!input.webhook_secret,
        };
        tokens.unshift(token);
        body = { ...token, token: SECRET, ...(input.webhook_secret ? { webhook_secret: SIGNING } : {}) };
      } else if (path === "/api/tokens") body = tokens;
      else if (path.startsWith("/api/tokens/") && request.method() === "DELETE") {
        const id = path.split("/").at(-1) as string;
        revoked.push(id);
        const token = tokens.find((item) => item.id === id) as Token;
        Object.assign(token, { state: "revoked", revoked_at: 1_800_300_000 });
        body = token;
      }
      await route.fulfill({ json: body });
    });
    await page.goto(`${process.env.SHOWCASE_URL || "http://127.0.0.1:4173"}/#settings`);
    await page.getByRole("tab", { name: "Automation API", exact: true }).click();

    const list = page.getByRole("list", { name: "API tokens" });
    const nightly = list.getByRole("listitem").filter({ hasText: "Nightly import" });
    await expect(nightly).toContainText("lbr_zz99yy88");
    await expect(nightly).toContainText("content:write");
    await expect(nightly).toContainText("No expiration");
    await expect(nightly).toContainText("Active");
    await expect(page.getByText("docs/api.md")).toBeVisible();
    await expect(page.getByText("POST /api/v1/translation-requests", { exact: true })).toBeVisible();

    await page.getByLabel("Token name").fill("CI export");
    await page.getByLabel("Send content").check();
    await page.getByLabel("Expiration").selectOption({ label: "90 days" });
    await page.getByRole("button", { name: "Create a token" }).click();
    await expect(page.getByLabel("Token secret", { exact: true })).toHaveValue(SECRET);
    expect(created).toEqual([
      { name: "CI export", scopes: ["jobs:read", "results:read", "content:write"], expires_in_days: 90 },
    ]);
    await page.getByRole("button", { name: "Copy the token secret" }).click();
    await expect(page.getByText("Copied", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(SECRET);
    await page.getByRole("button", { name: "I have copied the token" }).click();
    await expect(page.getByLabel("Token secret", { exact: true })).toHaveCount(0);
    expect(await page.content()).not.toContain(SECRET);
    await expect(list.getByRole("listitem").filter({ hasText: "CI export" })).toContainText("Never used");

    await page.getByRole("button", { name: "Revoke Nightly import" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("Clients using it will receive a 401 error");
    await dialog.getByRole("button", { name: "Revoke", exact: true }).click();
    await expect(nightly).toContainText("Revoked");
    await expect(page.getByRole("button", { name: "Revoke Nightly import" })).toHaveCount(0);
    expect(revoked).toEqual(["t1"]);

    await page.getByLabel("Token name").fill("Nothing");
    for (const label of ["Follow jobs", "Read results", "Send content"]) await page.getByLabel(label).uncheck();
    await page.getByRole("button", { name: "Create a token" }).click();
    await expect(page.getByRole("alert")).toContainText("Choose at least one permission.");
    expect(created).toHaveLength(1);

    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    const create = await page.getByRole("button", { name: "Create a token" }).boundingBox();
    expect(create?.height).toBeGreaterThanOrEqual(width < 720 ? 40 : 30);
  });
}
