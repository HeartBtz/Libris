import { expect, test } from "@playwright/test";

test("the same EPUB can be selected again after a failed upload", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  let uploads = 0;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/imports" && request.method() === "POST")
      await route.fulfill({ status: 201, json: { id: "session", format: "epub", files: [], expires_at: 1789999999, result: null } });
    else if (path === "/api/imports/session/files") {
      uploads += 1;
      await route.fulfill({ status: 422, json: { detail: "Archive EPUB ou XML invalide (BadZipFile)." } });
    } else if (path.endsWith("/auth/me"))
      await route.fulfill({ json: { id: "demo-user", username: "Demo", admin: true } });
    else await route.fulfill({ json: [] });
  });
  await page.goto(process.env.SHOWCASE_URL || "http://127.0.0.1:4173");
  await page.getByRole("button", { name: "Add content" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Add content" });
  await dialog.getByRole("radio", { name: /EPUB books/ }).check();
  await dialog.getByRole("button", { name: "Continue" }).click();
  await dialog.getByRole("radio", { name: /Standalone volume/ }).check();
  await dialog.getByRole("button", { name: "Continue" }).click();
  const input = dialog.locator('input[type="file"][accept=".epub"]');
  const file = { name: "book.epub", mimeType: "application/epub+zip", buffer: Buffer.from("not a real epub") };
  await input.setInputFiles(file);
  await expect(dialog.getByText("Archive EPUB ou XML invalide")).toBeVisible();
  await expect.poll(() => uploads).toBe(1);
  await expect(dialog.getByRole("button", { name: "Continue" })).toBeDisabled();
  // Choosing the very same file again must start a new attempt.
  await expect(input).toHaveValue("");
  await input.setInputFiles(file);
  await expect.poll(() => uploads).toBe(2);
  await expect(dialog.locator(".wizard-file")).toHaveCount(1);
});

test("the import wizard sends every user to their own API tokens", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me"))
      await route.fulfill({ json: { id: "reader", username: "Reader", admin: false, active: true } });
    else await route.fulfill({ json: [] });
  });
  await page.goto(process.env.SHOWCASE_URL || "http://127.0.0.1:4173");
  await page.getByRole("button", { name: "Add content" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Add content" });
  await expect(dialog.getByText("Every account creates its own tokens.")).toBeVisible();
  await dialog.getByRole("link", { name: "Create a token in My account › API tokens" }).click();
  await expect(page).toHaveURL(/#account$/);
  await expect(page.getByRole("heading", { name: "My account" })).toBeVisible();
});
