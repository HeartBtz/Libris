import { expect, test } from "@playwright/test";
import { projectProgress } from "../src/features/progress";
import type { Project } from "../src/types";

test("a slow answer for the previous chapter never replaces the current one", async ({
  page,
}) => {
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  const stats = {
    total: 1,
    translated: 1,
    validated: 0,
    reviewed_segments: 0,
    review_total: 1,
    flagged: 0,
    errors: 0,
    refused: 0,
    retained_source: 0,
    analyzed_segments: 1,
    synthesized_chapters: 1,
    chapters: 1,
    glossary: 0,
  };
  const book: Project = {
    id: "demo",
    owner_id: "demo-user",
    title: "Tide Lighthouse",
    author: "Demo",
    series_name: "",
    volume_number: null,
    archived_at: null,
    source_language: "en",
    target_language: "fr",
    provider_id: "local",
    quality: "high",
    context_backend: "internal",
    instructions: "",
    status: "translating",
    stats,
    updated_at: 1789254000,
    book_info: { words: 100, images: 0, size: 1000 },
    bible: {},
  };
  const segment = (chapter: string, text: string) => ({
    id: `segment-${chapter}`,
    project_id: "demo",
    chapter_id: chapter,
    position: 0,
    section: "One",
    source: text,
    translation: "",
    units: [{ id: "u1", text }],
    translated_units: [],
    status: "pending",
    stage: "pending",
    human: false,
    validated: false,
    revision: 0,
    retained_source: false,
    instructions: "",
    error: "",
    uncertainties: [],
    critique: [],
  });
  let releaseSlow: () => void = () => {};
  const slow = new Promise<void>((resolve) => (releaseSlow = resolve));
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
      data = { id: "demo-user", username: "Demo", admin: true };
    else if (path === "/api/projects/demo")
      data = { ...book, progress: projectProgress(book) };
    else if (path.endsWith("/chapters"))
      data = ["first", "second"].map((id, position) => ({
        id,
        title: `Chapter ${id}`,
        position,
        resource: `${id}.xhtml`,
        instructions: "",
        analyzed: true,
      }));
    else if (path.endsWith("/segments")) {
      const chapter = url.searchParams.get("chapter_id")!;
      if (chapter === "first") await slow; // the first chapter answers late
      data = [segment(chapter, `Source text of the ${chapter} chapter.`)];
    }
    await route.fulfill({ json: data });
  });
  await page.goto(
    `${process.env.SHOWCASE_URL || "http://127.0.0.1:4173"}/#project/demo`,
  );
  await page
    .locator(".chapter-list")
    .getByRole("button", { name: /Chapter second/ })
    .click();
  await expect(
    page.getByText("Source text of the second chapter."),
  ).toBeVisible();
  // Record any appearance of the late chapter, even a transient one repaired by the next refresh.
  await page.evaluate(() => {
    const state = window as unknown as { staleSeen: boolean };
    state.staleSeen = false;
    new MutationObserver(() => {
      if (document.body.innerText.includes("Source text of the first chapter."))
        state.staleSeen = true;
    }).observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  });
  const late = page.waitForResponse(
    (response) =>
      new URL(response.url()).searchParams.get("chapter_id") === "first",
  );
  releaseSlow();
  await late;
  await page.waitForTimeout(300);
  expect(
    await page.evaluate(
      () => (window as unknown as { staleSeen: boolean }).staleSeen,
    ),
  ).toBe(false);
  await expect(
    page.getByText("Source text of the second chapter."),
  ).toBeVisible();
});
