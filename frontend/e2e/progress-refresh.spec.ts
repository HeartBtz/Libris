import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';

const config = Object.fromEntries(readFileSync(new URL('../../.env', import.meta.url), 'utf8').split('\n')
  .filter(l => l && !l.startsWith('#')).map(l => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1)]));
const base = `http://${config.BIND_ADDRESS}:${config.PORT}`;

test('library has separate analysis/translation progress and refresh reloads metrics', async ({page}) => {
  await page.goto(base);
  await page.getByLabel('Utilisateur', {exact: true}).fill(config.BOOTSTRAP_USERNAME);
  await page.getByLabel('Mot de passe', {exact: true}).fill(config.BOOTSTRAP_PASSWORD);
  await page.getByRole('button', {name: 'Se connecter', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Bibliothèque', exact: true})).toBeVisible();
  const projects = await (await page.request.get(`${base}/api/projects`)).json();
  const project = projects.find((p: {title: string}) => p.title.endsWith('Vol. 1')) || projects[0];
  test.skip(!project, 'This read-only UI check needs an existing book.');
  const analysis = page.getByRole('progressbar', {name: `Analyse de ${project.title}`, exact: true});
  const translation = page.getByRole('progressbar', {name: `Traduction de ${project.title}`, exact: true});
  await expect(analysis).toBeVisible();
  await expect(translation).toBeVisible();
  expect(Number(await analysis.getAttribute('value'))).toBe(Math.floor((project.stats.analyzed_segments + project.stats.synthesized_chapters) / (project.stats.total + project.stats.chapters) * 100));
  expect(Number(await translation.getAttribute('value'))).toBe(Math.floor(project.stats.translated / project.stats.total * 100));
  await page.screenshot({path: '/tmp/opencode/epub-library-progress.png'});
  await page.goto(`${base}/#project/${project.id}`);
  await page.getByRole('button', {name: 'Observabilité', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Observabilité', exact: true})).toBeVisible();
  const metrics = page.waitForResponse(r => r.url().endsWith(`/api/projects/${project.id}/metrics`));
  await page.getByRole('button', {name: 'Actualiser les données du livre', exact: true}).click();
  expect((await metrics).status()).toBe(200);
});
