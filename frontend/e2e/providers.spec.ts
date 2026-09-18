import { expect, test } from "@playwright/test";

test("an Anthropic provider can be created from the settings", async ({
  page,
}) => {
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  let created: Record<string, unknown> | undefined;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/auth/me")
      await route.fulfill({
        json: { id: "owner", username: "owner", admin: true },
      });
    else if (path === "/api/providers" && request.method() === "POST") {
      created = request.postDataJSON();
      await route.fulfill({
        status: 201,
        json: { ...created, id: "claude", api_key: undefined, has_api_key: true },
      });
    } else await route.fulfill({ json: [] });
  });
  await page.goto(
    `${process.env.SHOWCASE_URL || "http://127.0.0.1:4173"}/#settings`,
  );
  await page
    .getByLabel("Connection / protocol")
    .selectOption({ label: "Anthropic · Claude (API key)" });
  await expect(page.getByLabel("Base URL")).toHaveValue(
    "https://api.anthropic.com",
  );
  const key = page.getByLabel("API key", { exact: true });
  await expect(key).toHaveAttribute("required", "");
  await expect(key).toHaveAttribute("placeholder", "Required");
  await expect(page.getByLabel("Temperature")).toHaveCount(0);
  await expect(page.getByLabel("Top P")).toHaveCount(0);
  await expect(page.getByText("Inference uses the Anthropic API")).toBeVisible();
  await page.getByLabel("Name", { exact: true }).fill("Claude");
  await page.getByLabel("Model", { exact: true }).fill("claude-opus-5");
  await key.fill("sk-ant-test");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Provider saved");
  expect(created).toMatchObject({
    kind: "anthropic",
    base_url: "https://api.anthropic.com",
    model: "claude-opus-5",
    api_key: "sk-ant-test",
  });
  // A new provider form never inherits the key typed for the previous one.
  await page.getByRole("button", { name: "New provider" }).click();
  await expect(page.getByLabel("API key", { exact: true })).toHaveValue("");
});
