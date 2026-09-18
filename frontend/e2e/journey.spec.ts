import { expect, test } from "@playwright/test";
import { readFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { base, password, username } from "./integration-config";

// Runs against the disposable test stack (real API and worker, backend/tests/mock_server.py as the
// model). LIBRIS_E2E_MOCK_LLM_URL is the mock's address as seen by the backend.
const mockUrl = (process.env.LIBRIS_E2E_MOCK_LLM_URL || "http://mock-llm:8091").replace(/\/$/, "");
const fixture = fileURLToPath(new URL("./fixtures/journey.epub", import.meta.url));

// A stuck step must fail with its own message, not only when the whole journey times out.
test.use({ actionTimeout: 20_000 });

interface ProjectState {
  status: string;
  stats: { total: number; translated: number; analyzed_segments: number; flagged: number };
}

test("@journey import, mock provider, analysis, translation, validation and EPUB export", async ({ page }) => {
  test.setTimeout(8 * 60_000);
  const suffix = Date.now().toString(36);
  const providerName = `Journey mock ${suffix}`;
  const created: { project?: string; provider?: string } = {};
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  // Passages are translated one by one: remove the mock's artificial latency when it is reachable.
  await page.request.post(`${mockUrl}/control`, { data: { available: true, delay: 0 } }).catch(() => undefined);

  try {
    await page.goto(base);
    await page.getByLabel("Username", { exact: true }).fill(username);
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Library", exact: true })).toBeVisible();

    // 1. Register the synthetic model through the settings screen.
    await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("link", { name: "Settings" }).click();
    await page.getByRole("button", { name: "New provider" }).click();
    await page.getByLabel("Connection / protocol").selectOption("openai");
    await page.getByLabel("Name", { exact: true }).fill(providerName);
    await page.getByLabel("Base URL").fill(`${mockUrl}/v1`);
    await page.getByLabel("Model", { exact: true }).fill("synthetic-literary-test");
    await page.getByLabel("Context window").fill("64000");
    await page.getByText("Declared capabilities").click();
    await page.getByLabel("supports_json_schema").check();
    const savedProvider = page.waitForResponse(
      (response) => response.url().endsWith("/api/providers") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Save", exact: true }).click();
    const provider = await savedProvider;
    expect(provider.status()).toBeLessThan(300);
    created.provider = (await provider.json()).id;
    await expect(page.getByRole("status").filter({ hasText: "Provider saved" })).toBeVisible();

    // 2. Import the EPUB as a standalone volume through the assistant, then open it.
    await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("link", { name: "Library" }).click();
    await page.getByRole("button", { name: "Add content", exact: true }).first().click();
    const assistant = page.getByRole("dialog", { name: "Add content" });
    await assistant.getByRole("radio", { name: /EPUB books/ }).check();
    await assistant.getByRole("button", { name: "Continue" }).click();
    await assistant.getByRole("radio", { name: /Standalone volume/ }).check();
    await assistant.getByRole("button", { name: "Continue" }).click();
    await assistant.locator('input[type="file"]').setInputFiles({
      name: `journey-${suffix}.epub`,
      mimeType: "application/epub+zip",
      buffer: readFileSync(fixture),
    });
    await expect(assistant.getByText("1/1 files analyzed")).toBeVisible();
    await assistant.getByRole("button", { name: "Continue" }).click();
    await assistant.getByRole("button", { name: "Continue" }).click();
    const imported = page.waitForResponse(
      (response) => response.url().endsWith("/commit") && response.request().method() === "POST",
    );
    await assistant.getByRole("button", { name: "Import only" }).click();
    const project = await imported;
    expect(project.status()).toBe(200);
    created.project = (await project.json()).projects[0].id;
    await assistant.getByRole("button", { name: "Open the volume" }).click();
    await expect(page).toHaveURL(new RegExp(`#project/${created.project}$`));
    await expect(page.getByRole("heading", { level: 1 })).toContainText("The Silver Tower");

    // 3. Choosing the provider in the book settings starts analysis, then translation.
    await page.getByRole("tab", { name: "Settings", exact: true }).click();
    await page.getByRole("combobox", { name: "Provider", exact: true }).selectOption({ label: `${providerName} · synthetic-literary-test` });
    await page.getByRole("button", { name: "Save settings" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Configuration saved." })).toBeVisible();

    const state = async () =>
      (await (await page.request.get(`${base}/api/projects/${created.project}`)).json()) as ProjectState;
    await expect
      .poll(async () => (await state()).stats.analyzed_segments, { timeout: 180_000, intervals: [2000] })
      .toBeGreaterThan(0);
    await expect
      .poll(
        async () => {
          const current = await state();
          return current.stats.total > 0 && current.stats.translated === current.stats.total;
        },
        { timeout: 300_000, intervals: [3000] },
      )
      .toBe(true);
    await expect
      .poll(async () => ["pending", "analyzing", "translating", "reviewing", "syncing"].includes((await state()).status), {
        timeout: 180_000,
        intervals: [3000],
      })
      .toBe(false);
    await expect(page.getByRole("button", { name: "Stage 3: Translation" })).toBeVisible();

    // 4. Validate the first passage from the editor.
    await page.getByRole("tab", { name: "Translation", exact: true }).click();
    const firstPassage = page.getByRole("textbox", { name: "Translation passage 1 unit 1" });
    await expect(firstPassage).not.toHaveValue("");
    const validated = page.waitForResponse(
      (response) => response.url().includes("/api/segments/") && response.request().method() === "PUT",
    );
    await firstPassage
      .locator("xpath=ancestor::article")
      .getByRole("button", { name: "Validate", exact: true })
      .click();
    const decision = await validated;
    expect(decision.status()).toBe(200);
    expect((await decision.json()).validated).toBe(true);
    await expect(firstPassage.locator("xpath=ancestor::article").getByText("Human-validated")).toBeVisible();

    // 5. Export the translated EPUB.
    await page.getByRole("button", { name: "Export", exact: true }).click();
    const download = page.waitForEvent("download");
    await page.getByRole("menuitem", { name: "Translated EPUB", exact: true }).click();
    const file = await download;
    expect(file.suggestedFilename()).toMatch(/\.epub$/);
    const path = await file.path();
    expect(statSync(path).size).toBeGreaterThan(1000);
    expect(readFileSync(path).subarray(0, 2).toString()).toBe("PK");
    await expect(page.getByText("File received; download sent to the browser.")).toBeVisible();
    expect(errors).toEqual([]);
  } finally {
    if (created.project) {
      for (const job of (await (await page.request.get(`${base}/api/projects/${created.project}/jobs`)).json()) as {
        id: string;
        status: string;
      }[])
        if (!["completed", "cancelled", "failed"].includes(job.status))
          await page.request.post(`${base}/api/projects/${created.project}/jobs/${job.id}/cancel`);
      await page.request.delete(`${base}/api/projects/${created.project}?stop_jobs=true`);
    }
    if (created.provider) await page.request.delete(`${base}/api/providers/${created.provider}`);
  }
});
