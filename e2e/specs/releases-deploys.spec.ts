import { createDeploy, makeStalledDeploy, sendClientReport, sendEvent, signIn } from '../harness/app';
import { BASE_URL } from '../harness/paths';
import { expect, test } from '../harness/test';

test('overview and ingest pages render live data', async ({ page }) => {
  await sendEvent();
  await signIn(page);

  await page.goto(`${BASE_URL}/overview/`);
  await expect(page.locator('.kpi-label', { hasText: 'Firing now' })).toBeVisible();
  await page.goto(`${BASE_URL}/ingest/`);
  await expect(page.locator('.kpi-label', { hasText: 'Backlog' }).first()).toBeVisible();
});

test('a stalled rollout reaches overview', async ({ page }) => {
  makeStalledDeploy();
  await signIn(page);

  await page.goto(`${BASE_URL}/overview/`);

  const row = page.locator('tr', { hasText: '2.4.1' });
  await expect(row.getByText('production')).toBeVisible();
});

test('a client report reaches ingest accounting', async ({ page }) => {
  await sendClientReport();
  await signIn(page);

  await page.goto(`${BASE_URL}/ingest/`);

  const row = page.locator('tr', { hasText: 'queue_overflow' });
  await expect(row.getByText('13', { exact: true })).toBeVisible();
});

test('the CI token creates an idempotent Sentry deploy', async () => {
  const first = await createDeploy();
  const second = await createDeploy();

  expect(first.status).toBe(201);
  expect(await second.json()).toEqual(await first.json());
});
