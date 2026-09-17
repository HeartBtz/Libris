import { expect, test } from "@playwright/test";

test("the same EPUB can be selected again after a failed import", async ({
  page,
}) => {
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  let uploads = 0;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/projects" && request.method() === "POST") {
      uploads += 1;
      await route.fulfill({
        status: 422,
        json: { detail: "Archive EPUB ou XML invalide (BadZipFile)." },
      });
    } else if (path.endsWith("/auth/me"))
      await route.fulfill({
        json: { id: "demo-user", username: "Demo", admin: true },
      });
    else await route.fulfill({ json: [] });
  });
  await page.goto(process.env.SHOWCASE_URL || "http://127.0.0.1:4173");
  const input = page.locator('input[type="file"][accept=".epub"]');
  const file = {
    name: "book.epub",
    mimeType: "application/epub+zip",
    buffer: Buffer.from("not a real epub"),
  };
  await input.setInputFiles(file);
  await expect(page.getByText("Archive EPUB ou XML invalide")).toBeVisible();
  await expect.poll(() => uploads).toBe(1);
  // Choosing the very same file again must start a new attempt.
  await expect(input).toHaveValue("");
  await input.setInputFiles(file);
  await expect.poll(() => uploads).toBe(2);
});
