import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import type { Project } from "../src/types";

const base = process.env.SHOWCASE_URL || "http://127.0.0.1:4173";

function volume(id: string, source_format: Project["source_format"]): Project {
  return {
    id,
    owner_id: "demo-user",
    title: source_format === "epub" ? "Tide Lighthouse" : "Glass Road One",
    author: "Demo collection",
    series_name: "Glass Road",
    volume_number: 1,
    source_format,
    archived_at: null,
    source_language: "en",
    target_language: "fr",
    provider_id: "local",
    quality: "normal",
    context_backend: "internal",
    instructions: "",
    status: "completed",
    stats: {
      total: 2,
      translated: 2,
      validated: 2,
      reviewed_segments: 2,
      review_total: 2,
      flagged: 0,
      errors: 0,
      refused: 0,
      retained_source: 0,
      analyzed_segments: 2,
      synthesized_chapters: 1,
      chapters: 1,
      glossary: 0,
    },
    updated_at: 1789254000,
    book_info: { words: 20, images: 0, size: 100 },
    bible: { summary: "Mira crosses the bridge." },
  };
}

/** A mocked server with one TXT volume and one EPUB volume; returns the export URLs requested. */
async function mockServer(page: Page) {
  const exports: string[] = [];
  const projects = { txt: volume("txt", "txt"), epub: volume("epub", "epub") };
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.includes("/export/")) {
      exports.push(path + url.search);
      await route.fulfill({ contentType: "application/octet-stream", body: "synthetic export" });
      return;
    }
    if (path.endsWith("/events")) {
      await route.fulfill({ contentType: "text/event-stream", body: ": demo\n\n" });
      return;
    }
    let data: unknown = [];
    if (path.endsWith("/auth/me")) data = { id: "demo-user", username: "Démo", admin: true };
    else if (path === "/api/projects") data = Object.values(projects);
    else if (path === "/api/projects/txt") data = projects.txt;
    else if (path === "/api/projects/epub") data = projects.epub;
    else if (path.endsWith("/chapters"))
      data = [{ id: "chapter", title: "Chapter 1", position: 0, resource: "txt/1", instructions: "", analyzed: true }];
    else if (path === "/api/providers") data = [{ id: "local", kind: "openai", name: "Local model", model: "zeta" }];
    await route.fulfill({ json: data });
  });
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  return exports;
}

test("a TXT volume offers chapter files and text formats with their options", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const exports = await mockServer(page);
  await page.goto(`${base}/#project/txt`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Glass Road One");

  await page.getByRole("button", { name: "Export", exact: true }).click();
  const menu = page.getByRole("menu");
  await expect(menu.getByRole("menuitem", { name: "Chapters (.zip, one file per chapter)" })).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: "Consolidated text (.txt)" })).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: "Markdown" })).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: /EPUB/ })).toHaveCount(0);
  const plain = page.waitForEvent("download");
  await menu.getByRole("menuitem", { name: "Chapters (.zip, one file per chapter)" }).click();
  expect((await plain).suggestedFilename()).toBe("Glass Road One - chapitres.zip");

  await page.getByRole("button", { name: "Export", exact: true }).click();
  await page.getByRole("menuitem", { name: "Export options…" }).click();
  const dialog = page.getByRole("dialog", { name: "Export options" });
  await expect(dialog.getByLabel("Format")).toHaveValue("txt-zip");
  await dialog.getByLabel("Fill in with the original text").check();
  await dialog.getByLabel("Add the consolidated text to the ZIP").check();
  const withOptions = page.waitForEvent("download");
  await dialog.getByRole("button", { name: "Export", exact: true }).click();
  await withOptions;
  // Book Bible and archives have no partial variant; the ZIP option only applies to the ZIP.
  await page.getByRole("button", { name: "Export", exact: true }).click();
  await page.getByRole("menuitem", { name: "Export options…" }).click();
  await dialog.getByLabel("Format").selectOption("project");
  await expect(dialog.getByLabel("Fill in with the original text")).toBeDisabled();
  await expect(dialog.getByLabel("Add the consolidated text to the ZIP")).toHaveCount(0);
  await dialog.getByRole("button", { name: "Cancel" }).click();

  expect(exports).toEqual([
    "/api/projects/txt/export/txt-zip",
    "/api/projects/txt/export/txt-zip?allow_source=true&consolidated=true",
  ]);
  expect(errors).toEqual([]);
});

test("an EPUB volume keeps its EPUB exports", async ({ page }) => {
  const exports = await mockServer(page);
  await page.goto(`${base}/#project/epub`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Tide Lighthouse");
  await page.getByRole("button", { name: "Export", exact: true }).click();
  const menu = page.getByRole("menu");
  await expect(menu.getByRole("menuitem", { name: "Translated EPUB", exact: true })).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: "Text", exact: true })).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: /Chapters/ })).toHaveCount(0);
  const download = page.waitForEvent("download");
  await menu.getByRole("menuitem", { name: "Partial EPUB · originals retained" }).click();
  expect((await download).suggestedFilename()).toBe("Tide Lighthouse.epub");
  expect(exports).toEqual(["/api/projects/epub/export/epub?allow_source=true"]);
});
