import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

// One TXT file holding several chapters: the import assistant proposes a split at its headings.
const PARTS = [
  { start: 0, number: 0, number_from_heading: false, title: "", heading: false, first_line: "The Glass Road",
    characters: 40, excerpt: "The Glass Road A synthetic serial.", checksum: "a" },
  { start: 4, number: 1, number_from_heading: true, title: "Chapter 1: The Start", heading: true,
    first_line: "Chapter 1: The Start", characters: 120, excerpt: "Alice woke up early.", checksum: "b" },
  { start: 9, number: 2, number_from_heading: true, title: "Chapter 2: The Storm", heading: true,
    first_line: "Chapter 2: The Storm", characters: 80, excerpt: "Rain fell on the glass road.", checksum: "c" },
  { start: 14, number: 3, number_from_heading: true, title: "Chapter 3", heading: true,
    first_line: "Chapter 3", characters: 60, excerpt: "The keeper opened the gate.", checksum: "d" },
];
const FILE = {
  index: 0, name: "novel.txt", size: 400, sha256: "f", duplicate: null, format: "txt", title: "novel",
  author: "", language: "", series: "", series_index: null, chapter_number: null, number_confidence: "low",
  number_reason: "aucun numéro trouvé", warnings: [], errors: [],
  meta: {
    first_line: "The Glass Road",
    split: { unit: "line", confidence: "high", reason: "chapter heading lines", default: true, warnings: [], parts: PARTS },
  },
};

async function mockImport(page: Page, commits: unknown[]) {
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const session = { id: "session", format: "txt", files: [FILE], expires_at: 1789999999, result: null,
                      confirm_low_confidence: false };
    if (path === "/api/imports" && request.method() === "POST")
      await route.fulfill({ status: 201, json: { ...session, files: [] } });
    else if (path === "/api/imports/session/files") await route.fulfill({ status: 201, json: FILE });
    else if (path === "/api/imports/session/commit") {
      commits.push(request.postDataJSON());
      await route.fulfill({
        json: {
          series_id: "s1", series_name: "Glass", projects: [{ id: "p1", title: "Glass", volume_number: null, status: "created" }],
          chapters: { created: 3, unchanged: 0, replaced: 0, items: [] }, decisions: [], jobs: [], warnings: [], views: [],
        },
      });
    } else if (path === "/api/imports/session")
      await route.fulfill({
        json: {
          ...session,
          proposal: {
            items: [{ index: 0, title: "novel", chapter_number: null, confidence: "low", reason: "", existing_chapter: null }],
            duplicate_numbers: [],
            missing_numbers: [],
          },
        },
      });
    else if (path.endsWith("/auth/me")) await route.fulfill({ json: { id: "demo-user", username: "Demo", admin: true } });
    else await route.fulfill({ json: [] });
  });
}

async function openReview(page: Page) {
  await page.goto(process.env.SHOWCASE_URL || "http://127.0.0.1:4173");
  await page.getByRole("button", { name: "Add content" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Add content" });
  await dialog.getByRole("radio", { name: /TXT chapters/ }).check();
  await dialog.getByRole("button", { name: "Continue" }).click();
  await dialog.getByRole("radio", { name: /New series/ }).check();
  await dialog.getByLabel("Series name").fill("Glass");
  await dialog.getByRole("button", { name: "Continue" }).click();
  await dialog.locator('input[type="file"][accept=".txt"]').setInputFiles({
    name: "novel.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("The Glass Road\n\nChapter 1: The Start\n\nAlice.\n"),
  });
  await expect(dialog.getByText("1/1 files analyzed")).toBeVisible();
  await dialog.getByRole("button", { name: "Continue" }).click();
  return dialog;
}

test("a file holding several chapters is split, renamed and merged before the import", async ({ page }) => {
  const commits: { items: { split?: unknown }[] }[] = [];
  await mockImport(page, commits);
  const dialog = await openReview(page);
  const toggle = dialog.getByRole("checkbox", { name: "Split novel.txt into chapters" });
  await expect(toggle).toBeChecked();
  await expect(dialog.getByText("Split into 4 chapters")).toBeVisible();
  const list = dialog.getByRole("list", { name: "Chapters of the file novel.txt" });
  await expect(list.getByRole("listitem")).toHaveCount(4);
  // Text before the first heading is the front matter; its title is editable like the others.
  await expect(dialog.getByRole("textbox", { name: "Title of chapter 1" })).toHaveValue("Front matter");
  await dialog.getByRole("textbox", { name: "Title of chapter 4" }).fill("Chapter 3: The Keeper");
  await dialog.getByRole("textbox", { name: "Number of chapter 2" }).fill("10");
  await dialog.getByRole("textbox", { name: "Number of chapter 3" }).fill("11");
  await dialog.getByRole("textbox", { name: "Number of chapter 4" }).fill("12");
  // Merge chapter 2 (The Storm) into chapter 1, then undo and redo it.
  await dialog.getByRole("button", { name: "Merge chapter 3 with the previous one" }).click();
  await expect(dialog.getByText("Split into 3 chapters")).toBeVisible();
  await expect(list.getByText("Merged with the previous chapter.")).toBeVisible();
  await list.getByRole("button", { name: /Restore the break/ }).click();
  await expect(dialog.getByText("Split into 4 chapters")).toBeVisible();
  await dialog.getByRole("button", { name: "Merge chapter 3 with the previous one" }).click();
  // Turning the split off keeps the file as one chapter; turning it on again keeps the edits.
  await toggle.uncheck();
  await expect(list).toBeHidden();
  await toggle.check();
  await expect(dialog.getByText("Split into 3 chapters")).toBeVisible();
  await dialog.getByRole("button", { name: "Continue" }).click();
  await expect(dialog.locator(".summary-list")).toContainText("Chapters created3");
  await dialog.getByRole("button", { name: "Import only" }).click();
  await expect(dialog.getByText("Import complete.")).toBeVisible();
  expect(commits).toHaveLength(1);
  expect(commits[0].items[0].split).toEqual([
    { start: 0, number: 0, title: "Front matter" },
    { start: 4, number: 10, title: "Chapter 1: The Start" },
    { start: 14, number: 12, title: "Chapter 3: The Keeper" },
  ]);
});

test("the split preview fits a phone screen", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await mockImport(page, []);
  const dialog = await openReview(page);
  await expect(dialog.getByRole("list", { name: "Chapters of the file novel.txt" })).toBeVisible();
  const merge = dialog.getByRole("button", { name: "Merge chapter 2 with the previous one" });
  expect((await merge.boundingBox())!.height).toBeGreaterThanOrEqual(40);
  const overflow = await dialog.evaluate((element) => {
    const body = element.querySelector(".dialog-body") || element;
    return body.scrollWidth - body.clientWidth;
  });
  expect(overflow).toBeLessThanOrEqual(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
});
