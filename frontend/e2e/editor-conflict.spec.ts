import { expect, test } from "@playwright/test";
import { projectProgress } from "../src/features/progress";
import type { Project } from "../src/types";

test("an edit conflict can be resolved from the editor", async ({ page }) => {
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
  const server = { revision: 0, text: "Version initiale." };
  const saves: { revision: number; text: string }[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const segment = () => ({
      id: "segment",
      project_id: "demo",
      chapter_id: "chapter",
      position: 0,
      section: "One",
      source: "Initial version.",
      translation: server.text,
      units: [{ id: "u1", text: "Initial version." }],
      translated_units: [{ id: "u1", text: server.text }],
      status: "ok",
      stage: "done",
      human: false,
      validated: false,
      revision: server.revision,
      retained_source: false,
      instructions: "",
      error: "",
      uncertainties: [],
      critique: [],
    });
    if (path === "/api/segments/segment" && request.method() === "PUT") {
      const body = request.postDataJSON() as {
        revision: number;
        units: { text: string }[];
      };
      saves.push({ revision: body.revision, text: body.units[0].text });
      if (body.revision !== server.revision) {
        await route.fulfill({
          status: 409,
          json: { detail: "Le passage a été modifié." },
        });
        return;
      }
      server.revision += 1;
      server.text = body.units[0].text;
      await route.fulfill({ json: segment() });
      return;
    }
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
      data = [
        {
          id: "chapter",
          title: "Chapter 1",
          position: 0,
          resource: "c.xhtml",
          instructions: "",
          analyzed: true,
        },
      ];
    else if (path.endsWith("/segments")) data = [segment()];
    await route.fulfill({ json: data });
  });
  await page.goto(
    `${process.env.SHOWCASE_URL || "http://127.0.0.1:4173"}/#project/demo`,
  );
  const box = page.getByRole("textbox", {
    name: "Translation passage 1 unit 1",
  });
  await expect(box).toHaveValue("Version initiale.");
  const row = box.locator("xpath=ancestor::article");

  // The reviewer types while the running job publishes a new revision.
  await box.fill("Ma correction.");
  server.revision = 1;
  server.text = "Version du travail en cours.";
  await page.getByRole("button", { name: "Refresh book data" }).click();
  await expect(
    row.getByText("A new version of this segment arrived"),
  ).toBeVisible();
  await expect(box).toHaveValue("Ma correction.");

  // Keeping the local text rebases it on the latest revision: the save goes through.
  await row.getByRole("button", { name: "Keep my text" }).click();
  const reloaded = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname.endsWith("/segments") &&
      response.request().method() === "GET",
  );
  await row.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => server.text).toBe("Ma correction.");
  await reloaded; // the editor has taken the saved revision into account
  expect(saves.at(-1)).toEqual({ revision: 1, text: "Ma correction." });

  // Reloading the server version discards the local text.
  await box.fill("Autre saisie.");
  server.revision = 3;
  server.text = "Version finale du serveur.";
  await page.getByRole("button", { name: "Refresh book data" }).click();
  await row.getByRole("button", { name: "Reload the server version" }).click();
  await expect(box).toHaveValue("Version finale du serveur.");
  await expect(
    row.getByText("A new version of this segment arrived"),
  ).toHaveCount(0);
});
