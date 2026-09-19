import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import type { GlossaryImportReport, SharedGlossary } from "../src/types";

const base = process.env.SHOWCASE_URL || "http://127.0.0.1:4173";

const glossary: SharedGlossary = {
  id: "universe",
  name: "Glass Road universe",
  description: "Names shared by every Glass Road series.",
  source_language: "en",
  target_language: "fr",
  created_at: 1789254000,
  updated_at: 1789254000,
  term_count: 2,
  locked_count: 1,
  series: [{ id: "glass-road", name: "Glass Road" }],
  terms: [
    { id: "t1", glossary_id: "universe", source: "Moonwell", translation: "Puits-de-Lune", category: "lieu",
      description: "", locked: true, accepted: true },
    { id: "t2", glossary_id: "universe", source: "Veilstone", translation: "Pierre-voile", category: "objet",
      description: "", locked: false, accepted: true },
  ],
};

function report(strategy: string): GlossaryImportReport {
  const replace = strategy !== "skip";
  return {
    format: "csv",
    encoding: "utf-8-sig",
    delimiter: ";",
    columns: ["Terme source", "Traduction", "Verrouillé"],
    header: true,
    mapping: { source: 0, translation: 1, locked: 2 },
    strategy: strategy as GlossaryImportReport["strategy"],
    counts: { terms: 3, new: 1, unchanged: 0, conflicts: 1, replaced: replace ? 1 : 0, kept: replace ? 0 : 1,
              duplicates: 1, errors: 1 },
    new: [{ line: 2, source: "Ashen Guard", translation: "Garde cendrée", category: "autre", description: "",
            locked: false, accepted: true }],
    conflicts: [{ line: 3, source: "Veilstone", fields: ["translation"], locked: false,
                  action: replace ? "replace" : "keep",
                  existing: { translation: "Pierre-voile", category: "objet", description: "", locked: false, accepted: true },
                  incoming: { translation: "Pierre de voile", category: "objet", description: "", locked: false, accepted: true } }],
    duplicates: [{ line: 4, source: "ashen guard", first_line: 2 }],
    errors: [{ line: 5, message: "Unrecognised value for locked: “maybe”" }],
    truncated: false,
    applied: false,
  };
}

async function mockServer(page: Page) {
  const posts: { path: string; body: string }[] = [];
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/events")) {
      await route.fulfill({ contentType: "text/event-stream", body: ": demo\n\n" });
      return;
    }
    const body = route.request().postData() || "";
    if (route.request().method() === "POST") posts.push({ path, body });
    const strategy = /name="strategy"\r\n\r\n(\w+)/.exec(body)?.[1] || "skip";
    let data: unknown = [];
    if (path.endsWith("/auth/me")) data = { id: "demo-user", username: "Démo", admin: false };
    else if (path === "/api/glossaries") data = [glossary];
    else if (path === "/api/glossaries/universe") data = glossary;
    else if (path.endsWith("/import/preview")) data = report(strategy);
    else if (path.endsWith("/import")) data = { ...report(strategy), applied: true, imported: 1, replaced: 1, skipped: 1 };
    await route.fulfill({ json: data });
  });
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  return posts;
}

test("a shared glossary import is previewed, its conflicts decided, then applied", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const posts = await mockServer(page);
  await page.goto(`${base}/#glossaries`);
  await expect(page.getByRole("heading", { level: 1, name: "Shared glossaries" })).toBeVisible();
  await expect(page.getByText("2 term(s) · 1 locked")).toBeVisible();
  await page.getByRole("button", { name: "Glass Road universe" }).click();
  await expect(page.getByRole("link", { name: "Glass Road" })).toHaveAttribute("href", "#series/glass-road");
  await expect(page.getByLabel("Translation of Moonwell")).toHaveValue("Puits-de-Lune");

  await page.locator('input[type="file"]').setInputFiles({
    name: "universe.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("﻿Terme source;Traduction;Verrouillé\nAshen Guard;Garde cendrée;non\n"),
  });
  const dialog = page.getByRole("dialog", { name: "Import preview" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("separator « ; »", { exact: false })).toBeVisible();
  await expect(dialog.getByText("1 conflict(s)")).toBeVisible();
  await expect(dialog.getByText("Row 5: Unrecognised value for locked: “maybe”")).toBeVisible();
  await expect(dialog.getByText("Row 4: “ashen guard”, already row 2")).toBeVisible();
  await expect(dialog.getByRole("cell", { name: "Kept" })).toBeVisible();
  await expect(dialog.getByLabel("Source expression")).toHaveValue("0");

  await dialog.getByLabel("On conflict").selectOption("replace");
  await expect(dialog.getByRole("cell", { name: "Replaced" })).toBeVisible();
  await dialog.getByRole("button", { name: "Apply the import" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("status").filter({ hasText: "1 term(s) added, 1 replaced, 1 left out." })).toBeVisible();

  const [first, second, applied] = posts;
  expect(first.path).toBe("/api/glossaries/universe/import/preview");
  expect(second.body).toContain('name="strategy"\r\n\r\nreplace');
  expect(applied.path).toBe("/api/glossaries/universe/import");
  expect(applied.body).toContain('name="skip_invalid"\r\n\r\ntrue');
  expect(errors).toEqual([]);
});

test("the shared glossaries page fits a phone screen", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await mockServer(page);
  await page.goto(`${base}/#glossaries`);
  await expect(page.getByRole("heading", { level: 1, name: "Shared glossaries" })).toBeVisible();
  await page.getByRole("button", { name: "Glass Road universe" }).click();
  await expect(page.getByLabel("Translation of Veilstone")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  const name = await page.getByRole("button", { name: "Back to the list" }).boundingBox();
  expect(name?.height ?? 0).toBeGreaterThanOrEqual(40);
});
