import { expect, test } from "@playwright/test";

for (const width of [1440, 390]) test(`configures an explicit reasoning level at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  const provider = {
    id: "qwen",
    kind: "openai",
    name: "Qwen",
    base_url: "http://qwen.test/v1",
    model: "qwen3.8-27b-uncensored",
    context_window: 262000,
    max_output_tokens: 16384,
    temperature: 0.2,
    top_p: 0.9,
    timeout: 900,
    max_concurrency: 2,
    input_cost: 0,
    output_cost: 0,
    capabilities: {
      supports_json_schema: true,
      supports_json_object: true,
      supports_reasoning: true,
      supports_tool_calls: false,
      reasoning_effort: "medium",
      max_tokens_parameter: "max_tokens",
    },
  };
  let saved: typeof provider | undefined;
  await page.route("**/api/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/auth/me") {
      await route.fulfill({ json: { id: "owner", username: "owner", admin: true } });
    } else if (path === "/api/providers" && request.method() === "GET") {
      await route.fulfill({ json: [provider] });
    } else if (path === "/api/providers/qwen" && request.method() === "PUT") {
      saved = { ...provider, ...request.postDataJSON() };
      await route.fulfill({ json: saved });
    } else {
      await route.fulfill({ json: [] });
    }
  });

  await page.goto(`${process.env.SHOWCASE_URL || "http://127.0.0.1:4173"}/#settings`);
  await page.getByRole("button", { name: /Qwen/ }).click();
  await expect(page.getByLabel("Reasoning level")).toHaveValue("medium");
  await page.getByLabel("Reasoning level").selectOption("none");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Provider saved");
  expect(saved?.capabilities).toMatchObject({
    supports_reasoning: true,
    reasoning_effort: "none",
  });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
});
