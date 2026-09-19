import { expect, test } from "@playwright/test";

// Prompt versions in Settings, against an in-memory stand-in of the API. Synthetic content only.

const BASE = process.env.SHOWCASE_URL || "http://127.0.0.1:4173";
const BUILTIN = "Built-in polishing prompt for {target_language}.";

for (const width of [1440, 390]) {
  test(`prompt versions can be restored at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript(() => localStorage.setItem("locale", "en"));
    // Saved versions; an empty content stands for the built-in prompt.
    const saved: { version: number; content: string; created_at: number }[] = [
      { version: 1, content: "First custom polishing prompt.", created_at: 1780000000 },
      { version: 2, content: "Second custom polishing prompt.", created_at: 1780003600 },
    ];
    const calls: { path: string; body: unknown }[] = [];
    const latest = () => saved.at(-1);
    const view = () => ({
      name: "polishing",
      version: latest()?.version ?? 0,
      content: latest()?.content || BUILTIN,
      builtin: !latest()?.content,
      updated_at: latest()?.created_at ?? null,
    });
    const add = (content: string) =>
      saved.push({ version: (latest()?.version ?? 0) + 1, content, created_at: 1780007200 + saved.length });
    await page.route("**/api/**", async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (path.endsWith("/auth/me"))
        return route.fulfill({ json: { id: "admin", username: "Admin", admin: true, active: true } });
      if (path === "/api/prompts") return route.fulfill({ json: [view()] });
      if (path === "/api/prompts/polishing/versions") {
        const current = latest()?.version ?? 0;
        return route.fulfill({
          json: [
            ...[...saved].reverse().map((v) => ({
              version: v.version,
              created_at: v.created_at,
              builtin: !v.content,
              content: v.content || BUILTIN,
              current: v.version === current,
            })),
            { version: 0, created_at: null, builtin: true, content: BUILTIN, current: current === 0 },
          ],
        });
      }
      if (path === "/api/prompts/polishing/restore") {
        const body = request.postDataJSON() as { version: number };
        calls.push({ path, body });
        add(body.version === 0 ? "" : saved.find((v) => v.version === body.version)!.content);
        return route.fulfill({ json: view() });
      }
      return route.fulfill({ json: [] });
    });
    await page.goto(`${BASE}/#settings`);
    await page.getByRole("tab", { name: "Prompts", exact: true }).click();
    const editor = page.getByLabel("Prompt content");
    await expect(editor).toHaveValue("Second custom polishing prompt.");
    const history = page.getByRole("list", { name: "Version history" });
    await expect(history.getByRole("listitem")).toHaveCount(3);
    await expect(history.getByRole("listitem").first()).toContainText("In force");
    await expect(page.getByRole("button", { name: "Restore: Version 2" })).toBeDisabled();

    await page.getByRole("button", { name: "Restore: Version 1" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("Restore version 1?");
    await dialog.getByRole("button", { name: "Restore", exact: true }).click();
    await expect(page.getByRole("status").filter({ hasText: "Version restored." })).toBeVisible();
    await expect(editor).toHaveValue("First custom polishing prompt.");
    expect(calls.at(-1)).toEqual({ path: "/api/prompts/polishing/restore", body: { version: 1 } });
    await expect(history.getByRole("listitem")).toHaveCount(4);

    await page.getByRole("button", { name: "Go back to the original prompt" }).click();
    await expect(page.getByRole("dialog")).toContainText("Go back to the original prompt?");
    await page.getByRole("dialog").getByRole("button", { name: "Go back to the original" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Original prompt restored." })).toBeVisible();
    await expect(editor).toHaveValue(BUILTIN);
    expect(calls.at(-1)).toEqual({ path: "/api/prompts/polishing/restore", body: { version: 0 } });
    await expect(page.getByRole("button", { name: "Go back to the original prompt" })).toBeDisabled();
    await expect(history.getByRole("listitem").first()).toContainText("Version 4 · back to the original");
    await expect(history.getByRole("listitem").last()).toContainText("Shipped with Libris");
    await expect(page.getByRole("button", { name: "Restore: Version initial" })).toBeDisabled();

    const restore = await page.getByRole("button", { name: "Restore: Version 1" }).boundingBox();
    if (width < 900) expect(restore!.height).toBeGreaterThanOrEqual(40);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
  });
}
