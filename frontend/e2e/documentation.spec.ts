import { expect, test } from "@playwright/test";
import { mkdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { base, password, username } from "./integration-config";

test("capture API-backed public documentation @integration", async ({ page }) => {
  test.setTimeout(180_000);
  test.skip(
    process.env.LIBRIS_DOCS_CAPTURE !== "1",
    "Documentation capture is an explicit disposable-environment task.",
  );
  expect(process.env.LIBRIS_DOCS_CAPTURE_CONFIRM).toBe("disposable");
  expect(["127.0.0.1", "localhost", "::1"]).toContain(new URL(base).hostname);
  const state = JSON.parse(
    readFileSync(
      process.env.LIBRIS_E2E_STATE || "/tmp/libris/epub-smoke.json",
      "utf8",
    ),
  ) as { project_id: string };
  const output = resolve(process.cwd(), "../docs/screenshots");
  const fixtures = ["a", "b", "c"].map((label) =>
    readFileSync(`/tmp/libris/batch-${label}.epub`),
  );
  const created: string[] = [];
  mkdirSync(output, { recursive: true });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  const login = await page.request.post(`${base}/api/auth/login`, {
    data: { username, password },
    headers: { Origin: new URL(base).origin },
  });
  expect(login.ok()).toBeTruthy();
  const existing = (await (
    await page.request.get(`${base}/api/projects?include_archived=true`)
  ).json()) as Array<{ id: string }>;
  expect(existing.map((project) => project.id)).toEqual([state.project_id]);
  const sourceProject = await (
    await page.request.get(`${base}/api/projects/${state.project_id}`)
  ).json();

  const updateProject = async (
    id: string,
    title: string,
    seriesName: string,
    volumeNumber: number | null,
  ) => {
    const project = await (
      await page.request.get(`${base}/api/projects/${id}`)
    ).json();
    const response = await page.request.put(`${base}/api/projects/${id}`, {
      data: {
        title,
        author: "Demo collection",
        series_name: seriesName,
        volume_number: volumeNumber,
        source_language: project.source_language,
        target_language: project.target_language,
        provider_id: project.provider_id,
        quality: project.quality,
        context_backend: "internal",
        instructions: "Preserve a restrained, poetic narrative voice.",
      },
    });
    expect(response.ok()).toBeTruthy();
  };

  await updateProject(
    state.project_id,
    "Tide Lighthouse",
    "Tide Chronicles",
    1,
  );
  try {
    const catalog: Array<[string, string, number | null]> = [
      ["Copper Gardens", "Tide Chronicles", 2],
      ["Atlas for Tomorrow", "", null],
      ["Mist Journal", "Tide Chronicles", 4],
    ];
    for (const [index, [title, series, volume]] of catalog.entries()) {
      const response = await page.request.post(`${base}/api/projects`, {
        multipart: {
          file: {
            name: `${title}.epub`,
            mimeType: "application/epub+zip",
            buffer: fixtures[index],
          },
        },
      });
      expect(response.status()).toBe(201);
      const project = await response.json();
      created.push(project.id);
      await updateProject(project.id, title, series, volume);
    }
    await page.request.post(`${base}/api/projects/${created[2]}/archive`);

    await page.setViewportSize({ width: 1440, height: 1040 });
    await page.goto(`${base}/#library`);
    await expect(page.getByRole("heading", { name: "Library" })).toBeVisible();
    await page.screenshot({
      path: resolve(output, "library.png"),
      fullPage: true,
    });
    await page
      .getByRole("combobox", { name: "Series" })
      .selectOption("Tide Chronicles");
    await page.screenshot({
      path: resolve(output, "series.png"),
      fullPage: true,
    });
    await page.getByRole("combobox", { name: "Series" }).selectOption("all");
    await page.getByRole("button", { name: /Archives/ }).click();
    await page.screenshot({
      path: resolve(output, "archives.png"),
      fullPage: true,
    });
    await page.getByRole("button", { name: /All books/ }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBe(390);
    await page.screenshot({
      path: resolve(output, "mobile.png"),
      fullPage: true,
    });

    await page.setViewportSize({ width: 1440, height: 1040 });
    await page.goto(`${base}/#project/${state.project_id}`);
    await expect(page.getByRole("heading", { level: 1 })).toContainText(
      "Tide Lighthouse",
    );
    await page.screenshot({
      path: resolve(output, "editor.png"),
      fullPage: true,
    });
    await page.screenshot({
      path: resolve(output, "progress-stages.png"),
      fullPage: true,
    });
    await page.getByRole("button", { name: /Validations/ }).click();
    await expect(
      page.getByRole("heading", { name: "Translation validations" }),
    ).toBeVisible();
    await page.screenshot({
      path: resolve(output, "validations.png"),
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBe(390);
    await page.screenshot({
      path: resolve(output, "validations-mobile.png"),
      fullPage: true,
    });
    await page.setViewportSize({ width: 1440, height: 1040 });
    await page.getByRole("button", { name: "Change theme" }).click();
    await page.screenshot({
      path: resolve(output, "validations-light.png"),
      fullPage: true,
    });
  } finally {
    for (const id of created) {
      await page.request.post(`${base}/api/projects/${id}/archive`);
      await page.request.delete(`${base}/api/projects/${id}?stop_jobs=true`);
    }
    await page.request.put(`${base}/api/projects/${state.project_id}`, {
      data: {
        title: sourceProject.title,
        author: sourceProject.author,
        series_name: sourceProject.series_name,
        volume_number: sourceProject.volume_number,
        source_language: sourceProject.source_language,
        target_language: sourceProject.target_language,
        provider_id: sourceProject.provider_id,
        quality: sourceProject.quality,
        context_backend: sourceProject.context_backend,
        instructions: sourceProject.instructions,
      },
    });
  }
});
