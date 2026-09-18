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
        const channels = [0, 2, 4].map((offset) => Number.parseInt(hex.slice(offset, offset + 2), 16) / 255);
        return channels
          .map((channel) => (channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4))
          .reduce((total, channel, index) => total + channel * [0.2126, 0.7152, 0.0722][index], 0);
      };
      const [lighter, darker] = [luminance(fgName), luminance(bgName)].sort((a, b) => b - a);
      return (lighter + 0.05) / (darker + 0.05);
    },
    [foreground, background],
  );
}

/** WCAG AA for text and 3:1 for the input boundaries, on the current theme's tokens. */
async function expectReadableTokens(page: Page) {
  for (const [foreground, background] of [
    ["--text", "--bg"],
    ["--text", "--surface"],
    ["--text-muted", "--bg"],
    ["--text-muted", "--surface"],
    ["--text-muted", "--surface-sunken"],
    ["--accent", "--bg"],
    ["--accent-text", "--accent-soft"],
    ["--on-accent", "--accent"],
    ["--success", "--success-soft"],
    ["--warning", "--warning-soft"],
    ["--danger", "--danger-soft"],
    ["--info", "--info-soft"],
  ])
    expect(await contrast(page, foreground, background), `${foreground} on ${background}`).toBeGreaterThanOrEqual(4.5);
  expect(await contrast(page, "--border-input", "--surface")).toBeGreaterThanOrEqual(3);
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
    // Lists what sticks out, so a failure names the culprit instead of a bare boolean. Polling lets
    // the layout settle: some regions switch component (table to cards) once the resize is seen.
    await expect
      .poll(
        () =>
          page.evaluate(() =>
            document.documentElement.scrollWidth <= innerWidth + 2
              ? []
              : Array.from(document.querySelectorAll("body *"))
                  .filter((element) => element.getBoundingClientRect().right > innerWidth + 2)
                  .map((element) => `${element.tagName.toLowerCase()}.${element.className}`)
                  .slice(0, 5),
          ),
        { message: `horizontal overflow at ${viewport.width}px`, timeout: 2000 },
      )
      .toEqual([]);
  }
}

async function chooseTheme(page: Page, theme: "Light" | "Dark") {
  await page.getByRole("button", { name: /Account menu/ }).click();
  await page.getByRole("menuitemradio", { name: theme }).click();
}

// Synthetic content written for documentation. Never connects to a real API.
test("capture public Libris showcase", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce", colorScheme: "light" });
  const output = process.env.SHOWCASE_SCREENSHOT_DIR || resolve("/tmp/libris/showcase");
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
    bible: { summary: "Mara returns to the lighthouse of her childhood." },
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
        stats: { ...stats, analyzed_segments: 10, synthesized_chapters: 0, translated: 0, validated: 0, flagged: 0 },
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
  const chapters = ["The Return", "Salt and Iron", "The Keeper’s Log", "Low Tide"].map((title, position) => ({
    id: position ? `chapter-${position}` : "chapter",
    title: `Chapter ${position + 1} · ${title}`,
    position,
    resource: `chapter${position}.xhtml`,
    instructions: "",
    analyzed: position < 3,
  }));
  const source =
    "At dawn, Mara returned to the lighthouse. The sea had carried away the old path, but the lantern still shone above the mist.";
  const translation =
    "À l’aube, Mara revint au phare. La mer avait emporté l’ancien sentier, mais la lanterne brillait encore au-dessus de la brume.";
  const passage = (position: number, text: string, translated: string, extra: object = {}) => ({
    id: `segment-${position}`,
    project_id: "demo",
    chapter_id: "chapter",
    position,
    section: "The Return",
    source: text,
    translation: translated,
    units: [{ id: "u1", text }],
    translated_units: [{ id: "u1", text: translated }],
    status: "ok",
    stage: "done",
    human: false,
    validated: false,
    revision: 0,
    retained_source: false,
    instructions: "",
    error: "",
    uncertainties: [] as string[],
    critique: [] as object[],
    ...extra,
  });
  let savedTranslation = translation;
  const segments = () => [
    passage(0, source, savedTranslation, {
      status: "check",
      uncertainties: ["“lantern”: the lighthouse light, or a hand-held lantern?"],
      critique: [
        {
          unit_id: "u1",
          category: "style",
          severity: "warning",
          description:
            "« Lantern » désigne ici la lumière du phare. « Lanterne » peut évoquer un objet portatif plutôt que le signal qui guide Mara.",
          suggestion:
            "À l’aube, Mara revint au phare. La mer avait emporté l’ancien sentier, mais le feu brillait encore au-dessus de la brume.",
        },
      ],
    }),
    passage(
      1,
      "She climbed the ⟦t0⟧hundred and twelve⟦/t0⟧ steps without counting them, the way her father had taught her.",
      "Elle gravit les ⟦t0⟧cent douze⟦/t0⟧ marches sans les compter, comme son père le lui avait appris.",
    ),
    passage(
      2,
      "The last entry in the keeper’s log read: ⟦t1⟧Do not trust the tide tables.⟦/t1⟧⟦x2⟧",
      "La dernière entrée du journal du gardien disait : ⟦t1⟧Ne vous fiez pas aux tables des marées.⟦/t1⟧⟦x2⟧",
      { human: true, validated: true, revision: 2 },
    ),
  ];
  const errors: string[] = [];
  let exportedProjects: string[] = [];
  const languages = new Set<string>();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    // EventSource cannot set headers: only requests made by the interface itself are checked.
    if (!path.endsWith("/events")) languages.add(request.headers()["accept-language"] || "");
    if (path === "/api/segments/segment-0" && request.method() === "PUT") {
      const body = request.postDataJSON() as { units: { id: string; text: string }[] };
      savedTranslation = body.units[0].text;
      await route.fulfill({ json: { ...segments()[0], human: true, revision: 1 } });
      return;
    }
    if (path.endsWith("/events")) {
      await route.fulfill({ contentType: "text/event-stream", body: ": demo\n\n" });
      return;
    }
    if (path === "/api/exports/epub" && request.method() === "POST") {
      exportedProjects = (request.postDataJSON() as { project_ids: string[] }).project_ids;
      await route.fulfill({ contentType: "application/zip", body: "synthetic zip" });
      return;
    }
    if (path.endsWith("/export/epub")) {
      await route.fulfill({ contentType: "application/epub+zip", body: "synthetic epub" });
      return;
    }
    let data: unknown = [];
    if (path.endsWith("/auth/me")) data = { id: "demo-user", username: "Démo", admin: true };
    else if (path === "/api/projects") data = books;
    else if (path === "/api/projects/demo") data = books[0];
    else if (path.endsWith("/chapters")) data = chapters;
    else if (path.includes("/preview/")) data = { html: `<p>${savedTranslation}</p>` };
    else if (path.endsWith("/segments"))
      data =
        url.searchParams.get("status") === "check"
          ? [segments()[0]]
          : url.searchParams.get("status")
            ? []
            : segments();
    else if (path === "/api/providers") data = [{ id: "local", kind: "openai", name: "Local model", model: "zeta-model" }];
    else if (path.endsWith("/final-review"))
      data = {
        automatic: true,
        web_enabled: false,
        eligible: 1,
        summary: { examined: 16, total: 24, resolved: 11, needs_human: 5, remaining: 1, protected: 12, revised: 7, failed: 0 },
      };
    else if (path.endsWith("/issues")) data = [];
    await route.fulfill({ json: data });
  });
  await page.setViewportSize({ width: 1440, height: 1040 });
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  await page.goto(base);
  await expect(page.getByRole("heading", { name: "Library", exact: true })).toBeVisible();

  // Both themes keep readable text and visible field boundaries.
  await expectReadableTokens(page);
  await chooseTheme(page, "Dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expectReadableTokens(page);
  await chooseTheme(page, "Light");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

  const libraryUrl = page.url();
  const skipLink = page.getByRole("link", { name: "Skip to content" });
  await skipLink.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();
  expect(page.url()).toBe(libraryUrl);

  await expect(page.getByRole("progressbar")).toHaveCount(3);
  const filters = page.getByRole("radiogroup", { name: "Filter books" });
  await expect(filters.getByRole("radio", { name: /Archives/ })).toContainText("1");
  await expect(page.getByRole("progressbar", { name: "Translation for Tide Lighthouse" })).toHaveAttribute("value", "75");
  await expect(page.getByRole("progressbar", { name: "Analysis & memory for Copper Gardens" })).toHaveAttribute(
    "value",
    "36",
  );
  await expect(page.getByRole("progressbar", { name: "Export for Atlas for Tomorrow" })).toHaveAttribute("value", "100");

  const firstBook = page.getByRole("checkbox", { name: "Select Tide Lighthouse" });
  await firstBook.check();
  const batch = page.getByRole("region", { name: "Actions for multiple books" });
  await batch.getByRole("button", { name: "Configure…" }).click();
  const configure = page.getByRole("dialog", { name: "Configure the selected books" });
  await configure.getByLabel("Memory source").selectOption("hybrid");
  await configure.getByRole("button", { name: "Cancel" }).click();
  const bulkDownload = page.waitForEvent("download");
  await batch.getByRole("button", { name: "Export EPUBs" }).click();
  expect((await bulkDownload).suggestedFilename()).toBe("libris-epubs.zip");
  expect(exportedProjects).toEqual(["demo"]);
  await firstBook.uncheck();
  await expect(batch).toHaveCount(0);
  await page.screenshot({ path: resolve(output, "library.png") });

  const rows = page.locator(".library-table tbody tr");
  await filters.getByRole("radio", { name: /In progress/ }).click();
  await expect(rows).toHaveCount(1);
  await expect(page.getByRole("link", { name: "Copper Gardens", exact: true })).toBeVisible();
  await page.getByRole("searchbox", { name: "Search for a book" }).fill("introuvable");
  await expect(page.getByText("No books match.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(rows).toHaveCount(3);
  await page.getByRole("combobox", { name: "Series", exact: true }).selectOption("Tide Chronicles");
  await expect(rows).toHaveCount(2);
  const series = page.getByRole("region", { name: "Series Tide Chronicles" });
  await expect(series.getByRole("listitem")).toHaveCount(3);
  await expect(series.getByText("Archived", { exact: true })).toBeVisible();
  await page.screenshot({ path: resolve(output, "series.png"), fullPage: true });
  await page.getByRole("combobox", { name: "Series", exact: true }).selectOption("all");

  await filters.getByRole("radio", { name: /Archives/ }).click();
  await expect(page.getByRole("link", { name: "Mist Journal", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Actions for Mist Journal" }).click();
  await expect(page.getByRole("menuitem", { name: "Restore", exact: true })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "Delete", exact: true })).toBeVisible();
  await page.screenshot({ path: resolve(output, "archives.png"), fullPage: true });
  await page.keyboard.press("Escape");
  await filters.getByRole("radio", { name: /All books/ }).click();

  const sort = page.getByRole("combobox", { name: "Sort by", exact: true });
  await sort.selectOption("title");
  await page.getByRole("button", { name: "Sort by status" }).click();
  await expect(sort).toHaveValue("status");
  await expect(page.locator(".library-table .book-title")).toHaveText([
    "Copper Gardens",
    "Atlas for Tomorrow",
    "Tide Lighthouse",
  ]);
  await sort.selectOption("model");
  await expect(page.locator(".library-table .book-title")).toHaveText([
    "Copper Gardens",
    "Tide Lighthouse",
    "Atlas for Tomorrow",
  ]);
  const importButton = page.getByRole("button", { name: "Import EPUBs", exact: true });
  await importButton.focus();
  await expect(importButton).toBeFocused();
  await sort.selectOption("recent");
  await page.getByRole("radio", { name: "Cards" }).click();
  await expect(page.locator(".book-card")).toHaveCount(3);
  await page.getByRole("radio", { name: "Table" }).click();
  await expectNoOverflow(page);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await page.screenshot({ path: resolve(output, "mobile.png"), fullPage: true });
  const primary = page.getByRole("navigation", { name: "Primary navigation" });
  await expect(primary).toBeHidden();
  await page.getByRole("button", { name: "Menu", exact: true }).click();
  await expect(primary).toBeVisible();
  await expect(primary.getByRole("link", { name: "Library" })).toHaveAttribute("aria-current", "page");
  for (const link of await primary.getByRole("link").all())
    expect((await link.boundingBox())?.height).toBeGreaterThanOrEqual(40);
  await primary.getByRole("link", { name: "Settings", exact: true }).click();
  await expect(page.locator("#main-content")).toBeFocused();
  await expect(primary).toBeHidden();

  await page.setViewportSize({ width: 1440, height: 1040 });
  await page.goto(`${base}/#project/demo`);
  await expect(page.getByText(source, { exact: true })).toBeVisible();
  // Formatting codes are shown as formatting, never as raw text, in the source column.
  await expect(page.getByText("hundred and twelve", { exact: true })).toHaveClass(/inline-format/);
  await expect(page.locator(".source-text").getByText("⟦t0⟧")).toHaveCount(0);
  await expectNoOverflow(page);

  await page.setViewportSize({ width: 390, height: 844 });
  const tabs = page.getByRole("tablist", { name: "Book navigation" });
  await expect(tabs.getByRole("tab", { name: "Translation" })).toHaveAttribute("aria-selected", "true");
  for (const control of await tabs.getByRole("tab").all())
    expect((await control.boundingBox())?.height).toBeGreaterThanOrEqual(40);
  await expect(page.locator(".mobile-column-label", { hasText: "Source · EN" }).first()).toBeVisible();
  await expect(page.locator(".mobile-column-label", { hasText: "Translation · FR" }).first()).toBeVisible();
  const mobileTranslation = page.getByRole("textbox", { name: "Translation passage 1 unit 1" });
  await mobileTranslation.fill(`${translation} Mobile review.`);
  const mobileSegment = mobileTranslation.locator("xpath=ancestor::article");
  await mobileSegment.getByRole("button", { name: "Save", exact: true }).click();
  await expect(mobileTranslation).toHaveValue(`${translation} Mobile review.`);
  await mobileSegment.scrollIntoViewIfNeeded();
  await page.screenshot({ path: resolve(output, "editor-mobile.png") });
  await mobileSegment.getByRole("button", { name: "Context / history / Ask AI" }).click();
  const mobileInspector = page.getByRole("dialog", { name: "Passage inspector" });
  await expect(mobileInspector).toBeVisible();
  expect((await mobileInspector.boundingBox())?.width).toBeLessThanOrEqual(390);
  await page.getByRole("button", { name: "Close inspector" }).click();
  await page.getByRole("button", { name: "Preview", exact: true }).click();
  const mobilePreview = page.getByRole("dialog", { name: "Chapter preview" });
  await expect(mobilePreview).toBeVisible();
  expect((await mobilePreview.boundingBox())?.width).toBeLessThanOrEqual(390);
  await mobilePreview.getByRole("button", { name: "Close" }).click();
  await tabs.getByRole("tab", { name: /Validations/ }).click();
  await expect(page.getByRole("heading", { name: "Translation validations" })).toBeVisible();
  await tabs.getByRole("tab", { name: "Translation" }).click();
  await expect(mobileTranslation).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);

  await page.setViewportSize({ width: 1440, height: 1040 });
  await expect(page.getByRole("progressbar")).toHaveCount(1);
  for (const [button, detail] of [
    ["Stage 1: Import", "EPUB imported and structure loaded."],
    ["Stage 2: Analysis & memory", "24/24 passages analyzed · 4/4 sections synthesized."],
    ["Stage 3: Translation", "18/24 passages translated · 0 retained in the original."],
    ["Stage 4: Review", "16/24 passages reviewed · 0 resolved · 1 to review."],
    ["Stage 5: Export", "Resolve the remaining alerts before the final export."],
  ]) {
    await page.getByRole("button", { name: button, exact: true }).click();
    await expect(page.getByText(detail, { exact: true })).toBeVisible();
    await expect(page.getByRole("progressbar")).toHaveCount(1);
  }
  await page.getByRole("button", { name: "Follow the active stage" }).click();
  await expect(page.getByText("18/24 passages translated · 0 retained in the original.", { exact: true })).toBeVisible();
  await page.screenshot({ path: resolve(output, "progress-stages.png") });
  await page.screenshot({ path: resolve(output, "editor.png") });

  const inspectorTrigger = page.getByRole("button", { name: "Context / history / Ask AI", exact: true }).first();
  await inspectorTrigger.click();
  await expect(page.getByRole("button", { name: "Close inspector", exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(inspectorTrigger).toBeFocused();

  await chooseTheme(page, "Dark");
  await page.getByRole("tab", { name: /Validations/ }).click();
  await expect(page.getByText("AI opinion", { exact: true })).toBeVisible();
  await page.screenshot({ path: resolve(output, "validations.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await expect(page.getByRole("tab", { name: /Validations/ })).toHaveAttribute("aria-selected", "true");
  await page.screenshot({ path: resolve(output, "validations-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1040 });
  await chooseTheme(page, "Light");
  await page.screenshot({ path: resolve(output, "validations-light.png"), fullPage: true });

  await page.getByRole("button", { name: "Export", exact: true }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("menuitem", { name: "Translated EPUB", exact: true }).click();
  await download;
  await expect(page.getByText("File received; download sent to the browser.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Translation validations" })).toBeVisible();
  expect([...languages]).toEqual(["en"]);
  expect(errors).toEqual([]);
});
