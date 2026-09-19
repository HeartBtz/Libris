import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { projectProgress } from "../src/features/progress";
import type { FileInspection, ImportResult, Project, Series, SeriesChapter } from "../src/types";

// A small in-memory stand-in for the import and series API: enough state for the assistant's
// answers to show up in the library and on the series page. Synthetic names only.
interface CommitBody {
  destination: { mode: "series" | "standalone"; series_id?: string; series_name?: string };
  target?: { mode: string; volume_number?: number; volume_title?: string };
  items: { index: number; title: string; volume_number?: number | null; chapter_number?: number | null; confirmed: boolean; skip: boolean }[];
  settings: Record<string, string>;
  start: string;
}

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
  chapters: 1,
  glossary: 0,
};

function project(id: string, title: string, extra: Partial<Project> = {}): Project {
  const value: Project = {
    id,
    owner_id: "demo-user",
    title,
    author: "Demo author",
    series_name: "",
    volume_number: null,
    archived_at: null,
    source_language: "en",
    target_language: "fr",
    provider_id: null,
    quality: "normal",
    context_backend: "internal",
    instructions: "",
    status: "pending",
    stats,
    updated_at: 1789254000,
    book_info: { words: 1000, images: 0, size: 1000 },
    series_id: null,
    source_format: "epub",
    project_kind: "volume",
    external_id: null,
    import_meta: {},
    ...extra,
  };
  return { ...value, progress: projectProgress(value) };
}

function seriesView(id: string, name: string, kind: Series["kind"], volumes: Project[], chapters: number): Series {
  const numbers = volumes.map((p) => p.volume_number).filter((n): n is number => n !== null);
  return {
    id,
    owner_id: "demo-user",
    name,
    kind,
    authors: ["Demo author"],
    source_language: "en",
    target_language: "fr",
    provider_id: null,
    quality: null,
    context_backend: null,
    instructions: "",
    bible_validated: false,
    archived_at: null,
    created_at: 1789254000,
    updated_at: 1789254000,
    shared: false,
    volumes: volumes.filter((p) => p.project_kind === "volume").length,
    serial: volumes.some((p) => p.project_kind === "serial"),
    chapters,
    formats: Array.from(new Set(volumes.map((p) => p.source_format || "epub"))),
    progress: { total: 10 * volumes.length, translated: 0, validated: 0, percent: 0, running: 0 },
    issues: { flagged: 0, errors: 0, context_stale: 0 },
    activity: 1789254000,
    providers: [],
    memory: { backends: ["internal"], pending: 0, failed: 0 },
    missing_volumes: numbers.length ? Array.from({ length: Math.max(...numbers) }, (_, i) => i + 1).filter((n) => !numbers.includes(n)) : [],
    duplicate_volumes: [],
  };
}

async function mockApi(page: Page, options: { rejectFirstCommit?: boolean } = {}) {
  const projects: Project[] = [];
  const series: { id: string; name: string; kind: Series["kind"] }[] = [];
  const chapters: SeriesChapter[] = [];
  const sessions = new Map<string, { format: "epub" | "txt"; files: FileInspection[]; result: ImportResult | null }>();
  const commits: CommitBody[] = [];
  const deleted: string[] = [];
  let rejected = !options.rejectFirstCommit;
  let counter = 0;
  const volumesOf = (id: string) => projects.filter((p) => p.series_id === id);
  const view = (s: (typeof series)[number]) =>
    seriesView(s.id, s.name, s.kind, volumesOf(s.id), chapters.filter((c) => volumesOf(s.id).some((p) => p.id === c.project_id)).length || volumesOf(s.id).length);
  function inspect(format: "epub" | "txt", name: string, index: number, files: FileInspection[]): FileInspection {
    const number = /(?:tome|volume|chapter)\s*(\d+)/i.exec(name);
    const same = files.find((file) => file.name === name);
    return {
      index,
      name,
      size: 2048 + index,
      sha256: `sha-${name}`,
      duplicate: same ? { kind: "batch", index: same.index, name: same.name } : null,
      format,
      title: name.replace(/\.(epub|txt)$/, ""),
      author: "Demo author",
      language: "en",
      series: "",
      series_index: null,
      chapter_number: format === "txt" && number ? Number(number[1]) : null,
      number_confidence: number ? "high" : "low",
      number_reason: number ? "keyword in the file name" : "no number found",
      warnings: [],
      errors: [],
      meta: format === "txt" ? { first_line: `First line of ${name}` } : { chapters: 3 },
    };
  }
  function proposal(format: "epub" | "txt", files: FileInspection[]) {
    const items = files.map((file) => {
      const number = /(?:tome|volume|chapter)\s*(\d+)/i.exec(file.name);
      const value = number ? Number(number[1]) : null;
      return {
        index: file.index,
        title: file.title,
        confidence: number ? "high" : "low",
        reason: number ? "keyword in the file name" : "no number found",
        ...(format === "epub"
          ? { volume_number: value, warnings: [], existing_volume: null }
          : { chapter_number: value, existing_chapter: null }),
      };
    });
    items.sort((a, b) => {
      const x = ("volume_number" in a ? a.volume_number : a.chapter_number) ?? 1e9;
      const y = ("volume_number" in b ? b.volume_number : b.chapter_number) ?? 1e9;
      return x - y;
    });
    const prefix = files.every((file) => file.name.startsWith("Saga")) ? "Saga" : "";
    return {
      ...(format === "epub"
        ? { series: { name: prefix, confidence: prefix ? "high" : "low", series_reason: "file name without its number", existing_series_id: null } }
        : {}),
      items,
      duplicate_numbers: [],
      missing_numbers: [],
    };
  }
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const parts = path.split("/").filter(Boolean);
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { id: "demo-user", username: "Demo", admin: true } });
    if (path === "/api/projects") return route.fulfill({ json: projects });
    if (path === "/api/series") return route.fulfill({ json: series.map(view) });
    if (parts[1] === "series" && parts.length === 3)
      return route.fulfill({ json: { ...view(series.find((s) => s.id === parts[2])!), bible: {}, volume_list: volumesOf(parts[2]) } });
    if (parts[1] === "series" && parts[3] === "chapters")
      return route.fulfill({ json: chapters.filter((c) => volumesOf(parts[2]).some((p) => p.id === c.project_id)) });
    if (parts[1] === "series" && parts[3] === "memory") return route.fulfill({ status: 404, json: { detail: "Unknown API route." } });
    if (parts[1] === "series" && parts[3] === "glossary") return route.fulfill({ json: { terms: [], overrides: [] } });
    if (parts[1] === "series" && parts[3] === "bible")
      return route.fulfill({ json: { bible: {}, validated: false, updated_at: 1789254000 } });
    if (path === "/api/imports" && method === "POST") {
      const id = `session-${++counter}`;
      const format = request.postDataJSON().format;
      sessions.set(id, { format, files: [], result: null });
      return route.fulfill({ status: 201, json: { id, format, files: [], expires_at: 1789999999, result: null } });
    }
    if (parts[1] === "imports") {
      const session = sessions.get(parts[2])!;
      if (parts[3] === "files" && method === "POST") {
        const body = request.postDataBuffer()?.toString("latin1") || "";
        const name = Buffer.from(/filename="([^"]+)"/.exec(body)![1], "latin1").toString("utf8");
        const index = session.files.length ? Math.max(...session.files.map((f) => f.index)) + 1 : 0;
        const entry = inspect(session.format, name, index, session.files);
        session.files.push(entry);
        return route.fulfill({ status: 201, json: entry });
      }
      if (parts[3] === "files" && method === "DELETE") {
        session.files = session.files.filter((f) => f.index !== Number(parts[4]));
        return route.fulfill({ json: { ok: true } });
      }
      if (parts[3] === "commit") {
        const body = request.postDataJSON() as CommitBody;
        commits.push(body);
        if (!rejected) {
          rejected = true;
          return route.fulfill({
            status: 422,
            json: { detail: { message: "The import cannot be confirmed.", errors: ["Volume 3 already exists in “Saga”."] } },
          });
        }
        let seriesId: string | null = null;
        let seriesName = "";
        if (body.destination.mode === "series") {
          const existing = series.find((s) => s.id === body.destination.series_id || s.name === body.destination.series_name);
          if (existing) ({ id: seriesId, name: seriesName } = existing);
          else {
            seriesId = `series-${++counter}`;
            seriesName = body.destination.series_name || "";
            series.push({ id: seriesId, name: seriesName, kind: session.format === "txt" ? "webnovel" : "books" });
          }
        }
        const kept = body.items.filter((item) => !item.skip);
        const created: Project[] = [];
        if (session.format === "epub")
          for (const item of kept)
            created.push(
              project(`p-${++counter}`, item.title, {
                series_id: seriesId,
                series_name: seriesName,
                volume_number: item.volume_number ?? null,
              }),
            );
        else {
          const serial = project(`p-${++counter}`, seriesName, {
            series_id: seriesId,
            series_name: seriesName,
            source_format: "txt",
            project_kind: "serial",
          });
          created.push(serial);
          kept.forEach((item, position) =>
            chapters.push({
              id: `c-${++counter}`,
              project_id: serial.id,
              volume_title: serial.title,
              position,
              title: item.title,
              chapter_number: item.chapter_number ?? null,
              external_id: null,
              analyzed: false,
              context_stale: false,
              segments: 4,
              translated: 0,
              validated: 0,
              flagged: 0,
            }),
          );
          chapters.sort((a, b) => (a.chapter_number ?? 1e9) - (b.chapter_number ?? 1e9));
        }
        projects.push(...created);
        const result: ImportResult = {
          series_id: seriesId,
          series_name: seriesName,
          projects: created.map((p) => ({ id: p.id, title: p.title, volume_number: p.volume_number, status: "created" })),
          chapters: { created: session.format === "txt" ? kept.length : 0, unchanged: 0, replaced: 0, items: [] },
          jobs: body.start === "none" ? [] : created.map((p) => ({ project_id: p.id, job_id: `job-${p.id}` })),
          warnings: body.start === "pipeline" ? ["No provider is configured: translation will wait for a provider."] : [],
        };
        session.result = result;
        return route.fulfill({ json: { ...result, views: created } });
      }
      if (method === "DELETE") {
        deleted.push(parts[2]);
        return route.fulfill({ json: { ok: true } });
      }
      return route.fulfill({ json: { id: parts[2], format: session.format, files: session.files, expires_at: 1789999999, result: session.result, proposal: proposal(session.format, session.files) } });
    }
    return route.fulfill({ json: [] });
  });
  return { commits, deleted, projects };
}

const epub = (name: string) => ({ name, mimeType: "application/epub+zip", buffer: Buffer.from(`synthetic ${name}`) });
const txt = (name: string) => ({ name, mimeType: "text/plain", buffer: Buffer.from(`Synthetic chapter ${name}\n`) });

async function expectNoOverflow(page: Page) {
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), { timeout: 3000 })
    .toBe(true);
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
});

test("a single EPUB imported as a standalone volume appears in the library", async ({ page }) => {
  const api = await mockApi(page);
  await page.goto(process.env.SHOWCASE_URL || "http://127.0.0.1:4173");
  await page.getByRole("button", { name: "Add content" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Add content" });
  await expect(dialog.getByRole("button", { name: "Continue" })).toBeDisabled();
  await dialog.getByRole("radio", { name: /EPUB books/ }).check();
  await dialog.getByRole("button", { name: "Continue" }).click();
  // No destination chosen, no way forward.
  await expect(dialog.getByRole("button", { name: "Continue" })).toBeDisabled();
  await dialog.getByRole("radio", { name: /Standalone volume/ }).check();
  await dialog.getByRole("button", { name: "Continue" }).click();
  await dialog.locator('input[type="file"]').setInputFiles([epub("Atlas of Islands.epub"), txt("notes.txt")]);
  await expect(dialog.getByText("This file is not a EPUB: it is not uploaded.")).toBeVisible();
  await expect(dialog.getByText("1/1 files analyzed")).toBeVisible();
  await dialog.getByRole("button", { name: "Remove notes.txt" }).click();
  await dialog.getByRole("button", { name: "Continue" }).click();
  await expect(dialog.getByText("Each EPUB will become a standalone volume, without a series.")).toBeVisible();
  await expect(dialog.getByRole("textbox", { name: /Volume number of/ })).toHaveCount(0);
  await dialog.getByRole("button", { name: "Continue" }).click();
  await expect(dialog.getByRole("region", { name: "Summary" })).toContainText("Standalone volumes");
  await dialog.getByRole("button", { name: "Import only" }).click();
  await expect(dialog.getByText("Import complete.")).toBeVisible();
  expect(api.commits[0]).toMatchObject({ destination: { mode: "standalone" }, start: "none", items: [{ index: 0, title: "Atlas of Islands" }] });
  await dialog.getByRole("button", { name: "Close" }).first().click();
  const standalone = page.getByRole("region", { name: /Standalone volumes/ });
  await expect(standalone.getByRole("link", { name: "Atlas of Islands", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: /^Series/ })).toHaveCount(0);
});

test("EPUB volumes of one series: the pre-analysis is corrected before the import", async ({ page }) => {
  const api = await mockApi(page, { rejectFirstCommit: true });
  await page.goto(process.env.SHOWCASE_URL || "http://127.0.0.1:4173");
  await page.getByRole("button", { name: "Add content" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Add content" });
  await dialog.getByRole("radio", { name: /EPUB books/ }).check();
  await dialog.getByRole("button", { name: "Continue" }).click();
  await dialog.getByRole("radio", { name: /New series/ }).check();
  await dialog.getByRole("button", { name: "Continue" }).click();
  await dialog
    .locator('input[type="file"]')
    .setInputFiles([epub("Saga - Tome 2.epub"), epub("Saga - Tome 1.epub"), epub("Saga - bonus.epub")]);
  await expect(dialog.getByText("3/3 files analyzed")).toBeVisible();
  await dialog.getByRole("button", { name: "Continue" }).click();
  // The series name comes from the files; the unnumbered file blocks until someone decides.
  await expect(dialog.getByRole("textbox", { name: "Series name" })).toHaveValue("Saga");
  await expect(dialog.getByText("A new series will be created.")).toBeVisible();
  await expect(dialog.getByRole("textbox", { name: "Volume number of Saga - Tome 1.epub" })).toHaveValue("1");
  await expect(dialog.getByText("1 blocking issue to fix before continuing.")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Continue" })).toBeDisabled();
  const bonus = dialog.getByRole("textbox", { name: "Volume number of Saga - bonus.epub" });
  await bonus.fill("2");
  await expect(dialog.getByText("Duplicate number in this batch.")).toHaveCount(2);
  await bonus.fill("3");
  await dialog.getByRole("textbox", { name: "Title of Saga - Tome 2.epub" }).fill("The Second Tide");
  await dialog.getByRole("textbox", { name: "Series name" }).fill("Saga of the Tides");
  await dialog.getByRole("button", { name: "Continue" }).click();
  await expect(dialog.getByRole("region", { name: "Summary" })).toContainText("Series “Saga of the Tides” (new)");
  await dialog.getByLabel("Quality").selectOption("high");
  await dialog.getByRole("button", { name: "Import only" }).click();
  // A refusal of the server is shown with each of its reasons.
  await expect(dialog.getByRole("alert")).toContainText("Volume 3 already exists in “Saga”.");
  await dialog.getByRole("button", { name: "Import and run the whole pipeline" }).click();
  await expect(dialog.getByText("Import complete.")).toBeVisible();
  await expect(dialog.getByText("No provider is configured: translation will wait for a provider.")).toBeVisible();
  const body = api.commits.at(-1)!;
  expect(body).toMatchObject({
    destination: { mode: "series", series_name: "Saga of the Tides" },
    settings: { quality: "high" },
    start: "pipeline",
  });
  expect(body.items.map((item) => [item.title, item.volume_number])).toEqual([
    ["Saga - Tome 1", 1],
    ["The Second Tide", 2],
    ["Saga - bonus", 3],
  ]);
  await dialog.getByRole("button", { name: "Close" }).first().click();
  const seriesSection = page.getByRole("region", { name: /^Series/ });
  await expect(seriesSection.locator(".series-card")).toHaveCount(1);
  await expect(seriesSection.getByRole("link", { name: "Saga of the Tides" })).toBeVisible();
  await expect(seriesSection).toContainText("3 volumes");
  await expect(page.getByRole("region", { name: /Standalone volumes/ })).toHaveCount(0);
  await seriesSection.getByRole("link", { name: "Saga of the Tides" }).click();
  await expect(page.getByRole("heading", { name: "Saga of the Tides", level: 1 })).toBeVisible();
  await page.getByRole("tab", { name: /Volumes/ }).click();
  await expect(page.getByRole("textbox", { name: "Number of The Second Tide" })).toHaveValue("2");
  // The series memory endpoint may be missing: the page says so instead of inventing numbers.
  await page.getByRole("tab", { name: "Memory" }).click();
  await expect(page.getByText("Series memory is not available on this server.")).toBeVisible();
});

test("webnovel TXT chapters need a series and keep the order chosen before the import", async ({ page }) => {
  const api = await mockApi(page);
  await page.goto(process.env.SHOWCASE_URL || "http://127.0.0.1:4173");
  await page.getByRole("button", { name: "Add content" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Add content" });
  await dialog.getByRole("radio", { name: /TXT chapters/ }).check();
  await dialog.getByRole("button", { name: "Continue" }).click();
  await expect(dialog.getByRole("radio", { name: /Standalone volume/ })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "Continue" })).toBeDisabled();
  await dialog.getByRole("radio", { name: /New series/ }).check();
  await expect(dialog.getByRole("button", { name: "Continue" })).toBeDisabled();
  await dialog.getByLabel("Series name").fill("Canal Lights");
  await expect(dialog.getByRole("radio", { name: /Continuous flow of the series/ })).toBeChecked();
  await dialog.getByRole("button", { name: "Continue" }).click();
  await dialog
    .locator('input[type="file"]')
    .setInputFiles([txt("dawn.txt"), txt("bridge.txt"), txt("bells.txt"), txt("bridge.txt")]);
  await expect(dialog.getByText("This file is already in the list.")).toBeVisible();
  await expect(dialog.getByText("3/3 files analyzed")).toBeVisible();
  await dialog.getByRole("button", { name: "Continue" }).click();
  await expect(dialog.getByText("3 blocking issues to fix before continuing.")).toBeVisible();
  // Put bridge first and dawn last, then renumber in that order: each number is the person's decision.
  await expect(dialog.locator(".wizard-row-name")).toHaveText(["dawn.txt", "bridge.txt", "bells.txt"]);
  await dialog.getByRole("button", { name: "Move bridge.txt up" }).click();
  await dialog.getByRole("button", { name: "Move dawn.txt down" }).click();
  await dialog.getByRole("button", { name: "Renumber in this order" }).click();
  await expect(dialog.locator(".wizard-row-name")).toHaveText(["bridge.txt", "bells.txt", "dawn.txt"]);
  await expect(dialog.getByText(/blocking issue/)).toHaveCount(0);
  await dialog.getByRole("checkbox", { name: "Use the first line as the title" }).check();
  await expect(dialog.getByRole("textbox", { name: "Title of bridge.txt" })).toHaveValue("First line of bridge.txt");
  await dialog.getByRole("button", { name: "Continue" }).click();
  await expect(dialog.getByRole("region", { name: "Summary" })).toContainText("Continuous flow (created)");
  await dialog.getByRole("button", { name: "Import and start analysis" }).click();
  await expect(dialog.getByText("3 chapters created · 0 identical · 0 replaced")).toBeVisible();
  const body = api.commits[0];
  expect(body).toMatchObject({ destination: { mode: "series", series_name: "Canal Lights" }, target: { mode: "serial" }, first_line_title: true, start: "analyze" });
  expect(body.items.map((item) => [item.title, item.chapter_number, item.confirmed])).toEqual([
    ["bridge", 1, true],
    ["bells", 2, true],
    ["dawn", 3, true],
  ]);
  await dialog.getByRole("button", { name: "Open the series" }).click();
  await expect(page.getByRole("heading", { name: "Canal Lights", level: 1 })).toBeVisible();
  await page.getByRole("tab", { name: /Chapters/ }).click();
  await expect(page.locator(".chapter-list .book-title")).toHaveText(["bridge", "bells", "dawn"]);
});

test("abandoning the assistant discards the server's staging", async ({ page }) => {
  const api = await mockApi(page);
  await page.goto(process.env.SHOWCASE_URL || "http://127.0.0.1:4173");
  await page.getByRole("button", { name: "Add content" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Add content" });
  await dialog.getByRole("radio", { name: /EPUB books/ }).check();
  await dialog.getByRole("button", { name: "Continue" }).click();
  await dialog.getByRole("radio", { name: /Standalone volume/ }).check();
  await dialog.getByRole("button", { name: "Continue" }).click();
  await dialog.locator('input[type="file"]').setInputFiles([epub("Draft.epub")]);
  await expect(dialog.getByText("1/1 files analyzed")).toBeVisible();
  await dialog.getByRole("button", { name: "Abandon", exact: true }).click();
  await page.getByRole("dialog", { name: "Abandon this import?" }).getByRole("button", { name: "Abandon the import" }).click();
  await expect(dialog).toBeHidden();
  await expect.poll(() => api.deleted).toEqual(["session-1"]);
  expect(api.projects).toEqual([]);
});

test("on a phone: the whole assistant, the library and the series page without horizontal scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page);
  await page.goto(process.env.SHOWCASE_URL || "http://127.0.0.1:4173");
  await page.getByRole("button", { name: "Add content" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Add content" });
  // Full screen below 720 px (once the opening animation is over).
  await expect.poll(async () => await dialog.boundingBox()).toMatchObject({ x: 0, y: 0, width: 390, height: 844 });
  await dialog.getByRole("radio", { name: /EPUB books/ }).check();
  await expectNoOverflow(page);
  await dialog.getByRole("button", { name: "Continue" }).click();
  await dialog.getByRole("radio", { name: /New series/ }).check();
  await dialog.getByLabel("Series name").fill("Pocket Saga");
  await expectNoOverflow(page);
  await dialog.getByRole("button", { name: "Continue" }).click();
  await dialog
    .locator('input[type="file"]')
    .setInputFiles([epub("Pocket Saga - Tome 1 with a very long file name that must never widen the screen.epub"), epub("Pocket Saga - Tome 2.epub")]);
  await expect(dialog.getByText("2/2 files analyzed")).toBeVisible();
  await expectNoOverflow(page);
  await dialog.getByRole("button", { name: "Continue" }).click();
  await expect(dialog.getByRole("textbox", { name: /Volume number of Pocket Saga - Tome 2/ })).toHaveValue("2");
  await expectNoOverflow(page);
  for (const control of await dialog.getByRole("button").all())
    if (await control.isVisible()) expect((await control.boundingBox())!.height).toBeGreaterThanOrEqual(36);
  await dialog.getByRole("button", { name: "Continue" }).click();
  await expectNoOverflow(page);
  await dialog.getByRole("button", { name: "Import only" }).click();
  await expect(dialog.getByText("Import complete.")).toBeVisible();
  await dialog.getByRole("button", { name: "Close" }).first().click();
  await expect(page.getByRole("link", { name: "Pocket Saga" })).toBeVisible();
  await expectNoOverflow(page);
  await page.getByRole("link", { name: "Pocket Saga" }).click();
  await expect(page.getByRole("heading", { name: "Pocket Saga", level: 1 })).toBeVisible();
  for (const tab of ["Dashboard", "Volumes", "Series Bible", "Glossary", "Identities", "Memory", "Defaults", "Management"]) {
    await page.getByRole("tab", { name: new RegExp(`^${tab}`) }).click();
    await expectNoOverflow(page);
  }
  const primary = page.getByRole("navigation", { name: "Primary navigation" });
  await page.getByRole("button", { name: "Menu", exact: true }).click();
  await expect(primary.getByRole("link", { name: "Library" })).toHaveAttribute("aria-current", "page");
  await primary.getByRole("link", { name: "Library" }).click();
  await expect(page.getByRole("heading", { name: "Library", exact: true })).toBeVisible();
  await expectNoOverflow(page);
});
