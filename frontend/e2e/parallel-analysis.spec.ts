import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { projectProgress } from "../src/features/progress";
import type { Job, Project } from "../src/types";

// The parallel analysis in the interface: the phase of a running analysis and the volume's analysis
// mode and threads, against an in-memory stand-in of the API. Synthetic names only.

const BASE = process.env.SHOWCASE_URL || "http://127.0.0.1:4173";

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
  chapters: 4,
  glossary: 0,
};

function book(config: Project["config"]): Project {
  const value: Project = {
    id: "p-1",
    owner_id: "demo-user",
    title: "Lantern Road - Book 2",
    author: "Demo author",
    series_name: "Lantern Road",
    series_id: "lantern",
    volume_number: 2,
    archived_at: null,
    source_language: "en",
    target_language: "fr",
    provider_id: "local",
    quality: "high",
    context_backend: "internal",
    instructions: "",
    status: "analyzing",
    stats,
    updated_at: 1789254000,
    book_info: { words: 5200, images: 0, size: 64000 },
    source_format: "txt",
    project_kind: "volume",
    config,
    bible: {},
  };
  const progress = projectProgress(value);
  const analysis = progress.stages.find((stage) => stage.key === "analysis")!;
  analysis.percent = 68;
  return {
    ...value,
    progress: {
      ...progress,
      active_stage: "analysis",
      current: analysis,
      analysis_phase: { step: "reconciliation", current: 5, total: 10, percent: 68 },
    },
  };
}

const job: Job = {
  id: "job-1",
  status: "analyzing",
  operation: "analyze",
  error: "",
  stop_reason: "",
  next_attempt: 0,
  outage_count: 0,
  attempts: 1,
  provider_id: "local",
  options: { autopilot: true, analysis_mode: "parallel" },
  checkpoint: { step: "reconciliation", current: 5, total: 10 },
  created_at: 1789250000,
  finished_at: null,
};

async function mockBook(page: Page) {
  const state = { config: {} as Project["config"], puts: [] as Record<string, unknown>[] };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    if (path.endsWith("/events")) return route.fulfill({ contentType: "text/event-stream", body: ": demo\n\n" });
    let data: unknown = [];
    if (path === "/api/auth/me") data = { id: "demo-user", username: "Demo", admin: true };
    else if (path === "/api/projects/p-1" && method === "PUT") {
      const body = request.postDataJSON() as Record<string, unknown>;
      state.puts.push(body);
      state.config = { ...state.config, analysis_mode: body.analysis_mode as "strict", threads: body.threads as number };
      data = book(state.config);
    } else if (path === "/api/projects/p-1") data = book(state.config);
    else if (path === "/api/projects/p-1/jobs") data = [job];
    else if (path === "/api/projects/p-1/autopilot")
      data = {
        enabled: true,
        settings: { max_rounds: 3, fallback_provider_ids: [], outage_max_retries: 5, outage_max_wait_seconds: 3600 },
        report: null,
        decisions: { items: [], total: 0, limit: 5, offset: 0 },
      };
    else if (path === "/api/projects/p-1/budget")
      data = {
        amount: null,
        own_amount: null,
        default_amount: null,
        spent: 0,
        remaining: null,
        ratio: 0,
        state: "none",
        switch_threshold: 0.9,
        on_estimate: "warn",
        priced: false,
        currency_note: "",
        last_job: null,
      };
    else if (path === "/api/projects/p-1/chapters")
      data = [{ id: "c-1", title: "Chapter 1 · The Ferry", position: 0, resource: "c1", instructions: "", analyzed: false }];
    else if (path === "/api/providers") data = [{ id: "local", kind: "openai", name: "Local model", model: "zeta-model" }];
    await route.fulfill({ json: data });
  });
  return state;
}

async function english(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("locale", "en");
    class QuietEventSource extends EventTarget {
      readyState = 1;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      constructor(public url: string) {
        super();
      }
      close() {
        this.readyState = 2;
      }
    }
    Object.defineProperty(window, "EventSource", { value: QuietEventSource });
  });
}

for (const width of [1440, 390]) {
  test(`a parallel analysis shows its phase and the volume chooses its mode and threads at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: width > 600 ? 1000 : 844 });
    await english(page);
    const api = await mockBook(page);
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${BASE}/#project/p-1`);

    await expect(page.getByText("Chronological reconciliation · 5 / 10").first()).toBeVisible();

    await page.getByRole("tab", { name: "Settings", exact: true }).click();
    const mode = page.getByLabel("Analysis mode");
    await expect(mode).toHaveValue("");
    await mode.selectOption("strict");
    const threads = page.getByLabel("Passages worked on at once");
    await threads.fill("4");
    expect((await threads.boundingBox())?.height).toBeGreaterThanOrEqual(width < 720 ? 40 : 30);
    await page.getByRole("button", { name: "Save settings" }).click();
    await expect.poll(() => api.puts.length).toBe(1);
    expect(api.puts[0]).toMatchObject({ analysis_mode: "strict", threads: 4 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    expect(errors).toEqual([]);
  });
}
