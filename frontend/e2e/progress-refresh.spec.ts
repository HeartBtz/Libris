import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";

const config = Object.fromEntries(
  readFileSync(new URL("../../.env", import.meta.url), "utf8")
    .split("\n")
    .filter((l) => l && !l.startsWith("#"))
    .map((l) => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]),
);
const base = `http://${config.BIND_ADDRESS}:${config.PORT}`;

test("library has separate analysis/translation progress and refresh reloads metrics", async ({
  page,
}) => {
  const login = await page.request.post(`${base}/api/auth/login`, {
    data: {
      username: config.BOOTSTRAP_USERNAME,
      password: config.BOOTSTRAP_PASSWORD,
    },
    headers: { Origin: "https://libris.hbtz.fr" },
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
  const analysis = page.getByRole("progressbar", {
    name: `Analyse de ${project.title}`,
    exact: true,
  });
  const translation = page.getByRole("progressbar", {
    name: `Traduction de ${project.title}`,
    exact: true,
  });
  await expect(analysis).toBeVisible();
  await expect(translation).toBeVisible();
  expect(Number(await analysis.getAttribute("value"))).toBe(
    Math.floor(
      ((project.stats.analyzed_segments + project.stats.synthesized_chapters) /
        (project.stats.total + project.stats.chapters)) *
        100,
    ),
  );
  expect(Number(await translation.getAttribute("value"))).toBe(
    Math.floor((project.stats.translated / project.stats.total) * 100),
  );
  await page.screenshot({ path: "/tmp/opencode/epub-library-progress.png" });
  await page.goto(`${base}/#project/${project.id}`);
  await page.getByRole("button", { name: /Validations/ }).click();
  await expect(
    page.getByRole("heading", {
      name: "Validations de traduction",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByLabel(`${project.stats.flagged} passages à vérifier`, {
      exact: true,
    }),
  ).toBeVisible();
  if (project.stats.flagged) {
    await expect(page.locator(".validation-item").first()).toBeVisible();
    await expect(
      page
        .locator(".validation-item")
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
    const draft = page.locator(".validation-item textarea").first();
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
  await page.screenshot({ path: "/tmp/opencode/libris-validations.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    390,
  );
  await page
    .getByRole("button", { name: "Observabilité", exact: true })
    .click();
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
});
