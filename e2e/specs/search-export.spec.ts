import { sendEvent, signIn } from '../harness/app';
import { BASE_URL } from '../harness/paths';
import { expect, test } from '../harness/test';

test('a saved view returns as a segment', async ({ page }) => {
  await sendEvent();
  await signIn(page);
  await page.goto(`${BASE_URL}/?q=is:unresolved`);

  await page.locator('input[name=name]').fill('Morning triage');
  await page.locator('form.save-view button[type=submit]').click();

  await expect(page.locator('.segment.view', { hasText: 'Morning triage' })).toBeVisible();
});

test('two issues can be merged and the alias remains visible', async ({ page }) => {
  await sendEvent();
  await sendEvent('d'.repeat(32), 'TimeoutError', 'src/payments/upstream.py', 7);
  await signIn(page);
  await page.goto(BASE_URL);

  await page.locator('input[name=issue]').nth(0).check();
  await page.locator('input[name=issue]').nth(1).check();
  await page.locator('button[value=merge]').click();

  await expect(page.locator('.message', { hasText: 'Merged' })).toBeVisible();
  await page.getByRole('link', { name: /GatewayError|TimeoutError/ }).first().click();
  await expect(page.getByText('Merged in')).toBeVisible();
});

test('markdown and CSV exports are served', async ({ page }) => {
  await sendEvent();
  await signIn(page);
  await page.goto(BASE_URL);
  const issueURL = await page.getByRole('link', { name: 'GatewayError: charge' }).getAttribute('href');
  if (issueURL === null) {
    throw new Error('issue link has no href');
  }

  const markdown = await page.request.get(`${BASE_URL}${issueURL}?format=md`);
  const csv = await page.request.get(`${BASE_URL}/?csv=1`);

  expect(await markdown.text()).toContain('# ');
  expect(await csv.text()).toContain('fingerprint_hash');
});

test('relevance sorting keeps an active issue visible', async ({ page }) => {
  await sendEvent();
  await signIn(page);

  await page.goto(`${BASE_URL}/?sort=relevance`);

  await expect(page.getByRole('link', { name: 'GatewayError: charge' })).toBeVisible();
});
