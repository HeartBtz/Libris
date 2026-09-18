import { test, expect } from "@playwright/test";
import { base, password, username } from "./integration-config";

test("Codex connection types, model catalog and device-code interface @integration", async ({
  page,
}) => {
  const errors: string[] = [];
  let providerId = "";
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(base);
  await page.getByLabel("Utilisateur", { exact: true }).fill(username);
  await page.getByLabel("Mot de passe", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Se connecter", exact: true }).click();
  await page.getByRole("link", { name: "Paramètres", exact: true }).click();
  await page
    .getByLabel("Connexion / protocole")
    .selectOption("openai_responses");
  await expect(page.getByLabel("Base URL", { exact: true })).toHaveValue(
    "https://api.openai.com/v1",
  );
  await expect(page.getByLabel("Température", { exact: true })).toHaveCount(0);
  await page.getByLabel("Connexion / protocole").selectOption("codex_chatgpt");
  await expect(page.getByLabel("Clé API", { exact: true })).toHaveCount(0);
  await page
    .getByLabel("Nom", { exact: true })
    .fill("Codex UI test — no account");
  const created = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/providers") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Enregistrer", exact: true }).click();
  providerId = (await (await created).json()).id;
  try {
    await expect(
      page.getByText("Compte non connecté", { exact: true }),
    ).toBeVisible();
    await page.route(`**/api/providers/${providerId}/codex/status`, (route) =>
      route.fulfill({ json: { connected: false } }),
    );
    await page.route(`**/api/providers/${providerId}/codex/models`, (route) =>
      route.fulfill({ json: { models: ["gpt-5-codex"] } }),
    );
    await page
      .getByRole("button", { name: "Vérifier / détecter les modèles Codex" })
      .click();
    await expect(page.getByLabel("Modèle", { exact: true })).toHaveValue(
      "gpt-5-codex",
    );
    await page.route(`**/api/providers/${providerId}/codex/login`, (route) =>
      route.fulfill({
        json: {
          type: "chatgptDeviceCode",
          verificationUrl: "https://auth.openai.com/codex/device",
          userCode: "TEST-CODE-ONLY",
        },
      }),
    );
    await page
      .getByRole("button", { name: "Se connecter avec ChatGPT" })
      .click();
    await expect(page.getByText("TEST-CODE-ONLY")).toBeVisible();
    await expect(
      page.getByRole("link", { name: /Ouvrir la connexion officielle/ }),
    ).toHaveAttribute("href", "https://auth.openai.com/codex/device");
    await page.screenshot({ path: "/tmp/libris/epub-codex.png" });
    expect(errors).toEqual([]);
  } finally {
    if (providerId)
      await page.request.delete(`${base}/api/providers/${providerId}`);
  }
});
