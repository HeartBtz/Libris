import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import type { Project, Series } from "../src/types";

const base = process.env.SHOWCASE_URL || "http://127.0.0.1:4173";

function volume(id: string, title: string, volume_number: number): Project {
  return {
    id,
    owner_id: "demo-user",
    title,
    author: "Demo collection",
    series_id: "tide",
    series_name: "Tide Chronicles",
    volume_number,
    source_format: "epub",
    archived_at: null,
    source_language: "en",
    target_language: "fr",
    provider_id: "local",
    quality: "normal",
    context_backend: "internal",
    instructions: "",
    status: "completed",
    stats: {
      total: 3, translated: 3, validated: 1, reviewed_segments: 3, review_total: 3, flagged: 1, errors: 0,
      refused: 0, retained_source: 1, analyzed_segments: 3, synthesized_chapters: 2, chapters: 2, glossary: 0,
    },
    updated_at: 1789254000,
    book_info: { words: 60, images: 0, size: 100 },
    bible: { summary: "Mara returns to the lighthouse." },
  } as Project;
}

const tide = {
  id: "tide", owner_id: "demo-user", name: "Tide Chronicles", kind: "books", authors: ["Demo collection"],
  source_language: "en", target_language: "fr", provider_id: "local", quality: "normal", context_backend: "internal",
  instructions: "", bible_validated: false, archived_at: null, created_at: 1789167600, updated_at: 1789254000,
  shared: false, volumes: 2, serial: false, chapters: 4, formats: ["epub"],
  progress: { total: 6, translated: 6, validated: 1, percent: 100, running: 0 },
  issues: { flagged: 1, errors: 0, context_stale: 0 }, activity: 1789254000,
  providers: [{ id: "local", name: "Local model", model: "zeta-model" }],
  memory: { backends: ["internal"], pending: 0, failed: 0 }, missing_volumes: [], duplicate_volumes: [],
} as Series;

const chapters = [
  { id: "c1", title: "Chapter 1 · The Return", position: 0, resource: "c1.xhtml", instructions: "", analyzed: true },
  { id: "c2", title: "Chapter 2 · Low Tide", position: 1, resource: "c2.xhtml", instructions: "", analyzed: true },
];

function segment(position: number, chapter_id: string, extra: object = {}) {
  const text = `Synthetic source passage number ${position + 1}, about the lighthouse and the tide.`;
  return {
    id: `s${position}`, project_id: "one", chapter_id, position, section: "", source: text, translation: `Passage ${position + 1}`,
    units: [{ id: "u1", text }], translated_units: [{ id: "u1", text: `Passage ${position + 1}` }], status: "ok",
    stage: "done", human: false, validated: false, revision: 1, retained_source: false, instructions: "", error: "",
    uncertainties: [] as string[], critique: [] as object[], ...extra,
  };
}

const signals = {
  retained: [{ code: "source_retained", count: 1, penalty: 60 }],
  doubts: [
    { code: "critique", count: 2, penalty: 20 },
    { code: "doubt", count: 3, penalty: 15 },
  ],
};

const summary = {
  scored: 3, average: 68.3, minimum: 40, bands: { good: 1, fair: 0, weak: 1, poor: 1 },
  histogram: [0, 0, 0, 0, 1, 0, 1, 0, 0, 1], to_review: 2, review_below: 70,
};

function review(project_id: string, project_title: string) {
  return [
    { segment_id: "s2", project_id, project_title, chapter_id: "c2", chapter_title: "Chapter 2 · Low Tide",
      position: 2, score: 40, band: "poor", signals: signals.retained, excerpt: "Synthetic source passage number 3" },
    { segment_id: "s1", project_id, project_title, chapter_id: "c1", chapter_title: "Chapter 1 · The Return",
      position: 1, score: 65, band: "weak", signals: signals.doubts, excerpt: "Synthetic source passage number 2" },
  ];
}

function chapterRows(project_id: string, project_title: string) {
  return [
    { chapter_id: "c2", project_id, project_title, volume_number: 1, title: "Chapter 2 · Low Tide", position: 1,
      passages: 1, scored: 1, average: 40, minimum: 40, weak: 1 },
    { chapter_id: "c1", project_id, project_title, volume_number: 1, title: "Chapter 1 · The Return", position: 0,
      passages: 2, scored: 2, average: 82.5, minimum: 65, weak: 1 },
  ];
}

/** A dashboard card, found by its heading. */
function card(page: Page, name: string) {
  return page.locator("section.card").filter({ has: page.getByRole("heading", { name, exact: true }) });
}

async function mockServer(page: Page) {
  const projects = { one: volume("one", "Tide Lighthouse", 1), two: volume("two", "Tide Harbour", 2) };
  const segments = [segment(0, "c1", { validated: true, human: true }), segment(1, "c1"), segment(2, "c2", {
    retained_source: true, status: "source_retained" })];  // prettier-ignore
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/events")) {
      await route.fulfill({ contentType: "text/event-stream", body: ": demo\n\n" });
      return;
    }
    let data: unknown = [];
    if (path.endsWith("/auth/me")) data = { id: "demo-user", username: "Démo", admin: true };
    else if (path === "/api/projects") data = Object.values(projects);
    else if (path === "/api/projects/one") data = projects.one;
    else if (path === "/api/projects/one/quality")
      data = { project_id: "one", summary, chapters: chapterRows("one", "Tide Lighthouse"), chapters_total: 2,
               review_first: review("one", "Tide Lighthouse") };  // prettier-ignore
    else if (path === "/api/projects/one/quality/passages") {
      const chapter = url.searchParams.get("chapter_id");
      data =
        chapter === "c2"
          ? { s2: { score: 40, band: "poor", signals: signals.retained } }
          : { s0: { score: 100, band: "good", signals: [] }, s1: { score: 65, band: "weak", signals: signals.doubts } };
    } else if (path === "/api/series/tide") data = { ...tide, bible: {}, volume_list: Object.values(projects) };
    else if (path === "/api/series/tide/quality")
      data = {
        series_id: "tide", summary, chapters: chapterRows("one", "Tide Lighthouse"), chapters_total: 2,
        review_first: review("one", "Tide Lighthouse"),
        volumes: [
          { project_id: "one", title: "Tide Lighthouse", volume_number: 1, scored: 3, average: 68.3, minimum: 40, weak: 2 },
          { project_id: "two", title: "Tide Harbour", volume_number: 2, scored: 0, average: null, minimum: null, weak: 0 },
        ],
      };
    else if (/^\/api\/segments\/s\d$/.test(path)) data = segments.find((item) => path.endsWith(`/${item.id}`));
    else if (path.endsWith("/chapters")) data = chapters;
    else if (path.endsWith("/segments")) {
      const chapter = url.searchParams.get("chapter_id");
      const status = url.searchParams.get("status");
      data = segments.filter((item) => item.chapter_id === chapter && (!status || item.status === status));
    } else if (path === "/api/providers") data = [{ id: "local", kind: "openai", name: "Local model", model: "zeta" }];
    else if (path.endsWith("/completion"))
      data = { total: 3, translated: 2, missing: 0, retained: 1, coverage_complete: false, flagged: 0, issues: 0,
               protected: 1, processing: false, last_job_status: "completed", recovery_total: 0, recovery: [],
               quality: summary };  // prettier-ignore
    await route.fulfill({ json: data });
  });
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
}

test("the quality tab ranks passages and chapters and opens the weakest in the editor", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await mockServer(page);
  await page.goto(`${base}/#project/one`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Tide Lighthouse");
  // The editor shows each passage's score, with the band named for screen readers.
  await expect(page.locator("#segment-s1").getByText("Quality score 65 Weak")).toBeVisible();

  await page.getByRole("tab", { name: "Quality", exact: true }).click();
  const scores = card(page, "Passage quality score");
  await expect(scores.getByText("68.3", { exact: true })).toBeVisible();
  await expect(scores.getByText("Below 70")).toBeVisible();
  await expect(scores.getByRole("img", { name: "Score distribution" })).toBeVisible();
  await expect(scores.getByRole("listitem").filter({ hasText: "Poor" })).toContainText("1");

  const reviewFirst = card(page, "Review these first");
  const items = reviewFirst.getByRole("listitem").filter({ has: page.getByRole("button") });
  await expect(items).toHaveCount(2);
  await expect(items.first()).toContainText("Chapter 2 · Low Tide · § 3");
  await expect(items.first()).toContainText("Source retained");
  await expect(items.nth(1)).toContainText("Open review critique ×2");

  const ranking = card(page, "Chapters, weakest first").getByRole("row");
  await expect(ranking.nth(1)).toContainText("Chapter 2 · Low Tide");
  await expect(ranking.nth(2)).toContainText("82.5");
  await page.screenshot({ path: "test-results/quality-dashboard.png", fullPage: true });

  await page.setViewportSize({ width: 360, height: 800 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(360);
  const open = reviewFirst.getByRole("button", { name: "Open passage 3 in the editor" });
  expect((await open.boundingBox())?.height).toBeGreaterThanOrEqual(40);
  await open.click();
  await expect(page.getByRole("tab", { name: "Translation", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#segment-s2")).toBeVisible();
  await expect(page.locator("#segment-s2").getByText("Quality score 40 Poor")).toBeVisible();

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.getByRole("tab", { name: "Summary & recovery", exact: true }).click();
  await expect(card(page, "Passage quality").getByText("68.3")).toBeVisible();
  expect(errors).toEqual([]);
});

test("the series quality tab covers its volumes and links each passage to its book", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await mockServer(page);
  await page.goto(`${base}/#series/tide`);
  await page.getByRole("tab", { name: "Quality", exact: true }).click();
  const volumes = card(page, "Volumes of the series");
  await expect(volumes.getByRole("link", { name: "Tide Harbour" })).toBeVisible();
  await expect(volumes).toContainText("No score yet");
  await expect(volumes).toContainText("3 scored passages");
  const reviewFirst = card(page, "Review these first");
  await expect(reviewFirst).toContainText("Tide Lighthouse · Chapter 2 · Low Tide · § 3");
  await reviewFirst.getByRole("button", { name: "Open passage 3 in the editor" }).click();
  await expect(page).toHaveURL(/#project\/one\/passage\/s2$/);
  await expect(page.locator("#segment-s2")).toBeVisible();
  expect(errors).toEqual([]);
});
