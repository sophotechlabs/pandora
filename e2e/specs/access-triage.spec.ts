import { makeOwnership, sendEvent, signIn } from '../harness/app';
import { BASE_URL } from '../harness/paths';
import { expect, test } from '../harness/test';

test('a viewer sees no mutation controls', async ({ page }) => {
  await sendEvent();
  await signIn(page, 'viewer');

  await page.goto(BASE_URL);

  await expect(page.getByRole('button', { name: 'Acknowledge' })).toHaveCount(0);
});

test('a member can resolve an issue', async ({ page }) => {
  await sendEvent();
  await signIn(page, 'member');
  await page.goto(BASE_URL);

  await page.locator('input[name=issue]').first().check();
  await page.getByRole('button', { name: 'Resolve' }).click();

  await expect(page.getByRole('link', { name: 'GatewayError: charge' })).toHaveCount(0);
});

test('a triage action reaches history', async ({ page }) => {
  await sendEvent();
  await signIn(page);
  await page.goto(BASE_URL);
  await page.locator('input[name=issue]').first().check();
  await page.getByRole('button', { name: 'Resolve' }).click();

  await page.goto(`${BASE_URL}/history/`);

  await expect(page.locator('code', { hasText: 'issue.triage' }).first()).toBeVisible();
});

test('ownership is visible and filters the stream', async ({ page }) => {
  makeOwnership();
  await sendEvent();
  await signIn(page);
  await page.goto(BASE_URL);

  await page.getByRole('link', { name: 'e2e-payments' }).click();

  await expect(page.getByRole('link', { name: 'GatewayError: charge' })).toBeVisible();
});
