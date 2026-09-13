import { test, expect } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { projectProgress } from "../src/features/progress";
import type { Project } from "../src/types";

// Synthetic content written for documentation. Never connects to a real API.
test("capture public Libris showcase", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const output = resolve(process.cwd(), "../docs/screenshots");
  mkdirSync(output, { recursive: true });
  const base = process.env.SHOWCASE_URL || "http://127.0.0.1:4173";
  const stats = {
    total: 24,
    translated: 18,
    validated: 12,
    reviewed_segments: 16,
    review_total: 24,
    flagged: 1,
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
    instructions: "Preserve a restrained, poetic narrative voice.",
    status: "ready",
    stats,
    updated_at: 1789254000,
    book_info: { words: 18500, images: 2, size: 240000 },
    bible: {},
  };
  const withModel = (project: Project, model: string | null): Project => ({
    ...project,
    progress: { ...projectProgress(project), model },
  });
  const books = [
    withModel(book, "zeta-model"),
    withModel(
      {
        ...book,
        id: "demo-2",
        title: "Copper Gardens",
        volume_number: 2,
        source_language: "ja",
        status: "analyzing",
        stats: {
          ...stats,
          analyzed_segments: 10,
          synthesized_chapters: 0,
          translated: 0,
          validated: 0,
          flagged: 0,
        },
      },
      "alpha-model",
    ),
    withModel(
      {
        ...book,
        id: "demo-3",
        title: "Atlas for Tomorrow",
        series_name: "",
        volume_number: null,
        source_language: "es",
        status: "completed",
        stats: { ...stats, translated: 24, validated: 24, flagged: 0 },
      },
      null,
    ),
    withModel(
      {
        ...book,
        id: "demo-archive",
        title: "Mist Journal",
        series_name: "Tide Chronicles",
        volume_number: 4,
        archived_at: 1789167600,
        status: "completed",
        stats: { ...stats, translated: 24, validated: 24, flagged: 0 },
      },
      "beta-model",
    ),
  ];
  const chapter = {
    id: "chapter",
    title: "Chapter 1 · The Return",
    position: 0,
    resource: "chapter.xhtml",
    instructions: "",
    analyzed: true,
  };
  const source =
    "At dawn, Mara returned to the lighthouse. The sea had carried away the old path, but the lantern still shone above the mist.";
  const translation =
    "À l’aube, Mara revint au phare. La mer avait emporté l’ancien sentier, mais la lanterne brillait encore au-dessus de la brume.";
  const segment = {
    id: "segment",
    project_id: "demo",
    chapter_id: "chapter",
    position: 0,
    section: "The Return",
    source,
    translation,
    units: [{ id: "u1", text: source }],
    translated_units: [{ id: "u1", text: translation }],
    status: "check",
    stage: "done",
    human: false,
    validated: false,
    revision: 0,
    retained_source: false,
    instructions: "",
    error: "",
    uncertainties: [],
    critique: [
      {
        unit_id: "u1",
        category: "style",
        severity: "warning",
        description:
          "Le mot « lantern » désigne ici la lumière du phare. « Lanterne » peut évoquer un objet portatif plutôt que le signal qui guide Mara.",
        suggestion:
          "À l’aube, Mara revint au phare. La mer avait emporté l’ancien sentier, mais le feu brillait encore au-dessus de la brume.",
      },
    ],
  };
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/events")) {
      await route.fulfill({
        contentType: "text/event-stream",
        body: ": demo\n\n",
      });
      return;
    }
    let data: unknown = [];
    if (path.endsWith("/auth/me"))
      data = { id: "demo-user", username: "Démo", admin: true };
    else if (path === "/api/projects") data = books;
    else if (path === "/api/projects/demo") data = book;
    else if (path.endsWith("/chapters")) data = [chapter];
    else if (path.endsWith("/segments"))
      data = url.searchParams.get("status") === "refused" ? [] : [segment];
    else if (path === "/api/providers") data = [];
    else if (path.endsWith("/final-review"))
      data = {
        automatic: true,
        web_enabled: false,
        eligible: 1,
        summary: {
          examined: 16,
          total: 24,
          resolved: 11,
          needs_human: 5,
          remaining: 1,
          protected: 12,
          revised: 7,
          failed: 0,
        },
      };
    await route.fulfill({ json: data });
  });
  await page.setViewportSize({ width: 1440, height: 1040 });
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  await page.goto(base);
  await expect(
    page.getByRole("heading", { name: "Library", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("progressbar")).toHaveCount(3);
  await expect(page.getByRole("button", { name: /Archives/ })).toContainText(
    "1",
  );
  await expect(
    page.getByRole("progressbar", {
      name: "Translation for Tide Lighthouse",
    }),
  ).toHaveAttribute("value", "75");
  await expect(
    page.getByRole("progressbar", {
      name: "Analysis & memory for Copper Gardens",
    }),
  ).toHaveAttribute("value", "36");
  await expect(
    page.getByRole("progressbar", {
      name: "Export for Atlas for Tomorrow",
    }),
  ).toHaveAttribute("value", "100");
  await page.screenshot({ path: resolve(output, "library.png") });
  await page.getByRole("button", { name: /In progress/ }).click();
  await expect(page.locator(".library-table tbody tr")).toHaveCount(1);
  await expect(
    page.getByRole("link", { name: "Copper Gardens", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Search for a book").fill("introuvable");
  await expect(
    page.getByText("No books match.", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page.locator(".library-table tbody tr")).toHaveCount(3);
  await page
    .getByRole("combobox", { name: "Series", exact: true })
    .selectOption("Tide Chronicles");
  await expect(page.locator(".library-table tbody tr")).toHaveCount(2);
  await expect(page.locator(".series-volumes li")).toHaveCount(3);
  await expect(page.locator(".series-volumes .badge.archived")).toHaveText(
    "Archived",
  );
  await page.screenshot({
    path: resolve(output, "series.png"),
    fullPage: true,
  });
  await page
    .getByRole("combobox", { name: "Series", exact: true })
    .selectOption("all");
  await page.getByRole("button", { name: /Archives/ }).click();
  await expect(
    page.getByRole("link", { name: "Mist Journal", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Restore", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: resolve(output, "archives.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: /All books/ }).click();
  await page.getByLabel("Sort by").selectOption("title");
  await page.getByRole("button", { name: "Sort by status" }).click();
  await expect(page.getByLabel("Sort by")).toHaveValue("status");
  await expect(page.locator(".library-table .book-title")).toHaveText([
    "Copper Gardens",
    "Atlas for Tomorrow",
    "Tide Lighthouse",
  ]);
  await page.getByLabel("Sort by").selectOption("model");
  await expect(page.locator(".library-table .book-title")).toHaveText([
    "Copper Gardens",
    "Tide Lighthouse",
    "Atlas for Tomorrow",
  ]);
  await page
    .getByRole("button", { name: "+ Import EPUBs", exact: true })
    .focus();
  await expect(
    page.getByRole("button", { name: "+ Import EPUBs", exact: true }),
  ).toBeFocused();
  await page.getByLabel("Sort by").selectOption("recent");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    390,
  );
  await page.screenshot({
    path: resolve(output, "mobile.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1040 });
  await page.goto(`${base}/#project/demo`);
  await expect(page.getByText(source, { exact: true })).toBeVisible();
  await expect(page.getByRole("progressbar")).toHaveCount(1);
  for (const [button, detail] of [
    ["1 · Import", "EPUB imported and structure loaded."],
    [
      "2 · Analysis & memory",
      "24/24 segments analyzed · 4/4 sections synthesized.",
    ],
    [
      "3 · Translation",
      "18/24 segments translated · 0 retained in the original.",
    ],
    ["4 · Review", "16/24 segments reviewed · 0 resolved · 1 to review."],
    ["5 · Export", "Resolve the remaining alerts before the final export."],
  ]) {
    await page.getByRole("button", { name: button, exact: true }).click();
    await expect(page.getByText(detail, { exact: true })).toBeVisible();
    await expect(page.getByRole("progressbar")).toHaveCount(1);
  }
  await page
    .getByRole("button", { name: "3 · Translation", exact: true })
    .click();
  await page.getByRole("button", { name: "Follow the active stage" }).click();
  await page.screenshot({
    path: resolve(output, "progress-stages.png"),
    fullPage: true,
  });
  await page.screenshot({ path: resolve(output, "editor.png") });
  await page
    .getByRole("button", { name: "Validations (1)", exact: true })
    .click();
  await expect(page.getByText("AI opinion", { exact: true })).toBeVisible();
  await page.screenshot({
    path: resolve(output, "validations.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    390,
  );
  await page.screenshot({
    path: resolve(output, "validations-mobile.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1040 });
  await page.getByRole("button", { name: "Change theme", exact: true }).click();
  await page.screenshot({
    path: resolve(output, "validations-light.png"),
    fullPage: true,
  });
  await page.getByText("Export ↓", { exact: true }).click();
  const download = page.waitForEvent("download");
  await page
    .getByRole("link", { name: "Translated EPUB", exact: true })
    .click();
  await download;
  await expect(
    page.getByText("File received; download sent to the browser."),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Translation validations" }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});
