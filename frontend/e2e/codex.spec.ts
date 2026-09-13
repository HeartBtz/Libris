import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';

const config = Object.fromEntries(readFileSync(new URL('../../.env', import.meta.url), 'utf8').split('\n')
  .filter(l => l && !l.startsWith('#')).map(l => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1)]));
const base = `http://${config.BIND_ADDRESS === '0.0.0.0' ? '127.0.0.1' : config.BIND_ADDRESS}:${config.PORT}`;

test('Codex connection types, model catalog and device-code interface', async ({page}) => {
  const errors: string[] = [];
  let providerId = '';
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(base);
  await page.getByLabel('Utilisateur', {exact: true}).fill(config.BOOTSTRAP_USERNAME);
  await page.getByLabel('Mot de passe', {exact: true}).fill(config.BOOTSTRAP_PASSWORD);
  await page.getByRole('button', {name: 'Se connecter', exact: true}).click();
  await page.getByRole('link', {name: 'Paramètres', exact: true}).click();
  await page.getByLabel('Connexion / protocole').selectOption('openai_responses');
  await expect(page.getByLabel('Base URL', {exact: true})).toHaveValue('https://api.openai.com/v1');
  await expect(page.getByLabel('Température', {exact: true})).toHaveCount(0);
  await page.getByLabel('Connexion / protocole').selectOption('codex_chatgpt');
  await expect(page.getByLabel('Clé API', {exact: true})).toHaveCount(0);
  await page.getByLabel('Nom', {exact: true}).fill('Codex UI test — no account');
  const created = page.waitForResponse(r => r.url().endsWith('/api/providers') && r.request().method() === 'POST');
  await page.getByRole('button', {name: 'Enregistrer', exact: true}).click();
  providerId = (await (await created).json()).id;
  try {
    await expect(page.getByText('Compte non connecté', {exact: true})).toBeVisible();
    await page.getByRole('button', {name: 'Vérifier / détecter les modèles Codex'}).click();
    await expect(page.getByLabel('Modèle', {exact: true})).not.toHaveValue('codex');
    await page.route(`**/api/providers/${providerId}/codex/login`, route => route.fulfill({
      json: {type: 'chatgptDeviceCode', verificationUrl: 'https://auth.openai.com/codex/device', userCode: 'TEST-CODE-ONLY'},
    }));
    await page.getByRole('button', {name: 'Se connecter avec ChatGPT'}).click();
    await expect(page.getByText('TEST-CODE-ONLY')).toBeVisible();
    await expect(page.getByRole('link', {name: /Ouvrir la connexion officielle/})).toHaveAttribute('href', 'https://auth.openai.com/codex/device');
    await page.screenshot({path: '/tmp/libris/epub-codex.png'});
    expect(errors).toEqual([]);
  } finally {
    if (providerId) await page.request.delete(`${base}/api/providers/${providerId}`);
  }
});
