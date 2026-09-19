import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { projectProgress } from "../src/features/progress";
import type { AutopilotDecision, AutopilotView, FileInspection, Job, Project } from "../src/types";

// The autopilot in the interface, against an in-memory stand-in of the API. Synthetic names only.

const BASE = process.env.SHOWCASE_URL || "http://127.0.0.1:4173";

/** An EventSource that stays connected; `window.__libris_emit()` delivers one event to every stream. */
async function controllableEvents(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("locale", "en");
    const streams: { onmessage: ((event: MessageEvent) => void) | null }[] = [];
    class ConnectedEventSource extends EventTarget {
      readyState = 1;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      constructor(public url: string) {
        super();
        streams.push(this);
        setTimeout(() => this.onopen?.(new Event("open")), 50);
      }
      close() {
        this.readyState = 2;
      }
    }
    Object.defineProperty(window, "EventSource", { value: ConnectedEventSource });
    Object.defineProperty(window, "__libris_emit", {
      value: () => streams.forEach((stream) => stream.onmessage?.(new MessageEvent("message", { data: "{}" }))),
    });
  });
}

const baseStats = {
  total: 6,
  translated: 0,
  validated: 0,
  reviewed_segments: 0,
  review_total: 6,
  flagged: 0,
  errors: 0,
  refused: 0,
  retained_source: 0,
  analyzed_segments: 0,
  synthesized_chapters: 0,
  chapters: 2,
  glossary: 0,
};

function volume(id: string, title: string, number: number | null, extra: Partial<Project> = {}): Project {
  const value: Project = {
    id,
    owner_id: "demo-user",
    title,
    author: "Demo author",
    series_name: "Harbour Lights",
    series_id: "harbour",
    volume_number: number,
    archived_at: null,
    source_language: "en",
    target_language: "fr",
    provider_id: "local",
    quality: "high",
    context_backend: "internal",
    instructions: "",
    status: "pending",
    stats: baseStats,
    updated_at: 1789254000,
    book_info: { words: 3200, images: 0, size: 64000 },
    source_format: "epub",
    project_kind: "volume",
    config: {},
    ...extra,
  };
  return { ...value, progress: projectProgress(value) };
}

const decision = (id: string, extra: Partial<AutopilotDecision>): AutopilotDecision => ({
  id,
  project_id: "p-2",
  job_id: "job-1",
  segment_id: null,
  stage: "review",
  kind: "critique",
  action: "applied",
  reason: "",
  provider: "Local model",
  model: "zeta-model",
  created_at: 1789254000,
  ...extra,
});

/**
 * Two EPUB volumes imported into a new series with the whole pipeline; the one without a number gets
 * the next volume automatically. `state.finished` switches its autopilot run from running to done.
 */
async function mockApi(page: Page) {
  const state = { finished: false, mutations: [] as string[], exports: [] as string[] };
  const files: FileInspection[] = [];
  let projects: Project[] = [];
  const decisions = [
    decision("d1", {
      stage: "provider",
      kind: "outage",
      action: "fallback_provider",
      reason: "“Local model” unavailable after 6 attempts: “Backup model” takes over.",
      provider: "Backup model",
      model: "omega-model",
    }),
    decision("d2", {
      segment_id: "s-4",
      stage: "review",
      kind: "critique",
      action: "applied",
      reason: "“lantern” means the lighthouse light here: “feu” kept, as in the glossary.",
    }),
    decision("d3", {
      segment_id: "s-6",
      stage: "recovery",
      kind: "failed_passage",
      action: "source_retained",
      reason: "Refused by every provider twice: original text kept.",
    }),
    decision("d4", { stage: "memory", kind: "glossary", action: "accepted", reason: "“Keeper” → “gardien”, used 9 times." }),
  ];
  const job = (): Job => ({
    id: "job-1",
    status: state.finished ? "completed" : "translating",
    operation: "translate",
    error: "",
    stop_reason: "",
    next_attempt: 0,
    outage_count: 0,
    attempts: 1,
    provider_id: "backup",
    options: { autopilot: true, automatic_recovery: true, full_review: true },
    checkpoint: state.finished ? {} : { step: "autopilot", autopilot_phase: "review", autopilot_round: 2 },
    created_at: 1789250000,
    finished_at: state.finished ? 1789254000 : null,
  });
  const book = () =>
    volume("p-2", "Harbour Lights - extra", 2, {
      status: state.finished ? "completed" : "translating",
      stats: state.finished
        ? { ...baseStats, analyzed_segments: 6, synthesized_chapters: 2, translated: 5, retained_source: 1, reviewed_segments: 6 }
        : { ...baseStats, analyzed_segments: 6, synthesized_chapters: 2, translated: 4, reviewed_segments: 3 },
    });
  const view = (url: URL): AutopilotView => {
    const segment = url.searchParams.get("segment_id");
    const stage = url.searchParams.get("stage");
    const visible = state.finished ? decisions : decisions.slice(0, 1);
    const items = visible.filter((item) => (!segment || item.segment_id === segment) && (!stage || item.stage === stage));
    return {
      enabled: true,
      settings: { max_rounds: 3, fallback_provider_ids: [], outage_max_retries: 5, outage_max_wait_seconds: 3600 },
      report: state.finished
        ? {
            outcome: "completed_with_residuals",
            rounds: 2,
            residuals: [{ segment_id: "s-6", chapter_id: "c-2", reason: "Refused by every provider twice: original text kept." }],
            job_id: "job-1",
            status: "completed",
            finished_at: 1789254000,
          }
        : null,
      decisions: { items, total: items.length, limit: 25, offset: 0 },
    };
  };
  const segment = (position: number) => ({
    id: `s-${position}`,
    project_id: "p-2",
    chapter_id: position <= 3 ? "c-1" : "c-2",
    position,
    section: "",
    source: `Synthetic sentence number ${position} about the harbour.`,
    translation: position === 6 ? "" : `Phrase synthétique numéro ${position} sur le port.`,
    units: [{ id: "u1", text: `Synthetic sentence number ${position} about the harbour.` }],
    translated_units: position === 6 ? [] : [{ id: "u1", text: `Phrase synthétique numéro ${position} sur le port.` }],
    status: "ok",
    stage: "done",
    human: false,
    validated: false,
    revision: 0,
    retained_source: position === 6 && state.finished,
    instructions: "",
    error: "",
    uncertainties: [],
    critique: [],
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    if (method !== "GET") state.mutations.push(`${method} ${path}`);
    if (path.endsWith("/events")) return route.fulfill({ contentType: "text/event-stream", body: ": demo\n\n" });
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { id: "demo-user", username: "Demo", admin: true } });
    if (path === "/api/imports" && method === "POST")
      return route.fulfill({
        status: 201,
        json: { id: "session-1", format: "epub", files: [], expires_at: 1789999999, result: null, confirm_low_confidence: false },
      });
    if (path === "/api/imports/session-1/files" && method === "POST") {
      const body = request.postDataBuffer()?.toString("latin1") || "";
      const name = /filename="([^"]+)"/.exec(body)![1];
      const number = /Tome (\d+)/.exec(name);
      const entry: FileInspection = {
        index: files.length,
        name,
        size: 4096,
        sha256: `sha-${name}`,
        duplicate: null,
        format: "epub",
        title: name.replace(/\.epub$/, ""),
        author: "Demo author",
        language: "en",
        series: "",
        series_index: null,
        chapter_number: null,
        number_confidence: number ? "high" : "low",
        number_reason: number ? "keyword in the file name" : "no number found",
        warnings: [],
        errors: [],
        meta: { chapters: 2 },
      };
      files.push(entry);
      return route.fulfill({ status: 201, json: entry });
    }
    if (path === "/api/imports/session-1/commit") {
      const body = request.postDataJSON() as { start: string; items: { index: number; title: string; volume_number: number | null }[] };
      projects = [volume("p-1", "Harbour Lights - Tome 1", 1), book()];
      return route.fulfill({
        json: {
          series_id: "harbour",
          series_name: "Harbour Lights",
          projects: projects.map((item) => ({ id: item.id, title: item.title, volume_number: item.volume_number, status: "created" })),
          chapters: { created: 0, unchanged: 0, replaced: 0, items: [] },
          jobs: body.start === "pipeline" ? projects.map((item) => ({ project_id: item.id, job_id: `job-${item.id}` })) : [],
          warnings: [],
          decisions: [{ index: 1, name: "Harbour Lights - extra.epub", volume_number: 2, reason: "next volume of the series" }],
          views: projects,
        },
      });
    }
    if (path === "/api/imports/session-1")
      return route.fulfill({
        json: {
          id: "session-1",
          format: "epub",
          files,
          expires_at: 1789999999,
          result: null,
          confirm_low_confidence: false,
          proposal: {
            series: { name: "Harbour Lights", confidence: "high", series_reason: "file name without its number", existing_series_id: null },
            items: files.map((file) => ({
              index: file.index,
              title: file.title,
              volume_number: file.number_confidence === "high" ? 1 : null,
              confidence: file.number_confidence,
              reason: file.number_reason,
              warnings: [],
              existing_volume: null,
            })),
            duplicate_numbers: [],
            missing_numbers: [],
          },
        },
      });
    if (path === "/api/projects/p-2/export/epub") {
      state.exports.push(path);
      return route.fulfill({ contentType: "application/epub+zip", body: "synthetic epub" });
    }
    let data: unknown = [];
    if (path === "/api/projects") data = projects;
    else if (path === "/api/providers")
      data = [
        { id: "local", kind: "openai", name: "Local model", model: "zeta-model" },
        { id: "backup", kind: "openai", name: "Backup model", model: "omega-model" },
      ];
    else if (path === "/api/projects/p-2") data = book();
    else if (path === "/api/projects/p-2/jobs") data = [job()];
    else if (path === "/api/projects/p-2/autopilot") data = view(url);
    else if (path === "/api/projects/p-2/chapters")
      data = [
        { id: "c-1", title: "Chapter 1 · The Quay", position: 0, resource: "c1.xhtml", instructions: "", analyzed: true },
        { id: "c-2", title: "Chapter 2 · Fog", position: 1, resource: "c2.xhtml", instructions: "", analyzed: true },
      ];
    else if (path === "/api/projects/p-2/segments") data = url.searchParams.get("status") ? [] : [1, 2, 3, 4, 5, 6].map(segment);
    else if (path.startsWith("/api/segments/s-")) data = segment(Number(path.split("-").at(-1)));
    await route.fulfill({ json: data });
  });
  return state;
}

const epub = (name: string) => ({ name, mimeType: "application/epub+zip", buffer: Buffer.from(`synthetic ${name}`) });

for (const width of [1440, 390]) {
  test(`from the import to the translated EPUB without a single validation click at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width > 600 ? 1000 : 844 });
    await controllableEvents(page);
    const api = await mockApi(page);
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(BASE);

    // The assistant: files, one guess applied automatically, and the whole pipeline by default.
    await page.getByRole("button", { name: "Add content" }).first().click();
    const dialog = page.getByRole("dialog", { name: "Add content" });
    await dialog.getByRole("radio", { name: /EPUB books/ }).check();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await dialog.getByRole("radio", { name: /New series/ }).check();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await dialog
      .locator('input[type="file"]')
      .setInputFiles([epub("Harbour Lights - Tome 1.epub"), epub("Harbour Lights - extra.epub")]);
    await expect(dialog.getByText("2/2 files analyzed")).toBeVisible();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await expect(dialog.getByText("No number: it will automatically get the series' next volume.")).toBeVisible();
    await expect(dialog.getByRole("checkbox", { name: /Confirm/ })).toHaveCount(0);
    await dialog.getByRole("button", { name: "Continue" }).click();
    await expect(dialog.getByText(/the autopilot takes each volume all the way to the result/)).toBeVisible();
    const pipeline = dialog.getByRole("button", { name: "Import and run the whole pipeline" });
    await expect(pipeline).toHaveClass(/btn-primary/);
    await pipeline.click();
    await expect(dialog.getByText("Import complete.")).toBeVisible();
    await expect(dialog.getByText("Harbour Lights - extra.epub: volume 2")).toBeVisible();
    await dialog.getByRole("button", { name: "Close" }).first().click();

    // The workspace follows the run: phase, round and the fallback provider it switched to.
    await page.goto(`${BASE}/#project/p-2`);
    const status = page.locator(".autopilot-status");
    await expect(status).toContainText("Autopilot running");
    await expect(status).toContainText("Phase: Final review");
    await expect(status).toContainText("Round 2 of 3");
    await expect(status).toContainText("Fallback provider: Backup model");
    await expect(status).toContainText("Nothing is expected from you");

    // The run ends; the next event brings the report and the download, nobody having clicked anything.
    api.finished = true;
    await page.evaluate(() => (window as unknown as { __libris_emit: () => void }).__libris_emit());
    await expect(status).toContainText("Autopilot finished");
    await expect(status).toContainText("Finished · 1 passage kept in the original");
    const download = page.waitForEvent("download");
    await status.getByRole("button", { name: "Download the EPUB" }).click();
    expect((await download).suggestedFilename()).toBe("Harbour Lights - extra.epub");
    expect(api.exports).toEqual(["/api/projects/p-2/export/epub"]);

    // The report: rounds, the passage kept in the original with its reason, every decision logged.
    await status.getByRole("button", { name: "See the report" }).click();
    await expect(page.getByRole("heading", { name: "Autopilot", level: 2 })).toBeVisible();
    await expect(page.getByText("Refused by every provider twice: original text kept.").first()).toBeVisible();
    const log = page.locator(".decision-log");
    await expect(log).toContainText("“Backup model” takes over.");
    await expect(log).toContainText("“Keeper” → “gardien”, used 9 times.");
    await page.locator(".residual-list").getByRole("button", { name: "See its decisions" }).click();
    await expect(log).not.toContainText("“Keeper” → “gardien”");

    // The review log is a log: nothing in it waits for a person.
    await page.getByRole("tab", { name: /Review log/ }).click();
    await expect(page.getByRole("heading", { name: "Review log" })).toBeVisible();
    await expect(page.getByText(/to validate/i)).toHaveCount(0);

    const scroll = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
    expect(scroll).toBeLessThanOrEqual(0);
    // Only the import was sent: no validation, acceptance or confirmation of any kind.
    expect(api.mutations).toEqual([
      "POST /api/imports",
      "POST /api/imports/session-1/files",
      "POST /api/imports/session-1/files",
      "POST /api/imports/session-1/commit",
    ]);
    expect(errors).toEqual([]);
  });
}

for (const width of [1440, 390]) {
  test(`installation autopilot and webhooks are saved, validated and reset from the settings at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width > 600 ? 1000 : 844 });
    await controllableEvents(page);
    const env = {
      enabled: true,
      max_rounds: 3,
      fallback_providers: ["Backup model", "retired-model"],
      outage_max_retries: 5,
      outage_max_wait_seconds: 3600,
      glossary_min_confidence: 0.75,
      identity_min_confidence: 0.8,
      bible_min_coverage: 0.8,
      stale_min_coverage: 0.5,
    };
    const hooks = { hosts: [] as string[], private_networks: [] as string[], max_attempts: 6, timeout_seconds: 10 };
    let autopilot = { values: env, defaults: env, saved: false, fallback_provider_ids: ["backup"] };
    let webhooks = { values: hooks, defaults: hooks, saved: false, secret: { configured: false, source: "none" } };
    const puts: { path: string; body: Record<string, unknown> }[] = [];
    await page.route("**/api/**", async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      const method = request.method();
      if (path.endsWith("/auth/me")) return route.fulfill({ json: { id: "admin", username: "Owner", admin: true } });
      if (path === "/api/providers")
        return route.fulfill({
          json: [
            { id: "local", kind: "openai", name: "Local model", model: "zeta-model" },
            { id: "backup", kind: "openai", name: "Backup model", model: "omega-model" },
            { id: "cloud", kind: "anthropic", name: "Cloud model", model: "sigma-model" },
          ],
        });
      if (path === "/api/settings/autopilot") {
        if (method === "PUT") {
          const body = request.postDataJSON();
          puts.push({ path, body });
          autopilot = { ...autopilot, values: body, saved: true, fallback_provider_ids: body.fallback_providers };
        } else if (method === "DELETE") autopilot = { values: env, defaults: env, saved: false, fallback_provider_ids: ["backup"] };
        return route.fulfill({ json: autopilot });
      }
      if (path === "/api/settings/webhooks") {
        if (method === "PUT") {
          const body = request.postDataJSON();
          puts.push({ path, body });
          if ((body.hosts as string[]).some((host) => host.includes("/")))
            return route.fulfill({ status: 422, json: { detail: "Invalid webhook host: https://hooks.example.org/in" } });
          webhooks = {
            ...webhooks,
            values: { hosts: body.hosts, private_networks: body.private_networks, max_attempts: body.max_attempts, timeout_seconds: body.timeout_seconds },
            saved: true,
            secret: body.secret ? { configured: true, source: "saved" } : webhooks.secret,
          };
        }
        return route.fulfill({ json: webhooks });
      }
      await route.fulfill({ json: [] });
    });
    await page.goto(`${BASE}/#settings`);
    await page.getByRole("tab", { name: "Autopilot", exact: true }).click();
    const form = page.getByRole("form", { name: "Installation autopilot" });
    await expect(page.getByText("Environment values", { exact: true })).toBeVisible();
    await expect(form.getByRole("switch", { name: /Run books on autopilot/ })).toBeChecked();
    // A name of AUTOPILOT_FALLBACK_PROVIDERS that matches no provider is pointed out.
    await expect(form.getByText("Not found among the providers (skipped): retired-model")).toBeVisible();
    await expect(form.locator(".fallback-list li")).toHaveText([/Backup model · omega-model/]);
    await form.getByLabel("Convergence rounds at most").fill("5");
    await form.getByLabel("Add a fallback provider").selectOption("cloud");
    await form.getByRole("button", { name: "Move Cloud model · sigma-model up" }).click();
    await form.getByText("Thresholds of the automatic decisions").click();
    await form.getByLabel("Minimum confidence of a glossary term").fill("0.9");
    await form.getByRole("button", { name: "Save the autopilot" }).click();
    await expect(form.getByRole("status")).toContainText("They apply to the next decisions, without a restart.");
    expect(puts.at(-1)).toEqual({
      path: "/api/settings/autopilot",
      body: { ...env, max_rounds: 5, glossary_min_confidence: 0.9, fallback_providers: ["cloud", "backup"] },
    });
    await expect(page.getByText("Values saved here", { exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await form.getByRole("button", { name: "Go back to the environment values" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Go back", exact: true }).click();
    await expect(form.getByRole("status")).toContainText("Environment values restored.");
    await expect(form.getByLabel("Convergence rounds at most")).toHaveValue("3");

    // Webhooks sit next to the API tokens; the secret is write-only.
    await page.getByRole("tab", { name: "Automation API", exact: true }).click();
    const hooksForm = page.getByRole("form", { name: "Webhooks of API requests" });
    await expect(hooksForm.getByText("Only the tokens with their own secret can receive webhooks.")).toBeVisible();
    await hooksForm.getByLabel("Allowed hosts").fill("https://hooks.example.org/in");
    await hooksForm.getByRole("button", { name: "Save the webhooks" }).click();
    await expect(page.getByText("Invalid webhook host: https://hooks.example.org/in")).toBeVisible();
    await hooksForm.getByLabel("Allowed hosts").fill("hooks.example.org\n*.example.net");
    await hooksForm.getByLabel("Allowed private networks").fill("10.0.0.0/8");
    await hooksForm.getByRole("button", { name: "Generate" }).click();
    const secret = await hooksForm.getByLabel("New global secret").inputValue();
    expect(secret.length).toBeGreaterThanOrEqual(32);
    await hooksForm.getByRole("button", { name: "Save the webhooks" }).click();
    await expect(hooksForm.getByRole("status")).toBeVisible();
    expect(puts.at(-1)).toEqual({
      path: "/api/settings/webhooks",
      body: {
        hosts: ["hooks.example.org", "*.example.net"],
        private_networks: ["10.0.0.0/8"],
        max_attempts: 6,
        timeout_seconds: 10,
        secret,
        clear_secret: false,
      },
    });
    await expect(hooksForm.getByText("Saved here", { exact: true })).toBeVisible();
    await expect(hooksForm.getByLabel("New global secret")).toHaveValue("");
    expect(await page.content()).not.toContain(secret);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  });
}
