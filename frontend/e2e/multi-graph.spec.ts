import { test, expect } from "@playwright/test";
import { base, password, username } from "./integration-config";

test("multiple EPUB import, batch selection, canonical graph and validated relationship @integration", async ({
  page,
}) => {
  // The assistant adds its steps to the import: allow for them.
  test.setTimeout(120_000);
  const errors: string[] = [];
  const ids: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("response", (response) => {
    if (response.url().endsWith("/commit") && response.request().method() === "POST" && response.status() === 200)
      void response.json().then((result: { projects: { id: string }[] }) => ids.push(...result.projects.map((p) => p.id)));
  });
  await page.goto(base);
  await page.getByLabel("Utilisateur", { exact: true }).fill(username);
  await page.getByLabel("Mot de passe", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Se connecter", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Bibliothèque", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Ajouter du contenu", exact: true }).first().click();
  const assistant = page.getByRole("dialog", { name: "Ajouter du contenu" });
  await assistant.getByRole("radio", { name: /Livres EPUB/ }).check();
  await assistant.getByRole("button", { name: "Continuer" }).click();
  await assistant.getByRole("radio", { name: /Volume unique/ }).check();
  await assistant.getByRole("button", { name: "Continuer" }).click();
  await assistant
    .locator('input[type="file"]')
    .setInputFiles(["/tmp/libris/batch-a.epub", "/tmp/libris/batch-b.epub"]);
  await expect(assistant.getByText("2/2 fichiers analysés")).toBeVisible({ timeout: 30000 });
  await assistant.getByRole("button", { name: "Continuer" }).click();
  await assistant.getByRole("button", { name: "Continuer" }).click();
  await assistant.getByRole("button", { name: "Importer uniquement" }).click();
  // Up to three EPUBs are checked with EPUBCheck while the import is confirmed.
  await expect(assistant.getByText("Import terminé.")).toBeVisible({ timeout: 60_000 });
  await assistant.getByRole("button", { name: "Fermer" }).first().click();
  await page.getByRole("checkbox", { name: "Sélectionner Batch fixture A" }).check();
  await page.getByRole("checkbox", { name: "Sélectionner Batch fixture B" }).check();
  try {
    await expect(
      page.getByText("2 livres sélectionnés", { exact: true }),
    ).toBeVisible({ timeout: 30000 });
    await expect(
      page.getByRole("button", { name: "Analyser la sélection" }),
    ).toBeVisible();
    await expect.poll(() => ids.length).toBe(2);
    const pid = ids[0];
    const project = await (
      await page.request.get(`${base}/api/projects/${pid}`)
    ).json();
    await page.request.put(`${base}/api/projects/${pid}`, {
      data: {
        title: project.title,
        author: project.author,
        source_language: project.source_language,
        target_language: project.target_language,
        context_backend: "internal",
      },
    });
    const bible = await page.request.put(`${base}/api/projects/${pid}/bible`, {
      data: {
        summary: "Synthetic identity fixture",
        characters: [
          { canonical_name: "Rudy" },
          { canonical_name: "Rudeus Greyrat" },
          { canonical_name: "Paul" },
        ],
      },
    });
    expect(bible.status()).toBe(200);
    let graph = await (
      await page.request.get(`${base}/api/projects/${pid}/characters/graph`)
    ).json();
    const rudy = graph.nodes.find(
      (n: { name: string }) => n.name === "Rudy",
    ).id;
    const rudeus = graph.nodes.find(
      (n: { name: string }) => n.name === "Rudeus Greyrat",
    ).id;
    const paul = graph.nodes.find(
      (n: { name: string }) => n.name === "Paul",
    ).id;
    await page.goto(`${base}/#project/${pid}`);
    const draft = page
      .getByRole("textbox", { name: /Traduction passage/ })
      .first();
    await draft.fill("Brouillon non enregistré à conserver.");
    const statsRequest = page.waitForResponse(
      (r) =>
        r.url().endsWith(`/api/projects/${pid}`) &&
        r.request().method() === "GET",
    );
    await page
      .getByRole("button", {
        name: "Actualiser les données du livre",
        exact: true,
      })
      .click();
    expect((await statsRequest).status()).toBe(200);
    await expect(draft).toHaveValue("Brouillon non enregistré à conserver.");
    await page
      .getByRole("tab", { name: "Personnages", exact: true })
      .click();
    // The unsaved draft is protected: leaving the editor asks first.
    await page
      .getByRole("dialog", { name: "Modifications non enregistrées" })
      .getByRole("button", { name: "Quitter sans enregistrer" })
      .click();
    await expect(page.locator(".react-flow__node")).toHaveCount(3);
    await page.locator(`.react-flow__node[data-id="${rudy}"]`).click();
    await page
      .getByLabel("Fiche canonique de destination")
      .selectOption(rudeus);
    await page
      .getByRole("button", { name: "Fusionner avec cette fiche" })
      .click();
    await page
      .getByRole("dialog", { name: "Même personne ?" })
      .getByRole("button", { name: "Fusionner", exact: true })
      .click();
    await expect(page.locator(".react-flow__node")).toHaveCount(2);
    await page.getByLabel("Ajouter des alias").fill("Rudeus");
    await page.getByRole("button", { name: "Enregistrer les alias" }).click();
    await expect(
      page
        .locator(".character-detail")
        .getByText("Rudy · Rudeus", { exact: true }),
    ).toBeVisible();
    await test.step("Choisir les personnages du lien", async () => {
      await page
        .getByRole("combobox", { name: "Personnage source", exact: true })
        .selectOption({ label: "Rudeus Greyrat" });
      await page
        .getByRole("combobox", { name: "Personnage cible", exact: true })
        .selectOption({ label: "Paul" });
    });
    await page.getByLabel("Type de lien", { exact: true }).fill("enfant de");
    await page
      .getByRole("button", { name: "Ajouter et valider le lien" })
      .click();
    await expect(page.locator(".react-flow__edge")).toHaveCount(1);
    graph = await (
      await page.request.get(`${base}/api/projects/${pid}/characters/graph`)
    ).json();
    expect(
      graph.nodes.find((n: { id: string }) => n.id === rudeus).data.aliases,
    ).toEqual(["Rudy", "Rudeus"]);
    expect(graph.edges[0].validated).toBe(true);
    await page.screenshot({
      path: "/tmp/libris/epub-character-graph.png",
      fullPage: false,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth + 2,
      ),
    ).toBe(false);
    expect(errors).toEqual([]);
    await page.goto(`${base}/#library`);
    const fixtureRow = page
      .getByRole("link", { name: "Batch fixture B", exact: true })
      .locator("xpath=ancestor::*[self::tr or self::li][1]");
    await fixtureRow
      .getByRole("button", { name: "Actions pour Batch fixture B" })
      .click();
    await page.getByRole("menuitem", { name: "Archiver", exact: true }).click();
    await page.getByRole("radio", { name: /Archives/ }).click();
    await page
      .getByRole("button", { name: "Actions pour Batch fixture B" })
      .click();
    await page.getByRole("menuitem", { name: "Supprimer", exact: true }).click();
    const deletion = page.waitForResponse(
      (r) =>
        r.url().includes("/api/projects/") && r.request().method() === "DELETE",
    );
    await page
      .getByRole("dialog", { name: "Supprimer Batch fixture B ?" })
      .getByRole("button", { name: "Supprimer définitivement" })
      .click();
    expect((await deletion).status()).toBe(200);
    await expect(
      page.getByRole("link", { name: "Batch fixture B", exact: true }),
    ).toHaveCount(0);
  } finally {
    for (const id of ids)
      expect([200, 404]).toContain(
        (await page.request.delete(`${base}/api/projects/${id}`)).status(),
      );
  }
});
