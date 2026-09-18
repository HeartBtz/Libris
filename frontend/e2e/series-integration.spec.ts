import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { base, password, username } from "./integration-config";

// Runs the import assistant against a disposable backend. The EPUBs are the synthetic batch
// fixtures of scripts/make_browser_fixtures.py; the TXT chapters are written here.
const fixtures = (process.env.LIBRIS_E2E_FIXTURES || "/tmp/libris").replace(/\/$/, "");
const epub = (label: string, name: string) => ({
  name,
  mimeType: "application/epub+zip",
  buffer: readFileSync(`${fixtures}/batch-${label}.epub`),
});
const chapter = (name: string, text: string) => ({ name, mimeType: "text/plain", buffer: Buffer.from(text, "utf8") });

async function openAssistant(page: Page, format: RegExp) {
  await page.getByRole("button", { name: "Add content", exact: true }).first().click();
  const dialog = page.getByRole("dialog", { name: "Add content" });
  await dialog.getByRole("radio", { name: format }).check();
  await dialog.getByRole("button", { name: "Continue" }).click();
  return dialog;
}

test("@integration series, webnovel and standalone imports through the assistant", async ({ page }) => {
  test.setTimeout(180_000);
  const suffix = Date.now().toString(36);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  await page.goto(base);
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Library", exact: true })).toBeVisible();
  const created: string[] = [];
  const series: string[] = [];
  try {
    // 1. Three EPUB volumes of one new series; the name comes from the files.
    let dialog = await openAssistant(page, /EPUB books/);
    await dialog.getByRole("radio", { name: /New series/ }).check();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await dialog.locator('input[type="file"]').setInputFiles([
      epub("b", `Saga ${suffix} - Volume 2.epub`),
      epub("a", `Saga ${suffix} - Volume 1.epub`),
      epub("c", `Saga ${suffix} - Volume 3.epub`),
    ]);
    await expect(dialog.getByText("3/3 files analyzed")).toBeVisible();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await expect(dialog.getByRole("textbox", { name: "Series name" })).toHaveValue(new RegExp(`Saga ${suffix}`, "i"));
    for (const volume of [1, 2, 3])
      await expect(
        dialog.getByRole("textbox", { name: `Volume number of Saga ${suffix} - Volume ${volume}.epub` }),
      ).toHaveValue(String(volume));
    await dialog.getByRole("button", { name: "Continue" }).click();
    const committed = page.waitForResponse((r) => r.url().endsWith("/commit") && r.request().method() === "POST");
    await dialog.getByRole("button", { name: "Import only" }).click();
    const result = await (await committed).json();
    expect(result.projects).toHaveLength(3);
    created.push(...result.projects.map((p: { id: string }) => p.id));
    series.push(result.series_id);
    await expect(dialog.getByText("Import complete.")).toBeVisible();
    await dialog.getByRole("button", { name: "Open the series" }).click();
    await expect(page.getByRole("heading", { name: new RegExp(`Saga ${suffix}`, "i"), level: 1 })).toBeVisible();
    await page.getByRole("tab", { name: /Volumes/ }).click();
    await expect(page.locator(".volume-list > li")).toHaveCount(3);

    // 2. Webnovel chapters: a series is mandatory, an unnumbered file needs a confirmation.
    await page.goto(`${base}/#library`);
    dialog = await openAssistant(page, /TXT chapters/);
    await dialog.getByRole("radio", { name: /New series/ }).check();
    await dialog.getByLabel("Series name").fill(`Canal ${suffix}`);
    await expect(dialog.getByRole("radio", { name: /Continuous flow of the series/ })).toBeChecked();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await dialog.locator('input[type="file"]').setInputFiles([
      chapter("Chapter 2.txt", "Chapter 2\n\nKai crossed the second bridge at dawn.\n"),
      chapter("Chapter 10.txt", "Chapter 10\n\nThe bells rang for the tenth morning.\n"),
      chapter("Chapter 1.txt", "Chapter 1\n\nKai walked along the canal.\n"),
      chapter("interlude.txt", "An unnumbered interlude about the keeper.\n"),
    ]);
    await expect(dialog.getByText("4/4 files analyzed")).toBeVisible();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await expect(dialog.getByText(/Missing numbers: 3, 4, 5, 6, 7, 8, 9/)).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Continue" })).toBeDisabled();
    await dialog.getByRole("checkbox", { name: "Confirm: no number" }).check();
    await dialog.getByRole("button", { name: "Continue" }).click();
    const chapters = page.waitForResponse((r) => r.url().endsWith("/commit") && r.request().method() === "POST");
    await dialog.getByRole("button", { name: "Import only" }).click();
    const webnovel = await (await chapters).json();
    expect(webnovel.chapters.created).toBe(4);
    created.push(webnovel.projects[0].id);
    series.push(webnovel.series_id);
    await dialog.getByRole("button", { name: "Open the series" }).click();
    await page.getByRole("tab", { name: /Chapters/ }).click();
    await expect(page.locator(".chapter-list > li")).toHaveCount(4);
    await expect(page.locator(".chapter-list .book-title").first()).toHaveText(/Chapter 1/);

    // 3. The same chapters again: identical ones are recognized, nothing is duplicated.
    await page.getByRole("button", { name: "Add content", exact: true }).first().click();
    dialog = page.getByRole("dialog", { name: "Add content" });
    await dialog.getByRole("radio", { name: /TXT chapters/ }).check();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await expect(dialog.getByRole("radio", { name: /Existing series/ })).toBeChecked();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await dialog.locator('input[type="file"]').setInputFiles([
      chapter("Chapter 1.txt", "Chapter 1\n\nKai walked along the canal.\n"),
      chapter("Chapter 2.txt", "Chapter 2\n\nKai crossed the second bridge at noon.\n"),
    ]);
    await expect(dialog.getByText("2/2 files analyzed")).toBeVisible();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await expect(dialog.getByText("Identical to the existing chapter: skipped.")).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Continue" })).toBeDisabled();
    await dialog.getByRole("checkbox", { name: /Replace the existing chapter/ }).check();
    await dialog.getByRole("button", { name: "Continue" }).click();
    const again = page.waitForResponse((r) => r.url().endsWith("/commit") && r.request().method() === "POST");
    await dialog.getByRole("button", { name: "Import only" }).click();
    expect((await (await again).json()).chapters).toMatchObject({ created: 0, unchanged: 1, replaced: 1 });
    await dialog.getByRole("button", { name: "Close" }).first().click();

    // 4. A file already in the library is set aside; detaching a volume makes it standalone.
    await page.goto(`${base}/#library`);
    dialog = await openAssistant(page, /EPUB books/);
    await dialog.getByRole("radio", { name: /Standalone volume/ }).check();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await dialog.locator('input[type="file"]').setInputFiles([epub("c", `Atlas ${suffix}.epub`)]);
    await expect(dialog.getByText("1/1 files analyzed")).toBeVisible();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await expect(dialog.getByRole("region", { name: "Excluded files" })).toContainText("Already in your library");
    await dialog.getByRole("button", { name: "Abandon", exact: true }).click();
    await page.getByRole("dialog", { name: "Abandon this import?" }).getByRole("button", { name: "Abandon the import" }).click();
    await expect(dialog).toBeHidden();
    await page.goto(`${base}/#series/${result.series_id}`);
    await page.getByRole("tab", { name: /Volumes/ }).click();
    const third = result.projects.find((p: { volume_number: number }) => p.volume_number === 3);
    await page.getByRole("button", { name: `Actions for ${third.title}` }).click();
    await page.getByRole("menuitem", { name: "Detach from the series" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Detach" }).click();
    await expect(page.locator(".volume-list > li")).toHaveCount(2);
    await page.goto(`${base}/#library`);
    const standalone = page.getByRole("region", { name: /Standalone volumes/ });
    await expect(standalone.getByRole("link", { name: third.title, exact: true })).toBeVisible();
  } finally {
    for (const id of created) await page.request.delete(`${base}/api/projects/${id}?stop_jobs=true`);
    for (const id of series) await page.request.delete(`${base}/api/series/${id}`);
  }
  expect(errors).toEqual([]);
});
