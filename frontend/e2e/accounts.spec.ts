import { expect, test } from "@playwright/test";

for (const width of [1440, 390]) {
  test(`accounts and recovery controls at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript(() => localStorage.setItem("locale", "en"));
    const changes: { path: string; body: unknown }[] = [];
    const me = { id: "admin", username: "owner", admin: true, active: true };
    const users = [me, { id: "reader", username: "reader", admin: false, active: true }];
    let sessions = [
      { id: "current", current: true, expires_at: 1900000000 },
      { id: "other", current: false, expires_at: 1900000000 },
    ];
    await page.route("**/api/**", async route => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      let body: unknown = {};
      if (request.method() === "PUT") {
        changes.push({ path, body: request.postDataJSON() });
      } else if (path === "/api/auth/me") body = me;
      else if (path === "/api/users") body = users;
      else if (path === "/api/auth/sessions") body = sessions;
      else if (path === "/api/auth/sessions/other") sessions = sessions.filter(s => s.current);
      else if (path === "/api/settings/recovery") body = { retry_seconds: 60 };
      else body = [];
      await route.fulfill({ json: body });
    });
    await page.goto(`${process.env.SHOWCASE_URL || "http://127.0.0.1:4173"}/#account`);
    await expect(page.getByRole("heading", { name: "My account" })).toBeVisible();
    await page.getByRole("listitem").filter({ hasText: "Other session" }).getByRole("button", { name: "Revoke" }).click();
    await expect(page.getByRole("listitem")).toHaveCount(1);
    await page.getByLabel("Current password", { exact: true }).fill("current-password-123");
    await page.getByLabel("New password", { exact: true }).fill("replacement-password-123");
    await page.getByLabel("Confirm password", { exact: true }).fill("different-password-123");
    await page.getByRole("button", { name: "Change password", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Passwords do not match");
    expect(changes).toHaveLength(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.screenshot({ path: `/tmp/opencode/libris-account-${width}.png`, fullPage: true });
    await page.getByRole("link", { name: "Settings", exact: true }).click();
    await page.getByRole("button", { name: "Automatic recovery", exact: true }).click();
    await page.getByLabel("Retry delay (seconds)").fill("30");
    await page.getByRole("button", { name: "Save retry delay" }).click();
    await expect(page.getByRole("status")).toContainText("Delay saved");
    expect(changes.at(-1)).toEqual({ path: "/api/settings/recovery", body: { retry_seconds: 30 } });
    await page.getByRole("button", { name: "Users", exact: true }).click();
    await page.getByRole("button", { name: "Manage reader", exact: true }).click();
    await page.getByLabel("Active account", { exact: true }).uncheck();
    await page.getByRole("button", { name: "Save account", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("Account saved");
    expect(changes.at(-1)).toEqual({ path: "/api/users/reader", body: { admin: false, active: false } });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.screenshot({ path: `/tmp/opencode/libris-users-${width}.png`, fullPage: true });
  });
}
