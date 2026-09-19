import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import type { CleanupEntry } from "../src/features/OpenVikingCleanup";

// Settings › Memory · OpenViking: the opt-in cleanup, the orphan dry run and the log, against a
// stand-in of the API. Synthetic identifiers only.

const BASE = process.env.SHOWCASE_URL || "http://127.0.0.1:4173";
const ROOT = "viking://resources/demo-root";
const OWNER = "11111111-1111-4111-8111-111111111111";
const ORPHANS = [
  { uri: `${ROOT}/${OWNER}/standalone/22222222-2222-4222-8222-222222222222`, reason: "volume_deleted" },
  { uri: `${ROOT}/${OWNER}/series/33333333-3333-4333-8333-333333333333`, reason: "series_deleted" },
];

async function mock(page: Page) {
  const state = {
    view: { enabled: false, default: false, saved: false, configured: true },
    saved: [] as unknown[],
    scans: 0,
    cleaned: [] as string[],
    log: [
      {
        id: "c-1",
        kind: "volume",
        label: "Harbour Lights 2",
        status: "done",
        attempts: 2,
        next_attempt: 0,
        error: "",
        created_at: 1789254000,
        finished_at: 1789254100,
        uris: [`${ROOT}/${OWNER}/standalone/44444444-4444-4444-8444-444444444444`],
        results: [
          {
            uri: `${ROOT}/${OWNER}/standalone/44444444-4444-4444-8444-444444444444`,
            status: "removed",
            reason: "volume_deleted",
            document_count: 2,
            documents: [`${ROOT}/${OWNER}/standalone/44444444-4444-4444-8444-444444444444/book.md`],
          },
        ],
      },
      {
        id: "c-2",
        kind: "series",
        label: "Harbour Lights",
        status: "pending",
        attempts: 1,
        next_attempt: 4102444800,
        error: "OpenViking : ConnectError",
        created_at: 1789254200,
        finished_at: null,
        uris: [`${ROOT}/${OWNER}/series/55555555-5555-4555-8555-555555555555`],
        results: [],
      },
    ] as CleanupEntry[],
    retried: [] as string[],
  };
  await page.addInitScript(() => localStorage.setItem("locale", "en"));
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    if (path === "/api/auth/me") await route.fulfill({ json: { id: OWNER, username: "admin", admin: true } });
    else if (path === "/api/settings/memory")
      await route.fulfill({
        json: {
          base_url: "http://openviking:1933",
          root_uri: ROOT,
          enable_search: true,
          enable_deep_search: true,
          context_budget: 12000,
          retrieval_budget: 6000,
          min_score: 0.15,
          timeout: 20,
          auth_mode: "api_key",
          account: "",
          user: "",
          has_api_key: false,
        },
      });
    else if (path === "/api/settings/memory/cleanup") {
      if (method === "PUT") {
        const body = request.postDataJSON();
        state.saved.push(body);
        state.view = { ...state.view, enabled: body.enabled, saved: true };
      } else if (method === "DELETE") state.view = { ...state.view, enabled: state.view.default, saved: false };
      await route.fulfill({ json: state.view });
    } else if (path === "/api/settings/memory/cleanups") await route.fulfill({ json: state.log });
    else if (path.startsWith("/api/settings/memory/cleanups/") && path.endsWith("/retry")) {
      state.retried.push(path.split("/")[5]);
      await route.fulfill({ json: state.log[1] });
    } else if (path === "/api/settings/memory/orphans/scan") {
      state.scans += 1;
      await route.fulfill({ json: { root_uri: ROOT, orphans: ORPHANS, total: ORPHANS.length } });
    } else if (path === "/api/settings/memory/orphans/clean") {
      state.cleaned = request.postDataJSON().uris;
      await route.fulfill({ json: { cleanup: {}, refused: [], queued_at: 1789254300 } });
    } else await route.fulfill({ json: [] });
  });
  return state;
}

test("an administrator switches the cleanup on, dry-runs the orphans, then cleans them", async ({ page }) => {
  const state = await mock(page);
  await page.goto(`${BASE}/#settings`);
  await page.getByRole("tab", { name: "Memory · OpenViking" }).click();
  const card = page.locator(".settings-card", { has: page.getByRole("heading", { name: "OpenViking cleanup" }) });
  await expect(card.getByText("Environment setting")).toBeVisible();
  await expect(card.getByText("Environment value (OPENVIKING_CLEANUP_ON_DELETE): off")).toBeVisible();

  const toggle = card.getByRole("switch", { name: "Remove OpenViking documents on deletion" });
  await expect(toggle).not.toBeChecked();
  await toggle.check({ force: true });
  await card.getByRole("button", { name: "Save the cleanup" }).click();
  await expect(card.getByText("Cleanup saved. It applies to the next deletions.")).toBeVisible();
  expect(state.saved).toEqual([{ enabled: true }]);
  await expect(card.getByText("Setting saved here")).toBeVisible();

  // The log: what was removed, and a waiting cleanup that can be retried now.
  const log = card.getByRole("list", { name: "Cleanup log" });
  await expect(log.getByText("Deleted volume · Harbour Lights 2")).toBeVisible();
  await log.getByText("Directories in detail").click();
  await expect(log.getByText("removed (2 document(s))")).toBeVisible();
  await expect(log.getByText("OpenViking : ConnectError")).toBeVisible();
  await log.getByRole("button", { name: "Retry now" }).click();
  await expect.poll(() => state.retried).toEqual(["c-2"]);

  // The dry run lists and removes nothing; only the confirmation queues the removal.
  await card.getByRole("button", { name: "Look for orphans (dry run)" }).click();
  await expect(card.getByText(`2 orphan directory(ies) under ${ROOT}`)).toBeVisible();
  const orphans = card.getByRole("list", { name: "Orphan documents" });
  await expect(orphans.getByRole("listitem")).toHaveCount(2);
  await expect(orphans.getByText("series deleted")).toBeVisible();
  expect(state.cleaned).toEqual([]);
  await card.getByRole("button", { name: "Remove these 2 directory(ies)" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("checked again against the database");
  await dialog.getByRole("button", { name: "Remove", exact: true }).click();
  await expect(card.getByText("Cleanup scheduled")).toBeVisible();
  expect(state.cleaned).toEqual(ORPHANS.map((item) => item.uri));
  expect(state.scans).toBe(1);
});

test("the cleanup panel fits a phone screen", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await mock(page);
  await page.goto(`${BASE}/#settings`);
  await page.getByRole("tab", { name: "Memory · OpenViking" }).click();
  const card = page.locator(".settings-card", { has: page.getByRole("heading", { name: "OpenViking cleanup" }) });
  await card.getByRole("button", { name: "Look for orphans (dry run)" }).click();
  await expect(card.getByRole("list", { name: "Orphan documents" })).toBeVisible();
  await card.getByRole("list", { name: "Cleanup log" }).getByText("Directories in detail").click();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  for (const name of ["Save the cleanup", "Look for orphans (dry run)", "Retry now"]) {
    const box = await card.getByRole("button", { name }).boundingBox();
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(40);
  }
});
