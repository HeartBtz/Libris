import { test, expect } from "@playwright/test";
import { base, password, username } from "./integration-config";

test("multiple EPUB import, batch selection, canonical graph and validated relationship @integration", async ({
  page,
}) => {
  const errors: string[] = [];
  const ids: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("response", (response) => {
    if (
      response.url().endsWith("/api/projects") &&
      response.request().method() === "POST" &&
      response.status() === 201
    ) {
      void response.json().then((p) => ids.push(p.id));
    }
  });
  await page.goto(base);
  await page.getByLabel("Utilisateur", { exact: true }).fill(username);
  await page.getByLabel("Mot de passe", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Se connecter", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Bibliothèque", exact: true }),
  ).toBeVisible();
  await page
    .locator('input[type="file"][accept=".epub"]')
    .setInputFiles(["/tmp/libris/batch-a.epub", "/tmp/libris/batch-b.epub"]);
  try {
    await expect(
      page.getByText("2 livre(s) sélectionné(s)", { exact: true }),
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
      .getByRole("button", { name: "Personnages & liens", exact: true })
      .click();
    await expect(page.locator(".react-flow__node")).toHaveCount(3);
    await page.locator(`.react-flow__node[data-id="${rudy}"]`).click();
    await page
      .getByLabel("Fiche canonique de destination")
      .selectOption(rudeus);
    page.once("dialog", (dialog) => dialog.accept());
    await page
      .getByRole("button", { name: "Fusionner avec cette fiche" })
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
      .locator("xpath=ancestor::tr");
    await fixtureRow
      .getByRole("button", { name: "Archiver", exact: true })
      .click();
    await page.getByRole("button", { name: /Archives/ }).click();
    page.once("dialog", (dialog) => dialog.accept());
    const deletion = page.waitForResponse(
      (r) =>
        r.url().includes("/api/projects/") && r.request().method() === "DELETE",
    );
    await page
      .getByRole("button", { name: "Supprimer Batch fixture B", exact: true })
      .click();
    expect((await deletion).status()).toBe(200);
    await expect(
      page.getByRole("button", {
        name: "Supprimer Batch fixture B",
        exact: true,
      }),
    ).toHaveCount(0);
  } finally {
    for (const id of ids)
      expect([200, 404]).toContain(
        (await page.request.delete(`${base}/api/projects/${id}`)).status(),
      );
  }
});
