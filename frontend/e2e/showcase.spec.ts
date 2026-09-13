import { test, expect } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

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
  const book = {
    id: "demo",
    owner_id: "demo-user",
    title: "Le phare des marées",
    author: "Collection de démonstration",
    series_name: "Chroniques des marées",
    volume_number: 1,
    archived_at: null,
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
    {
      ...book,
      id: "demo-3",
      title: "Un atlas pour demain",
      series_name: "",
      volume_number: null,
      source_language: "es",
      status: "completed",
      stats: { ...stats, translated: 24, validated: 24, flagged: 0 },
    },
    {
      ...book,
      id: "demo-archive",
      title: "Le carnet des brumes",
      series_name: "",
      volume_number: null,
      archived_at: 1789167600,
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
  await page.goto(base);
  await expect(
    page.getByRole("heading", { name: "Bibliothèque", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("progressbar")).toHaveCount(3);
  await expect(page.getByRole("button", { name: /Archives/ })).toContainText(
    "1",
  );
  await expect(
    page.getByRole("progressbar", {
      name: "Traduction de Le phare des marées",
    }),
  ).toHaveAttribute("value", "75");
  await expect(
    page.getByRole("progressbar", {
      name: "Analyse & mémoire de Les jardins de cuivre",
    }),
  ).toHaveAttribute("value", "36");
  await expect(
    page.getByRole("progressbar", {
      name: "Export de Un atlas pour demain",
    }),
  ).toHaveAttribute("value", "100");
  await page.screenshot({ path: resolve(output, "library.png") });
  await page.getByRole("button", { name: /En cours/ }).click();
  await expect(page.locator(".library-table tbody tr")).toHaveCount(1);
  await expect(
    page.getByRole("link", { name: "Les jardins de cuivre", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Rechercher un livre").fill("introuvable");
  await expect(
    page.getByText("Aucun livre ne correspond.", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Effacer les filtres" }).click();
  await expect(page.locator(".library-table tbody tr")).toHaveCount(3);
  await page
    .getByRole("combobox", { name: "Série", exact: true })
    .selectOption("Chroniques des marées");
  await expect(page.locator(".library-table tbody tr")).toHaveCount(2);
  await page.screenshot({
    path: resolve(output, "series.png"),
    fullPage: true,
  });
  await page
    .getByRole("combobox", { name: "Série", exact: true })
    .selectOption("all");
  await page.getByRole("button", { name: /Archives/ }).click();
  await expect(
    page.getByRole("link", { name: "Le carnet des brumes", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Restaurer", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: resolve(output, "archives.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: /Tous les livres/ }).click();
  await page.getByLabel("Trier par").selectOption("title");
  await page
    .getByRole("button", { name: "+ Importer des EPUB", exact: true })
    .focus();
  await expect(
    page.getByRole("button", { name: "+ Importer des EPUB", exact: true }),
  ).toBeFocused();
  await page.getByLabel("Trier par").selectOption("recent");
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
    ["1 · Import", "EPUB importé et structure chargée."],
    [
      "2 · Analyse & mémoire",
      "24/24 passages analysés · 4/4 sections synthétisées.",
    ],
    ["3 · Traduction", "18/24 passages traduits · 0 conservés en original."],
    [
      "4 · Relecture",
      "16/24 passages examinés · 0 résolus · 1 à vérifier.",
    ],
    [
      "5 · Export",
      "Terminez les alertes restantes avant l’export final.",
    ],
  ]) {
    await page.getByRole("button", { name: button, exact: true }).click();
    await expect(page.getByText(detail, { exact: true })).toBeVisible();
    await expect(page.getByRole("progressbar")).toHaveCount(1);
  }
  await page
    .getByRole("button", { name: "3 · Traduction", exact: true })
    .click();
  await page.getByRole("button", { name: "Suivre l’étape active" }).click();
  await page.screenshot({
    path: resolve(output, "progress-stages.png"),
    fullPage: true,
  });
  await page.screenshot({ path: resolve(output, "editor.png") });
  await page
    .getByRole("button", { name: "Validations (1)", exact: true })
    .click();
  await expect(page.getByText("Avis de l’IA", { exact: true })).toBeVisible();
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
  await page
    .getByRole("button", { name: "Changer de thème", exact: true })
    .click();
  await page.screenshot({
    path: resolve(output, "validations-light.png"),
    fullPage: true,
  });
  await page.getByText("Exporter ↓", { exact: true }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "EPUB traduit", exact: true }).click();
  await download;
  await expect(
    page.getByText("Fichier reçu ; téléchargement transmis au navigateur."),
  ).toBeVisible();
  await page.getByRole("combobox", { name: "Langue" }).selectOption("en");
  await expect(
    page.getByRole("heading", { name: "Translation validations" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Change theme" })).toBeVisible();
  await page.getByRole("combobox", { name: "Language" }).selectOption("fr");
  await expect(
    page.getByRole("heading", { name: "Validations de traduction" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "3 · Traduction", exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});
