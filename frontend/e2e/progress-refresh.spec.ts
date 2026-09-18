import { test, expect } from "@playwright/test";
import { base, password, username } from "./integration-config";

test("library shows active-stage progress and refresh reloads metrics @integration", async ({
  page,
}) => {
  const login = await page.request.post(`${base}/api/auth/login`, {
    data: {
      username,
      password,
    },
    headers: { Origin: new URL(base).origin },
  });
  expect(login.ok()).toBeTruthy();
  await page.goto(base);
  await expect(
    page.getByRole("heading", { name: "Bibliothèque", exact: true }),
  ).toBeVisible();
  const projects = await (
    await page.request.get(`${base}/api/projects`)
  ).json();
  const project =
    projects.find((p: { title: string }) => p.title.endsWith("Vol. 1")) ||
    projects[0];
  test.skip(!project, "This read-only UI check needs an existing book.");
  const analysis = Math.floor(
    ((project.stats.analyzed_segments + project.stats.synthesized_chapters) /
      (project.stats.total + project.stats.chapters)) *
      100,
  );
  const translation = Math.floor(
    (project.stats.translated / project.stats.total) * 100,
  );
  const review = Math.floor(
    ((project.stats.reviewed_segments || 0) /
      (project.stats.review_total || project.stats.total)) *
      100,
  );
  const expected = project.progress?.current
    ? {
        label: project.progress.current.label,
        value: project.progress.current.percent,
      }
    : project.status === "completed" && !project.stats.flagged
      ? { label: "Export", value: 100 }
      : project.status === "analyzing"
        ? { label: "Analyse & mémoire", value: analysis }
        : project.status === "reviewing" || translation === 100
          ? { label: "Relecture", value: review }
          : project.status === "translating" || analysis === 100
            ? { label: "Traduction", value: translation }
            : { label: "Analyse & mémoire", value: analysis };
  const row = page
    .getByRole("link", { name: project.title, exact: true })
    .locator("xpath=ancestor::tr");
  await expect(row.getByRole("progressbar")).toHaveCount(1);
  const progress = row.getByRole("progressbar", {
    name: `${expected.label} de ${project.title}`,
    exact: true,
  });
  await expect(progress).toBeVisible();
  expect(Number(await progress.getAttribute("value"))).toBe(expected.value);
  await page.screenshot({ path: "/tmp/libris/epub-library-progress.png" });
  await page.goto(`${base}/#project/${project.id}`);
  await page.getByRole("tab", { name: /Validations/ }).click();
  await expect(
    page.getByRole("button", { name: "Lancer la revue IA", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", {
      name: "Validations de traduction",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText(
      `${project.stats.flagged} ${project.stats.flagged > 1 ? "passages" : "passage"} à vérifier`,
      { exact: true },
    ),
  ).toBeVisible();
  if (project.stats.flagged) {
    await expect(page.locator(".review-item").first()).toBeVisible();
    await expect(
      page
        .locator(".review-item")
        .first()
        .getByRole("button", { name: "Valider", exact: true }),
    ).toBeVisible();
    const firstAdvice = page.locator(".ai-guidance").first();
    if (await firstAdvice.count()) {
      await expect(
        firstAdvice.getByText("Avis de l’IA", { exact: true }),
      ).toBeVisible();
      await expect(
        firstAdvice.getByText("Proposition", { exact: true }).first(),
      ).toBeVisible();
      await expect(
        firstAdvice
          .getByRole("button", {
            name: "Accepter cette proposition",
            exact: true,
          })
          .first(),
      ).toBeVisible();
      await expect(
        firstAdvice
          .getByRole("button", {
            name: "Refuser cette proposition",
            exact: true,
          })
          .first(),
      ).toBeVisible();
    }
    const draft = page.locator(".review-item textarea").first();
    const original = await draft.inputValue();
    await draft.fill(`${original} [brouillon de test]`);
    await page
      .getByRole("button", {
        name: "Actualiser les données du livre",
        exact: true,
      })
      .click();
    await page.waitForTimeout(750);
    await expect(draft).toHaveValue(`${original} [brouillon de test]`);
  }
  await page.screenshot({ path: "/tmp/libris/libris-validations.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    390,
  );
  await page.getByRole("tab", { name: "Bilan & récupération" }).click();
  // The draft typed above is protected: leaving the review queue asks first.
  if (project.stats.flagged)
    await page
      .getByRole("dialog", { name: "Modifications non enregistrées" })
      .getByRole("button", { name: "Quitter sans enregistrer" })
      .click();
  await expect(
    page.getByRole("heading", { name: "Bilan & récupération", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Provider de récupération")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    390,
  );
  await page.setViewportSize({ width: 1440, height: 1040 });
  await page.screenshot({ path: "/tmp/libris/completion.png" });
  await page.getByRole("tab", { name: "Observabilité", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Observabilité", exact: true }),
  ).toBeVisible();
  const metrics = page.waitForResponse((r) =>
    r.url().endsWith(`/api/projects/${project.id}/metrics`),
  );
  await page
    .getByRole("button", {
      name: "Actualiser les données du livre",
      exact: true,
    })
    .click();
  expect((await metrics).status()).toBe(200);
  await page.goto(`${base}/#settings`);
  await page.getByRole("tab", { name: "SearXNG", exact: true }).click();
  await expect(page.getByLabel("URL de l’instance SearXNG")).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Tester la connexion", exact: true }),
  ).toBeVisible();
});
