import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { base, password, username } from "./integration-config";

// Local credentials are consumed only by the test process; no auth state or secrets are exported.
const state = process.env.LIBRIS_E2E_PROJECT_ID
  ? { project_id: process.env.LIBRIS_E2E_PROJECT_ID }
  : (JSON.parse(
      readFileSync(
        process.env.LIBRIS_E2E_STATE || "/tmp/libris/epub-smoke.json",
        "utf8",
      ),
    ) as { project_id: string });

test.beforeEach(async ({ page }) => {
  await page.goto(base);
  await page.getByLabel("Utilisateur", { exact: true }).fill(username);
  await page.getByLabel("Mot de passe", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Se connecter", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Bibliothèque", exact: true }),
  ).toBeVisible();
});

test("workspace, manual correction, history, inspector and preview", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`${base}/#project/${state.project_id}`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(
    "Silver Tower",
  );
  await page.getByRole("button", { name: /02 Chapter One/ }).click();
  const field = page.getByRole("textbox", {
    name: "Traduction passage 4 unité 1",
    exact: true,
  });
  await expect(field).not.toHaveValue("");
  await field.fill("Chapitre Un — correction humaine");
  await expect(field).toHaveValue("Chapitre Un — correction humaine");
  const savedResponse = page.waitForResponse(
    (r) => r.url().includes("/api/segments/") && r.request().method() === "PUT",
  );
  await field
    .locator("xpath=ancestor::article")
    .getByRole("button", { name: "Enregistrer", exact: true })
    .click();
  const saved = await (await savedResponse).json();
  expect(saved.translated_units[0].text).toBe(
    "Chapitre Un — correction humaine",
  );
  await expect(
    page.getByText("Correction humaine protégée").first(),
  ).toBeVisible();
  await field
    .locator("xpath=ancestor::article")
    .getByRole("button", { name: "Contexte / historique / Ask AI" })
    .click();
  await expect(
    page.getByRole("dialog", { name: "Inspecteur du passage" }),
  ).toBeVisible();
  await expect(
    page.getByText("Contexte réellement sélectionné / écarté"),
  ).toBeVisible();
  await page.getByRole("button", { name: "Fermer l’inspecteur" }).focus();
  await page.keyboard.press("Shift+Tab");
  await expect(page.getByText("Réponse brute du provider")).toBeFocused();
  await page.getByRole("button", { name: "Historique", exact: true }).click();
  await expect(page.getByText(/human ·/).first()).toBeVisible();
  await page.getByRole("button", { name: "Fermer l’inspecteur" }).click();
  await page.getByRole("button", { name: "Prévisualiser" }).click();
  const closePreview = page.getByRole("button", {
    name: "Fermer",
    exact: true,
  });
  await expect(closePreview).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.locator('iframe[title="Rendu du chapitre"]')).toBeFocused();
  await expect(
    page
      .frameLocator('iframe[title="Rendu du chapitre"]')
      .getByText("Chapitre Un — correction humaine"),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(closePreview).toBeHidden();
  await page.screenshot({
    path: "/tmp/libris/epub-workspace.png",
    fullPage: false,
  });
  expect(errors).toEqual([]);
});

test("OpenViking settings and responsive layout", async ({ page }) => {
  await page.getByRole("link", { name: "Paramètres", exact: true }).click();
  await page
    .getByRole("button", { name: "Mémoire · OpenViking", exact: true })
    .click();
  await expect(page.getByLabel("URL OpenViking")).toBeVisible();
  await expect(page.getByLabel("Racine dédiée viking://")).toHaveValue(
    "viking://resources/epub-translator",
  );
  await page.screenshot({ path: "/tmp/libris/epub-openviking.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${base}/#project/${state.project_id}`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > innerWidth + 2,
  );
  expect(overflow).toBe(false);
  await page.screenshot({ path: "/tmp/libris/epub-mobile.png" });
});

test("saved provider can be edited without submitting read-only metadata", async ({
  page,
}) => {
  await page.getByRole("link", { name: "Paramètres", exact: true }).click();
  await page.getByRole("button", { name: /Synthetic test only/ }).click();
  const saved = page.waitForResponse(
    (r) =>
      r.url().includes("/api/providers/") && r.request().method() === "PUT",
  );
  await page.getByRole("button", { name: "Enregistrer", exact: true }).click();
  expect((await saved).status()).toBe(200);
  await expect(
    page.getByText("Provider enregistré.", { exact: true }),
  ).toBeVisible();
});
