import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { projectProgress } from "../src/features/progress";
import type { Project } from "../src/types";

const base = process.env.SHOWCASE_URL || "http://127.0.0.1:4173";

const stats = {
  total: 1,
  translated: 1,
  validated: 0,
  reviewed_segments: 1,
  review_total: 1,
  flagged: 1,
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
  status: "ready",
  stats,
  updated_at: 1789254000,
  book_info: { words: 100, images: 0, size: 1000 },
  bible: { summary: "Synthetic" },
};
const source = "She climbed the ⟦t0⟧hundred and twelve⟦/t0⟧ steps.⟦x1⟧";
const translation = "Elle gravit les ⟦t0⟧cent douze⟦/t0⟧ marches.⟦x1⟧";
const segment = {
  id: "segment",
  project_id: "demo",
  chapter_id: "chapter",
  position: 0,
  section: "One",
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
      description: "Rhythm.",
      suggestion: "Elle monta les ⟦t0⟧cent douze⟦/t0⟧ marches.⟦x1⟧",
    },
  ],
};

async function open(page: Page, calls: { method: string; path: string; body: unknown }[]) {
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (request.method() !== "GET") calls.push({ method: request.method(), path, body: request.postDataJSON() });
    if (path.endsWith("/events")) return route.fulfill({ contentType: "text/event-stream", body: ": demo\n\n" });
    if (path === "/api/segments/segment" && request.method() === "PUT")
      return route.fulfill({ json: { ...segment, revision: 1, human: true } });
    if (path === "/api/projects/demo/estimate")
      return route.fulfill({
        json: {
          input_tokens: 900000,
          output_tokens: 300000,
          requests: 40,
          cost: 3.4,
          currency_note: "",
          basis: "",
          passages: 12,
          basis_kind: "history",
          history_books: 2,
          breakdown: [],
        },
      });
    let data: unknown = [];
    if (path.endsWith("/auth/me")) data = { id: "demo-user", username: "Demo", admin: true };
    else if (path === "/api/projects/demo") data = { ...book, progress: projectProgress(book) };
    else if (path.endsWith("/chapters"))
      data = [{ id: "chapter", title: "Chapter 1", position: 0, resource: "c.xhtml", instructions: "", analyzed: true }];
    else if (path.endsWith("/segments")) data = url.searchParams.get("status") === "refused" ? [] : [segment];
    else if (path.endsWith("/final-review"))
      data = { automatic: false, web_enabled: false, eligible: 1, summary: projectProgress(book).review };
    else if (request.method() !== "GET") data = {};
    await route.fulfill({ json: data });
  });
  await page.goto(`${base}/#project/demo`);
}

test("formatting codes reach the API unchanged and drafts are never lost silently", async ({ page }) => {
  const calls: { method: string; path: string; body: unknown }[] = [];
  await open(page, calls);
  const box = page.getByRole("textbox", { name: "Translation passage 1 unit 1" });
  await expect(box).toHaveValue(translation);
  // The source shows formatting, not codes; the editor keeps the exact codes.
  await expect(page.getByText("hundred and twelve", { exact: true })).toHaveClass(/inline-format/);
  // Codes are painted as marks without brackets; hovering one names it.
  const opening = box.locator("xpath=preceding-sibling::div").locator("mark.marker-open");
  await expect(opening).toHaveCSS("color", "rgba(0, 0, 0, 0)");
  const mark = await opening.boundingBox();
  await page.mouse.move(mark!.x + mark!.width / 2, mark!.y + mark!.height / 2);
  await expect(box).toHaveAttribute("title", "Start of book formatting (italic, bold, link…)");
  await box.fill("Elle monta les ⟦t0⟧cent douze⟦/t0⟧ marches.⟦x1⟧");

  // Leaving the editor with a draft asks first; "Stay" keeps the text.
  await page.getByRole("tab", { name: /Validations/ }).click();
  const leave = page.getByRole("dialog", { name: "Unsaved changes" });
  await expect(leave).toBeVisible();
  await leave.getByRole("button", { name: "Stay" }).click();
  await expect(box).toHaveValue("Elle monta les ⟦t0⟧cent douze⟦/t0⟧ marches.⟦x1⟧");

  // Ctrl+S saves with the codes untouched.
  await box.press("Control+s");
  await expect.poll(() => calls.filter((call) => call.method === "PUT").length).toBe(1);
  expect(calls[0].body).toMatchObject({
    revision: 0,
    validated: false,
    units: [{ id: "u1", text: "Elle monta les ⟦t0⟧cent douze⟦/t0⟧ marches.⟦x1⟧" }],
  });
  await expect(page.getByText("Unsaved", { exact: true })).toHaveCount(0);

  // A removed code is flagged before the server refuses it.
  await box.fill("Elle monta les cent douze marches.");
  await expect(page.getByText(/Formatting codes differ from the source/)).toBeVisible();

  // The browser also asks before closing the tab while a draft is pending.
  const beforeUnload = await page.evaluate(() => {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  });
  expect(beforeUnload).toBe(true);

  // "Leave without saving" discards the draft and opens the tab.
  await page.getByRole("tab", { name: /Validations/ }).click();
  await page.getByRole("dialog", { name: "Unsaved changes" }).getByRole("button", { name: "Leave without saving" }).click();
  await expect(page.getByRole("heading", { name: "Translation validations" })).toBeVisible();
});

test("review decisions and paid operations ask for confirmation", async ({ page }) => {
  const calls: { method: string; path: string; body: unknown }[] = [];
  await open(page, calls);
  await page.getByRole("tab", { name: /Validations/ }).click();

  // "Accept all" is no longer applied on a single click.
  await page.getByRole("button", { name: "Accept all" }).click();
  const acceptAll = page.getByRole("dialog", { name: "Accept all proposals?" });
  await acceptAll.getByRole("button", { name: "Cancel" }).click();
  expect(calls.some((call) => call.path.endsWith("/accept-all"))).toBe(false);
  await page.getByRole("button", { name: "Accept all" }).click();
  await page.getByRole("dialog", { name: "Accept all proposals?" }).getByRole("button", { name: "Accept all" }).click();
  await expect.poll(() => calls.some((call) => call.path.endsWith("/critiques/accept-all"))).toBe(true);

  // "Edit" loads the proposal into the translation field for adjustment.
  await page.getByRole("button", { name: "Edit this proposal" }).click();
  await expect(page.getByRole("textbox", { name: "Translation passage 1 unit 1" })).toHaveValue(
    "Elle monta les ⟦t0⟧cent douze⟦/t0⟧ marches.⟦x1⟧",
  );
  await page.getByRole("button", { name: "Validate", exact: true }).click();
  await expect.poll(() => calls.find((call) => call.method === "PUT")?.body).toMatchObject({ validated: true });

  // Starting the AI review shows the server's estimate before anything is queued.
  await page.getByRole("button", { name: "Start AI review" }).click();
  const review = page.getByRole("dialog", { name: "Start the AI review?" });
  await expect(review.getByText("≈ 1.2M tokens · ≈ 3.40 (configured rates)")).toBeVisible();
  await expect(review.getByText("12 remaining passages · based on 2 previous books")).toBeVisible();
  await review.getByRole("button", { name: "Start" }).click();
  await expect
    .poll(() => calls.some((call) => (call.body as { operation?: string } | null)?.operation === "resolve_validations"))
    .toBe(true);
});
