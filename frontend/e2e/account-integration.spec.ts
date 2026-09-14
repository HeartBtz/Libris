import { test, expect } from "@playwright/test";

test("real PostgreSQL account and recovery workflow", async ({ page }) => {
  test.skip(!process.env.LIBRIS_INTEGRATION_URL, "Requires a disposable migrated backend");
  const base = process.env.LIBRIS_INTEGRATION_URL!;
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => {
    if (message.type() === "error" && /Content Security Policy|Refused to/i.test(message.text())) errors.push(message.text());
  });
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  await page.goto(base);
  await page.getByLabel("Username", { exact: true }).fill("tester");
  await page.getByLabel("Password", { exact: true }).fill("test-password-123456789");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Automatic recovery", exact: true }).click();
  await page.getByLabel("Retry delay (seconds)").fill("30");
  await page.getByRole("button", { name: "Save retry delay" }).click();
  await expect(page.getByRole("status")).toContainText("Delay saved");
  expect((await (await page.request.get(`${base}/api/settings/recovery`)).json()).retry_seconds).toBe(30);
  await page.getByRole("button", { name: "Users", exact: true }).click();
  await page.getByLabel("User", { exact: true }).fill("integration-reader");
  await page.getByLabel("Initial password", { exact: true }).fill("integration-password-123");
  await page.getByRole("button", { name: "Create account", exact: true }).click();
  await page.getByRole("button", { name: "Manage integration-reader", exact: true }).click();
  await page.getByLabel("Active account").uncheck();
  await page.getByRole("button", { name: "Save account", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Account saved");
  const accounts = await (await page.request.get(`${base}/api/users`)).json();
  expect(accounts.find((u: { username: string }) => u.username === "integration-reader").active).toBe(false);
  await page.getByRole("link", { name: /My account/ }).click();
  await expect(page.getByRole("heading", { name: "Active sessions" })).toBeVisible();
  expect(errors).toEqual([]);
});
