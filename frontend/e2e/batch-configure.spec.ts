import { expect, test } from "@playwright/test";
import { projectProgress } from "../src/features/progress";
import type { Project } from "../src/types";

test("batch configuration keeps each book's language and quality unless asked", async ({
  page,
}) => {
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  const stats = {
    total: 10,
    translated: 0,
    validated: 0,
    reviewed_segments: 0,
    review_total: 10,
    flagged: 0,
    errors: 0,
    refused: 0,
    retained_source: 0,
    analyzed_segments: 0,
    synthesized_chapters: 0,
    chapters: 2,
    glossary: 0,
  };
  const base: Project = {
    id: "german",
    owner_id: "demo-user",
    title: "German Edition",
    author: "Author",
    series_name: "",
    volume_number: null,
    archived_at: null,
    source_language: "en",
    target_language: "de",
    provider_id: "old",
    quality: "fast",
    context_backend: "internal",
    instructions: "Keep honorifics.",
    status: "ready",
    stats,
    updated_at: 1789254000,
    book_info: { words: 1000, images: 0, size: 1000 },
    bible: {},
  };
  const books = [
    base,
    {
      ...base,
      id: "spanish",
      title: "Spanish Edition",
      target_language: "es",
      quality: "normal",
    },
  ].map((book) => ({ ...book, progress: projectProgress(book) }));
  const saved: Record<string, Record<string, unknown>> = {};
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let data: unknown = [];
    if (request.method() === "PUT") {
      saved[path.split("/").pop()!] = request.postDataJSON();
      data = {};
    } else if (path.endsWith("/auth/me"))
      data = { id: "demo-user", username: "Demo", admin: true };
    else if (path === "/api/projects") data = books;
    else if (path === "/api/providers")
      data = [{ id: "new", name: "New provider", model: "m" }];
    await route.fulfill({ json: data });
  });
  await page.goto(process.env.SHOWCASE_URL || "http://127.0.0.1:4173");
  await page.getByRole("checkbox", { name: "Select German Edition" }).check();
  await page.getByRole("checkbox", { name: "Select Spanish Edition" }).check();
  await page.getByLabel("Common provider").selectOption("new");
  await expect(page.getByLabel("Target language")).toHaveValue("");
  await expect(page.getByLabel("Quality")).toHaveValue("");
  await page.getByRole("button", { name: "Configure selection" }).click();
  await expect(page.getByRole("status").last()).toContainText(
    "Spanish Edition",
  );
  expect(saved.german).toMatchObject({
    provider_id: "new",
    target_language: "de",
    quality: "fast",
    instructions: "Keep honorifics.",
  });
  expect(saved.spanish).toMatchObject({
    provider_id: "new",
    target_language: "es",
    quality: "normal",
  });

  await page.getByLabel("Target language").fill("it");
  await page.getByLabel("Quality").selectOption("maximum");
  await page.getByRole("button", { name: "Configure selection" }).click();
  await expect.poll(() => saved.spanish.target_language).toBe("it");
  expect(saved.german).toMatchObject({
    target_language: "it",
    quality: "maximum",
  });
});
