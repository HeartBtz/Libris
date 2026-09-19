import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { projectProgress } from "../src/features/progress";
import type { Job, Project } from "../src/types";

// Cost budgets in the interface, against an in-memory stand-in of the API. Synthetic names only.

const BASE = process.env.SHOWCASE_URL || "http://127.0.0.1:4173";

const stats = {
  total: 6,
  translated: 3,
  validated: 0,
  reviewed_segments: 0,
  review_total: 6,
  flagged: 0,
  errors: 0,
  refused: 0,
  retained_source: 0,
  analyzed_segments: 6,
  synthesized_chapters: 2,
  chapters: 2,
  glossary: 0,
};

function book(): Project {
  const value: Project = {
    id: "p-1",
    owner_id: "demo-user",
    title: "Harbour Lights - Tome 1",
    author: "Demo author",
    series_name: "Harbour Lights",
    series_id: "harbour",
    volume_number: 1,
    archived_at: null,
    source_language: "en",
    target_language: "fr",
    provider_id: "local",
    quality: "high",
    context_backend: "internal",
    instructions: "",
    status: "paused",
    stats,
    updated_at: 1789254000,
    book_info: { words: 3200, images: 0, size: 64000 },
    source_format: "epub",
    project_kind: "volume",
    config: {},
    bible: { summary: "Synthetic" },
  };
  return { ...value, progress: projectProgress(value) };
}

const PAUSE = "Book budget at 100% (5.02 of 5.00): job paused. Raise the budget, then resume the job.";

async function mockBook(page: Page) {
  const state = { amount: 5 as number | null, puts: [] as unknown[], resumed: 0 };
  const job = (): Job => ({
    id: "job-1",
    status: state.resumed ? "translating" : "paused",
    operation: "analyze",
    error: state.resumed ? "" : PAUSE,
    stop_reason: state.resumed ? "" : "budget_exceeded",
    next_attempt: 0,
    outage_count: 0,
    attempts: 1,
    provider_id: "local",
    options: { autopilot: true, budget: { estimate: 7.4, warning: "Estimated cost 7.40 for 4.00 left (Book budget): the job will be paused near the cap." } },
    checkpoint: { step: "translation", current: 3, total: 6 },
    created_at: 1789250000,
    finished_at: null,
  });
  const budget = () => {
    const amount = state.amount;
    const ratio = amount ? 5.02 / amount : 0;
    return {
      amount,
      own_amount: amount,
      default_amount: null,
      spent: 5.02,
      remaining: amount ? Math.max(0, amount - 5.02) : null,
      ratio,
      state: !amount ? "none" : ratio >= 1 ? "exceeded" : ratio >= 0.9 ? "near" : "ok",
      switch_threshold: 0.9,
      on_estimate: "warn",
      priced: true,
      currency_note: "Current prices of the provider “Local model” per million tokens.",
      last_job: {
        id: "job-1",
        status: job().status,
        stop_reason: job().stop_reason,
        estimated: 7.4,
        actual: 4.02,
        budget: amount,
        book_spent: 5.02,
        warning: null,
        paused_for_budget: !state.resumed,
        provider_switches: 1,
      },
    };
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    if (path.endsWith("/events")) return route.fulfill({ contentType: "text/event-stream", body: ": demo\n\n" });
    let data: unknown = [];
    if (path === "/api/auth/me") data = { id: "demo-user", username: "Demo", admin: true };
    else if (path === "/api/projects/p-1/budget" && method === "PUT") {
      const body = request.postDataJSON() as { amount: number | null };
      state.puts.push(body);
      state.amount = body.amount;
      data = budget();
    } else if (path === "/api/projects/p-1/budget") data = budget();
    else if (path === "/api/projects/p-1/jobs/job-1/resume") {
      state.resumed += 1;
      data = job();
    } else if (path === "/api/projects/p-1") data = book();
    else if (path === "/api/projects/p-1/jobs") data = [job()];
    else if (path === "/api/projects/p-1/autopilot")
      data = {
        enabled: true,
        settings: { max_rounds: 3, fallback_provider_ids: [], outage_max_retries: 5, outage_max_wait_seconds: 3600 },
        report: null,
        decisions: { items: [], total: 0, limit: 5, offset: 0 },
      };
    else if (path === "/api/projects/p-1/chapters")
      data = [{ id: "c-1", title: "Chapter 1 · The Quay", position: 0, resource: "c1.xhtml", instructions: "", analyzed: true }];
    else if (path === "/api/providers") data = [{ id: "local", kind: "openai", name: "Local model", model: "zeta-model" }];
    else if (path === "/api/projects/p-1/segments") data = [];
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

async function noOverflow(page: Page, width: number) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
}

for (const width of [1440, 390]) {
  test(`a job paused by the book budget says why and resumes once the budget is raised at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width > 600 ? 1000 : 844 });
    await english(page);
    const api = await mockBook(page);
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${BASE}/#project/p-1`);

    const notice = page.getByRole("status").filter({ hasText: "Budget reached: job paused" });
    await expect(notice).toContainText(PAUSE);
    await expect(page.getByText("Paused manually — use Resume to continue.")).toHaveCount(0);
    const raise = notice.getByRole("button", { name: "Raise the budget" });
    expect((await raise.boundingBox())?.height).toBeGreaterThanOrEqual(width < 720 ? 40 : 30);
    await raise.click();

    const card = page.locator("section").filter({ has: page.getByRole("heading", { name: "Book budget" }) });
    await expect(card).toContainText("Cap reached");
    await expect(card.getByRole("progressbar", { name: "Budget used" })).toBeVisible();
    await expect(card).toContainText("Estimated cost");
    await expect(card).toContainText("7.40");
    await expect(card).toContainText("4.02");
    await expect(card).toContainText("1 switch to a cheaper provider");
    await expect(card.getByLabel("Budget of this book")).toHaveValue("own");
    await card.getByLabel("Cap amount").fill("20");
    const save = card.getByRole("button", { name: "Save the budget" });
    expect((await save.boundingBox())?.height).toBeGreaterThanOrEqual(width < 720 ? 40 : 30);
    await save.click();
    await expect(card.getByRole("status")).toContainText("Budget saved.");
    await expect(card).toContainText("Within budget");
    expect(api.puts).toEqual([{ amount: 20 }]);
    await noOverflow(page, width);

    await page.getByRole("button", { name: "Resume", exact: true }).click();
    await expect.poll(() => api.resumed).toBe(1);
    // No budget of its own: the installation's default applies again.
    await card.getByLabel("Budget of this book").selectOption("default");
    await card.getByRole("button", { name: "Save the budget" }).click();
    await expect.poll(() => api.puts).toEqual([{ amount: 20 }, { amount: null }]);
    expect(errors).toEqual([]);
  });

  test(`administrators set the installation budgets and token caps at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await english(page);
    const saved: unknown[] = [];
    const created: unknown[] = [];
    const tokenBudgets: unknown[] = [];
    let values = { default_book: 0, switch_threshold: 0.9, on_estimate: "warn" };
    const token = {
      id: "t1",
      name: "Nightly import",
      prefix: "lbr_zz99yy88",
      scopes: ["content:write", "pipeline:start"],
      created_at: 1_800_000_000,
      expires_at: null,
      revoked_at: null,
      last_used_at: 1_800_100_000,
      state: "active",
      budget: { amount: 10, period: "month", spent: 10.5, resets_at: 1_801_000_000 } as unknown,
    };
    await page.route("**/api/**", async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      const method = request.method();
      let body: unknown = [];
      if (path === "/api/auth/me") body = { id: "admin", username: "owner", admin: true, active: true };
      else if (path === "/api/settings/budget" && method === "PUT") {
        values = request.postDataJSON();
        saved.push(values);
        body = { values, defaults: { default_book: 0, switch_threshold: 0.9, on_estimate: "warn" }, saved: true };
      } else if (path === "/api/settings/budget")
        body = { values, defaults: { default_book: 0, switch_threshold: 0.9, on_estimate: "warn" }, saved: false };
      else if (path === "/api/tokens/t1/budget") {
        const input = request.postDataJSON() as { amount: number | null; period: string };
        tokenBudgets.push(input);
        token.budget = input.amount ? { amount: input.amount, period: input.period, spent: 10.5, resets_at: null } : null;
        body = token;
      } else if (path === "/api/tokens" && method === "POST") {
        created.push(request.postDataJSON());
        body = { ...token, id: "t2", name: "Budgeted", budget: null, token: "lbr_ab12cd34_synthetic-secret-0123456789" };
      } else if (path === "/api/tokens") body = [token];
      await route.fulfill({ json: body });
    });
    await page.goto(`${BASE}/#settings`);
    await page.getByRole("tab", { name: "Budgets", exact: true }).click();
    const form = page.getByRole("form", { name: "Installation budgets" });
    await expect(page.getByText("Environment values", { exact: true })).toBeVisible();
    await form.getByLabel("Default cap of a book").fill("12.5");
    await form.getByLabel("Switch threshold (%)").fill("80");
    await form.getByLabel("Estimate above what is left").selectOption("refuse");
    await form.getByRole("button", { name: "Save the budgets" }).click();
    await expect(form.getByRole("status")).toContainText("Settings saved.");
    expect(saved).toEqual([{ default_book: 12.5, switch_threshold: 0.8, on_estimate: "refuse" }]);
    await noOverflow(page, width);

    await page.getByRole("tab", { name: "Automation API", exact: true }).click();
    const nightly = page.getByRole("list", { name: "API tokens" }).getByRole("listitem").filter({ hasText: "Nightly import" });
    await expect(nightly).toContainText("Budget: 10.50 of 10.00 this month");
    await nightly.getByText("Change the budget").click();
    const edit = nightly.getByRole("form", { name: "Budget of Nightly import" });
    await edit.getByLabel("Token budget").fill("50");
    await edit.getByLabel("Period").selectOption("total");
    const saveToken = edit.getByRole("button", { name: "Save the budget" });
    expect((await saveToken.boundingBox())?.height).toBeGreaterThanOrEqual(width < 720 ? 40 : 30);
    await saveToken.click();
    await expect(nightly).toContainText("Budget: 10.50 of 50.00 in total");
    expect(tokenBudgets).toEqual([{ amount: 50, period: "total" }]);

    const create = page.locator("section").filter({ has: page.getByRole("heading", { name: "Create a token" }) });
    await create.getByLabel("Token name").fill("Budgeted");
    await create.getByLabel("Token budget").fill("25");
    await create.getByRole("button", { name: "Create a token" }).click();
    await expect(page.getByLabel("Token secret", { exact: true })).toBeVisible();
    expect(created).toEqual([
      {
        name: "Budgeted",
        scopes: ["jobs:read", "results:read"],
        expires_in_days: 90,
        budget_amount: 25,
        budget_period: "month",
      },
    ]);
    await noOverflow(page, width);
  });
}
