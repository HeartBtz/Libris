import { expect, test } from "@playwright/test";

test("switches the application shell to English", async ({ page }) => {
  await page.goto(process.env.SHOWCASE_URL || "http://127.0.0.1:4173");
  await expect(page.getByRole("heading", { name: "Retrouvez vos livres" })).toBeVisible();
  await page.getByRole("combobox", { name: "Langue" }).selectOption("en");
  await expect(page.getByRole("heading", { name: "Find your books" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Find your books" })).toBeVisible();
});
