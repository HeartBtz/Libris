import { test, expect } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

// Synthetic content written for documentation. Never connects to a real API.
test("capture public Libris showcase", async ({ page }) => {
  const output = resolve(process.cwd(), "../docs/screenshots");
  mkdirSync(output, { recursive: true });
  const base = process.env.SHOWCASE_URL || "http://127.0.0.1:4173";
  const stats = {
    total: 24,
    translated: 18,
    validated: 12,
    flagged: 1,
    errors: 0,
    refused: 0,
    retained_source: 0,
    analyzed_segments: 24,
    synthesized_chapters: 4,
    chapters: 4,
    glossary: 8,
  };
  const book = {
    id: "demo",
    owner_id: "demo-user",
    title: "Le phare des marées",
    author: "Collection de démonstration",
    source_language: "en",
    target_language: "fr",
    provider_id: "local",
    quality: "high",
    context_backend: "internal",
    instructions: "Préserver une voix narrative sobre et poétique.",
    status: "ready",
    stats,
    updated_at: 1789254000,
    book_info: { words: 18500, images: 2, size: 240000 },
    bible: {},
  };
  const books = [
    book,
    {
      ...book,
      id: "demo-2",
      title: "Les jardins de cuivre",
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
    {
      ...book,
      id: "demo-3",
      title: "Un atlas pour demain",
      source_language: "es",
      status: "completed",
      stats: { ...stats, translated: 24, validated: 24, flagged: 0 },
    },
  ];
  const chapter = {
    id: "chapter",
    title: "Chapitre 1 · Le retour",
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
    section: "Le retour",
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
    await route.fulfill({ json: data });
  });
  await page.setViewportSize({ width: 1440, height: 1040 });
  await page.goto(base);
  await expect(
    page.getByRole("heading", { name: "Bibliothèque", exact: true }),
  ).toBeVisible();
  await page.screenshot({ path: resolve(output, "library.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: resolve(output, "mobile.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1040 });
  await page.goto(`${base}/#project/demo`);
  await expect(page.getByText(source, { exact: true })).toBeVisible();
  await page.screenshot({ path: resolve(output, "editor.png") });
  await page
    .getByRole("button", { name: "Validations (1)", exact: true })
    .click();
  await expect(page.getByText("Avis de l’IA", { exact: true })).toBeVisible();
  await page.screenshot({
    path: resolve(output, "validations.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
