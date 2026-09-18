import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { base, password, username } from "./integration-config";

test("batch pause, resume, cancel by operation and confirmed delete @integration", async ({
  page,
}) => {
  const ids: string[] = [];
  let providerId = "";
  await page.goto(base);
  await page.getByLabel("Utilisateur", { exact: true }).fill(username);
  await page.getByLabel("Mot de passe", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Se connecter", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Bibliothèque", exact: true }),
  ).toBeVisible();
  try {
    const provider = await page.request.post(`${base}/api/providers`, {
      data: {
        name: "Batch controls fixture",
        model: "fixture",
        base_url: "http://127.0.0.1:9/v1",
        timeout: 5,
      },
    });
    providerId = (await provider.json()).id;
    for (const letter of ["a", "b"]) {
      const response = await page.request.post(`${base}/api/projects`, {
        multipart: {
          file: {
            name: `batch-${letter}.epub`,
            mimeType: "application/epub+zip",
            buffer: readFileSync(`/tmp/libris/batch-${letter}.epub`),
          },
        },
      });
      const p = await response.json();
      ids.push(p.id);
      expect(
        (
          await page.request.put(`${base}/api/projects/${p.id}`, {
            data: {
              title: p.title,
              provider_id: providerId,
              context_backend: "internal",
              quality: "fast",
            },
          })
        ).status(),
      ).toBe(200);
      expect(
        (
          await page.request.post(`${base}/api/projects/${p.id}/jobs`, {
            data: { operation: "analyze" },
          })
        ).status(),
      ).toBe(202);
    }
    await page.reload();
    await page
      .getByRole("checkbox", {
        name: "Sélectionner Batch fixture A",
        exact: true,
      })
      .check();
    await page
      .getByRole("checkbox", {
        name: "Sélectionner Batch fixture B",
        exact: true,
      })
      .check();
    const statuses = async () =>
      Promise.all(
        ids.map(
          async (id) =>
            (
              await (
                await page.request.get(`${base}/api/projects/${id}`)
              ).json()
            ).status,
        ),
      );
    await page.getByRole("button", { name: "Plus d’actions" }).click();
    await page.getByRole("menuitem", { name: "Mettre la sélection en pause" }).click();
    await expect.poll(statuses).toEqual(["paused", "paused"]);
    await page.getByRole("button", { name: "Plus d’actions" }).click();
    await page.getByRole("menuitem", { name: "Reprendre la sélection" }).click();
    await expect
      .poll(async () =>
        (await statuses()).every((s) =>
          ["pending", "analyzing", "waiting"].includes(s),
        ),
      )
      .toBe(true);
    await page.getByRole("button", { name: "Plus d’actions" }).click();
    await page.getByRole("menuitem", { name: "Annuler les analyses" }).click();
    await expect.poll(statuses).toEqual(["cancelled", "cancelled"]);
    for (const id of ids) {
      await page.request.put(`${base}/api/projects/${id}/bible`, {
        data: { summary: "Synthetic fixture summary" },
      });
      expect(
        (
          await page.request.post(`${base}/api/projects/${id}/jobs`, {
            data: { operation: "translate" },
          })
        ).status(),
      ).toBe(202);
    }
    await page.getByRole("button", { name: "Plus d’actions" }).click();
    await page.getByRole("menuitem", { name: "Annuler les traductions" }).click();
    await expect.poll(statuses).toEqual(["cancelled", "cancelled"]);
    await page.getByRole("button", { name: "Plus d’actions" }).click();
    await page.getByRole("menuitem", { name: "Supprimer la sélection" }).click();
    await page
      .getByRole("dialog", { name: "Supprimer les livres sélectionnés ?" })
      .getByRole("button", { name: "Supprimer définitivement" })
      .click();
    await expect
      .poll(async () =>
        Promise.all(
          ids.map(async (id) =>
            (await page.request.get(`${base}/api/projects/${id}`)).status(),
          ),
        ),
      )
      .toEqual([404, 404]);
  } finally {
    for (const id of ids)
      await page.request.delete(`${base}/api/projects/${id}?stop_jobs=true`);
    if (providerId)
      await page.request.delete(`${base}/api/providers/${providerId}`);
  }
});
