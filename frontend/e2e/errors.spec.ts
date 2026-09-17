import { expect, test } from "@playwright/test";
import { projectProgress } from "../src/features/progress";
import type { Project } from "../src/types";

const base = process.env.SHOWCASE_URL || "http://127.0.0.1:4173";

test("validation errors never echo the typed password", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({ status: 401, json: { detail: "Unauthorized" } }),
  );
  await page.route("**/api/auth/login", (route) =>
    route.fulfill({
      status: 422,
      json: {
        detail: [
          {
            type: "string_pattern_mismatch",
            loc: ["body", "username"],
            msg: "String should match pattern",
            input: "bad name",
          },
          {
            type: "string_too_long",
            loc: ["body", "password"],
            msg: "String should have at most 200 characters",
            input: "typed-secret-password",
          },
        ],
      },
    }),
  );
  await page.goto(base);
  const password = page.getByLabel("Password", { exact: true });
  await expect(password).toHaveAttribute("minlength", "12");
  await page.getByLabel("Username", { exact: true }).fill("bad name");
  await password.fill("typed-secret-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("username : String should match pattern");
  await expect(alert).toContainText("password : String should have at most");
  await expect(alert).not.toContainText("typed-secret-password");
  await expect(alert).not.toContainText("input");
});

test("a non-JSON proxy error is reported with its HTTP status", async ({
  page,
}) => {
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({ status: 401, json: { detail: "Unauthorized" } }),
  );
  await page.route("**/api/auth/login", (route) =>
    route.fulfill({
      status: 502,
      contentType: "text/html",
      body: "<html><body>Bad Gateway</body></html>",
    }),
  );
  await page.goto(base);
  await page.getByLabel("Username", { exact: true }).fill("owner");
  await page
    .getByLabel("Password", { exact: true })
    .fill("a-valid-password-123");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("HTTP 502");
  await expect(alert).not.toContainText("Unexpected token");
});

test("a rejected bulk export names the book and lists EPUBCheck errors", async ({
  page,
}) => {
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  const stats = {
    total: 24,
    translated: 24,
    validated: 24,
    reviewed_segments: 24,
    review_total: 24,
    flagged: 0,
    errors: 0,
    refused: 0,
    retained_source: 0,
    analyzed_segments: 24,
    synthesized_chapters: 4,
    chapters: 4,
    glossary: 8,
  };
  const book: Project = {
    id: "demo",
    owner_id: "demo-user",
    title: "Tide Lighthouse",
    author: "Demo collection",
    series_name: "Tide Chronicles",
    volume_number: 1,
    archived_at: null,
    source_language: "en",
    target_language: "fr",
    provider_id: "local",
    quality: "high",
    context_backend: "internal",
    instructions: "",
    status: "completed",
    stats,
    updated_at: 1789254000,
    book_info: { words: 18500, images: 2, size: 240000 },
    bible: {},
  };
  const books = [{ ...book, progress: projectProgress(book) }];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/exports/epub") {
      await route.fulfill({
        status: 422,
        json: {
          detail: {
            message:
              "EPUBCheck signale un EPUB invalide : « Tide Lighthouse ».",
            book: "Tide Lighthouse",
            errors: [
              'RSC-007 — Referenced resource "OEBPS/js/kobo.js" could not be found in the EPUB. (OEBPS/Text/toc.xhtml)',
            ],
            validation: {
              available: true,
              valid: false,
              report: { messages: [] },
            },
          },
        },
      });
      return;
    }
    let data: unknown = [];
    if (path.endsWith("/auth/me"))
      data = { id: "demo-user", username: "Demo", admin: true };
    else if (path === "/api/projects") data = books;
    await route.fulfill({ json: data });
  });
  await page.goto(base);
  await page.getByRole("checkbox", { name: "Select Tide Lighthouse" }).check();
  await page.getByRole("button", { name: "Export EPUBs" }).click();
  const status = page.getByRole("status").filter({ hasText: "EPUBCheck" });
  await expect(status).toContainText("« Tide Lighthouse »");
  await expect(status).toContainText("RSC-007 — Referenced resource");
  await expect(status).not.toContainText('"validation"');
  await expect(status).not.toContainText("{");
});
