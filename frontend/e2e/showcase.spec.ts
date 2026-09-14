import { test, expect } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { projectProgress } from "../src/features/progress";
import type { Project } from "../src/types";
import type { Page } from "@playwright/test";

async function contrast(page: Page, foreground: string, background: string) {
  return page.evaluate(
    ([fgName, bgName]) => {
      const styles = getComputedStyle(document.documentElement);
      const luminance = (value: string) => {
        const hex = styles.getPropertyValue(value).trim().slice(1);
        const channels = [0, 2, 4].map(
          (offset) => Number.parseInt(hex.slice(offset, offset + 2), 16) / 255,
        );
        return channels
          .map((channel) =>
            channel <= 0.04045
              ? channel / 12.92
              : ((channel + 0.055) / 1.055) ** 2.4,
          )
          .reduce(
            (total, channel, index) =>
              total + channel * [0.2126, 0.7152, 0.0722][index],
            0,
          );
      };
      const [lighter, darker] = [luminance(fgName), luminance(bgName)].sort(
        (a, b) => b - a,
      );
      return (lighter + 0.05) / (darker + 0.05);
    },
    [foreground, background],
  );
}

async function expectNoOverflow(page: Page) {
  for (const viewport of [
    { width: 320, height: 720 },
    { width: 360, height: 800 },
    { width: 390, height: 844 },
    { width: 430, height: 932 },
    { width: 768, height: 1024 },
    { width: 1024, height: 768 },
    { width: 1440, height: 1040 },
    { width: 1920, height: 1080 },
    { width: 844, height: 390 },
  ]) {
    await page.setViewportSize(viewport);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 2,
      ),
    ).toBe(true);
  }
}

// Synthetic content written for documentation. Never connects to a real API.
test("capture public Libris showcase", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const output =
    process.env.SHOWCASE_SCREENSHOT_DIR || resolve("/tmp/libris/showcase");
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
  let exportedProjects: string[] = [];
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
    if (path === "/api/exports/epub" && route.request().method() === "POST") {
      exportedProjects = (
        route.request().postDataJSON() as { project_ids: string[] }
      ).project_ids;
      await route.fulfill({
        contentType: "application/zip",
        body: "synthetic zip",
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
  for (const pair of [
    ["--text", "--base"],
    ["--muted", "--base"],
    ["--accent", "--base"],
    ["--on-accent", "--accent"],
  ])
    expect(await contrast(page, pair[0], pair[1])).toBeGreaterThanOrEqual(4.5);
  expect(await contrast(page, "--line", "--input")).toBeGreaterThanOrEqual(3);
  expect(await contrast(page, "--muted", "--input")).toBeGreaterThanOrEqual(
    4.5,
  );
  await page.getByRole("button", { name: "Change theme", exact: true }).click();
  for (const pair of [
    ["--text", "--base"],
    ["--muted", "--base"],
    ["--accent", "--base"],
    ["--on-accent", "--accent"],
  ])
    expect(await contrast(page, pair[0], pair[1])).toBeGreaterThanOrEqual(4.5);
  expect(await contrast(page, "--line", "--input")).toBeGreaterThanOrEqual(3);
  expect(await contrast(page, "--muted", "--input")).toBeGreaterThanOrEqual(
    4.5,
  );
  await page.getByRole("button", { name: "Change theme", exact: true }).click();
  const libraryUrl = page.url();
  const skipLink = page.getByRole("link", { name: "Skip to content" });
  await skipLink.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();
  expect(page.url()).toBe(libraryUrl);
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
  const firstBook = page.getByRole("checkbox", {
    name: "Select Tide Lighthouse",
  });
  await firstBook.check();
  await expect(page.getByLabel("Memory source")).toBeVisible();
  await page.getByLabel("Memory source").selectOption("hybrid");
  const bulkDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export EPUBs" }).click();
  expect((await bulkDownload).suggestedFilename()).toBe("libris-epubs.zip");
  expect(exportedProjects).toEqual(["demo"]);
  await firstBook.uncheck();
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
  await page.getByLabel("Sort by", { exact: true }).selectOption("title");
  await page.getByRole("button", { name: "Sort by status" }).click();
  await expect(page.getByLabel("Sort by", { exact: true })).toHaveValue(
    "status",
  );
  await expect(page.locator(".library-table .book-title")).toHaveText([
    "Copper Gardens",
    "Atlas for Tomorrow",
    "Tide Lighthouse",
  ]);
  await page.getByLabel("Sort by", { exact: true }).selectOption("model");
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
  await page.getByLabel("Sort by", { exact: true }).selectOption("recent");
  await expectNoOverflow(page);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    390,
  );
  await page.screenshot({
    path: resolve(output, "mobile.png"),
    fullPage: true,
  });
  await expect(
    page.getByRole("navigation", { name: "Primary navigation" }),
  ).toBeHidden();
  await page.getByRole("button", { name: "Menu", exact: true }).click();
  await expect(
    page.getByRole("navigation", { name: "Primary navigation" }),
  ).toBeVisible();
  const settingsLink = page.getByRole("link", {
    name: "Settings",
    exact: true,
  });
  await expect(settingsLink).toBeVisible();
  await settingsLink.click();
  await expect(page.locator("#main-content")).toBeFocused();
  await expect(
    page.getByRole("navigation", { name: "Primary navigation" }),
  ).toBeHidden();
  await page.setViewportSize({ width: 1440, height: 1040 });
  await page.goto(`${base}/#project/demo`);
  await expect(page.getByText(source, { exact: true })).toBeVisible();
  await expectNoOverflow(page);
  await page.setViewportSize({ width: 1440, height: 1040 });
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
  const inspectorTrigger = page
    .getByRole("button", { name: "Context / history / Ask AI", exact: true })
    .first();
  await inspectorTrigger.click();
  await expect(
    page.getByRole("button", { name: "Close inspector", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(inspectorTrigger).toBeFocused();
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
  await expect(page.locator(".workspace-nav-select select")).toHaveValue(
    "validations",
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
