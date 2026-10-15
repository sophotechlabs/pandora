import {
  inboundRefusals,
  makeCodeMapping,
  makeLocalhostFilter,
  makeMetricMonitor,
  runCommand,
  securityURL,
  sendEvent,
  sendLocalEvent,
  sendReleasedEvent,
  sendTransaction,
  setCommits,
  signIn,
} from '../harness/app';
import { BASE_URL } from '../harness/paths';
import { expect, test } from '../harness/test';

test('a transaction reaches the performance page and its breakdown', async ({ page }) => {
  await sendTransaction('GET /e2e/cart', 240, 'db.query', 180);
  await signIn(page);

  await page.goto(`${BASE_URL}/performance/`);
  await page.getByRole('link', { name: 'GET /e2e/cart' }).click();

  const row = page.locator('tr', { hasText: 'db.query' });
  await expect(row).toBeVisible();
});

test('a metric monitor breach becomes an alert issue', async ({ page }) => {
  makeMetricMonitor('e2e error volume');
  await sendEvent();
  runCommand('alerts');
  await signIn(page);

  await page.goto(BASE_URL);

  await expect(page.getByRole('link', { name: /e2e error volume/ })).toBeVisible();
});

test('a new issue waits for review until it is marked reviewed', async ({ page }) => {
  await sendEvent();
  await signIn(page);
  await page.goto(`${BASE_URL}/?q=${encodeURIComponent('is:for_review')}`);
  await expect(page.getByRole('link', { name: 'GatewayError: charge' })).toBeVisible();

  await page.locator('input[name=issue]').first().check();
  await page.getByRole('button', { name: 'Mark reviewed' }).click();
  await page.goto(`${BASE_URL}/?q=${encodeURIComponent('is:for_review')}`);

  await expect(page.getByRole('link', { name: 'GatewayError: charge' })).toHaveCount(0);
});

test('a negated search hides what it names', async ({ page }) => {
  await sendEvent();
  await sendEvent('d'.repeat(32), 'LedgerError', 'src/ledger/post.py', 7);
  await signIn(page);

  await page.goto(`${BASE_URL}/?q=${encodeURIComponent('!GatewayError')}`);

  await expect(page.getByRole('link', { name: 'LedgerError: post' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'GatewayError: charge' })).toHaveCount(0);
});

test('a comment lands in the activity feed and subscribes its author', async ({ page }) => {
  await sendEvent();
  await signIn(page);
  await page.goto(BASE_URL);
  await page.getByRole('link', { name: 'GatewayError: charge' }).click();
  await page.getByRole('link', { name: 'Activity' }).click();

  await page.getByPlaceholder('Leave a note for whoever picks this up next').fill('checked the gateway logs');
  await page.getByRole('button', { name: 'Comment' }).click();

  await expect(page.locator('.feed-item', { hasText: 'checked the gateway logs' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Stop watching' })).toBeVisible();
});

test('watching can be turned on and off', async ({ page }) => {
  await sendEvent();
  await signIn(page);
  await page.goto(BASE_URL);
  await page.getByRole('link', { name: 'GatewayError: charge' }).click();

  await page.getByRole('button', { name: 'Watch', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Stop watching' })).toBeVisible();
  await page.getByRole('button', { name: 'Stop watching' }).click();

  await expect(page.getByRole('button', { name: 'Watch', exact: true })).toBeVisible();
});

test('a similar issue is listed on the similar tab', async ({ page }) => {
  await sendEvent();
  await sendEvent('f'.repeat(32), 'GatewayError', 'src/payments/refund.py', 11);
  await signIn(page);
  await page.goto(BASE_URL);
  await page.getByRole('link', { name: 'GatewayError: charge' }).click();

  await page.getByRole('link', { name: 'Similar' }).click();

  await expect(page.getByRole('link', { name: 'GatewayError: refund' })).toBeVisible();
});

test('a CSP violation the browser reports becomes an issue', async ({ page }) => {
  const policy = `img-src 'self'; report-uri ${securityURL()}`;
  await page.route(`${BASE_URL}/e2e-shop/**`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/html',
      headers: { 'Content-Security-Policy': policy },
      body: '<!doctype html><title>shop</title><img src="http://cdn.blocked.test/pixel.png">',
    });
  });

  await page.goto(`${BASE_URL}/e2e-shop/checkout`);
  await signIn(page);

  await expect(async () => {
    await page.goto(BASE_URL);
    await expect(page.getByRole('link', { name: /Blocked 'img-src' from/ })).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 30_000 });
});

test('the localhost filter refuses a local event', async ({ page }) => {
  makeLocalhostFilter();
  const refused = await sendLocalEvent();
  await sendEvent();
  await signIn(page);

  await page.goto(BASE_URL);

  expect(refused.ok).toBe(true);
  await expect(page.getByRole('link', { name: 'GatewayError: charge' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'LocalOnlyError: server' })).toHaveCount(0);
  expect(inboundRefusals()).toBe('1');
});

test('CI commits name a suspect commit and link the frame to its source', async ({ page }) => {
  makeCodeMapping();
  const response = await setCommits('9.9.9');
  expect(response.ok).toBe(true);
  await sendReleasedEvent('9.9.9');
  await signIn(page);
  await page.goto(BASE_URL);

  await page.getByRole('link', { name: 'LedgerError: post' }).click();

  const suspects = page.locator('section', { hasText: 'Suspect commits' });
  await expect(suspects.getByText('E2E Committer')).toBeVisible();
  const source = page.getByRole('link', { name: 'Open source' }).first();
  await expect(source).toHaveAttribute('href', /^https:\/\/github\.com\/sophotechlabs\/e2e-ledger\/blob\/.+\/src\/ledger\/post\.py#L17$/);
});
