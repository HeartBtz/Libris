import { expect, test } from "@playwright/test";

const BASE = process.env.SHOWCASE_URL || "http://127.0.0.1:4173";

function entry(overrides: Record<string, unknown>) {
  return {
    job_id: "j1",
    project_id: "p1",
    title: "Synthetic Saga · Volume 1",
    owner: "reader",
    mine: true,
    provider_id: "prov",
    provider: "Local model",
    operation: "analyze",
    status: "pending",
    priority: "normal",
    effective_priority: "normal",
    queued_at: 1_800_000_000,
    next_attempt: 0,
    position: 1,
    reason: "provider_busy",
    ...overrides,
  };
}

for (const width of [1440, 390]) {
  test(`the queue shows each waiting job's place and reason at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript(() => localStorage.setItem("locale", "en"));
    const changes: unknown[] = [];
    const view = {
      running: [entry({ job_id: "j0", project_id: "p0", title: "Running volume", status: "translating", position: null, reason: "running" })],
      waiting: [
        entry({}),
        entry({
          job_id: "j2",
          project_id: "p2",
          title: "A very long synthetic volume title that must wrap on small screens without overflowing",
          priority: "low",
          effective_priority: "normal",
          position: 2,
          reason: "account_limit",
        }),
        entry({ job_id: "j3", project_id: "p3", title: "Retrying volume", status: "waiting", position: null, reason: "retry_scheduled", next_attempt: 1_800_000_600 }),
      ],
      totals: { waiting: 5, running: 2 },
      account: { running: 1, waiting: 3, max_running: 2, max_queued: 10, max_priority: "normal" },
      aging_minutes: 60,
    };
    await page.route("**/api/**", async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      let body: unknown = [];
      if (path === "/api/auth/me") body = { id: "u1", username: "reader", admin: false, active: true };
      else if (path === "/api/queue") body = view;
      else if (path.startsWith("/api/queue/") && request.method() === "PUT") {
        const input = request.postDataJSON() as { priority: string };
        changes.push({ job: path.split("/")[3], ...input });
        view.waiting[0].priority = input.priority;
        body = { job_id: path.split("/")[3], priority: input.priority };
      }
      await route.fulfill({ json: body });
    });
    await page.goto(`${BASE}/#queue`);
    await expect(page.getByRole("heading", { name: "Queue", exact: true })).toBeVisible();
    const waiting = page.getByRole("list", { name: "Pending" });
    const first = waiting.getByRole("listitem").filter({ hasText: "Synthetic Saga · Volume 1" });
    await expect(first).toContainText("Position 1");
    await expect(first).toContainText("The provider Local model is busy");
    const second = waiting.getByRole("listitem").filter({ hasText: "A very long synthetic" });
    await expect(second).toContainText("Low → Normal (raised by waiting)");
    await expect(second).toContainText("The account has reached its number of simultaneous jobs");
    await expect(waiting.getByRole("listitem").filter({ hasText: "Retrying volume" })).toContainText("next attempt on");
    await expect(page.getByRole("list", { name: "In progress" })).toContainText("Running volume");
    await expect(page.getByText("2 at most")).toBeVisible();

    const select = page.getByLabel("Priority of Synthetic Saga · Volume 1");
    await expect(select.locator("option[value=high]")).toHaveAttribute("disabled", "");
    await select.selectOption("low");
    await expect(page.getByRole("status")).toContainText("Priority saved.");
    expect(changes).toEqual([{ job: "j1", priority: "low" }]);

    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    const box = await select.boundingBox();
    expect(box?.height).toBeGreaterThanOrEqual(width < 720 ? 40 : 28);
  });
}

test("an administrator sets the queue quotas per account", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  const saved: unknown[] = [];
  const admin = {
    values: { max_running_per_account: 0, max_queued_per_account: 0, aging_minutes: 60 },
    defaults: { max_running_per_account: 0, max_queued_per_account: 0, aging_minutes: 60 },
    accounts: [] as unknown[],
    saved: false,
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let body: unknown = [];
    if (path === "/api/auth/me") body = { id: "a1", username: "owner", admin: true, active: true };
    else if (path === "/api/users")
      body = [
        { id: "a1", username: "owner", admin: true, active: true },
        { id: "u2", username: "translator", admin: false, active: true },
      ];
    else if (path === "/api/settings/queue" && request.method() === "PUT") {
      const input = request.postDataJSON() as typeof admin.values & { accounts: unknown[] };
      saved.push(input);
      body = { ...admin, values: input, accounts: input.accounts, saved: true };
    } else if (path === "/api/settings/queue") body = admin;
    await route.fulfill({ json: body });
  });
  await page.goto(`${BASE}/#settings`);
  await page.getByRole("tab", { name: "Queue", exact: true }).click();
  const form = page.getByRole("form", { name: "Fair queue" });
  await form.getByLabel("Simultaneous jobs per account").first().fill("2");
  await form.getByLabel("Waiting jobs per account").first().fill("20");
  await form.getByLabel("Add an account").selectOption({ label: "translator" });
  const row = form.locator(".queue-account").filter({ hasText: "translator" });
  await row.getByLabel("Simultaneous jobs per account").fill("4");
  await row.getByLabel("Highest priority").selectOption("high");
  await form.getByRole("button", { name: "Save the queue" }).click();
  await expect(form.getByRole("status")).toContainText("Settings saved");
  expect(saved).toEqual([
    {
      max_running_per_account: 2,
      max_queued_per_account: 20,
      aging_minutes: 60,
      accounts: [{ user_id: "u2", max_running: 4, max_queued: null, max_priority: "high" }],
    },
  ]);
  await expect(page.getByText("Values saved here")).toBeVisible();
});
