import { checkIn, sendLog, sendMinifiedEvent, signIn, uploadSourceMap } from '../harness/app';
import { shell } from '../harness/cluster';
import { BASE_URL } from '../harness/paths';
import { expect, test } from '../harness/test';

test('a log line becomes an issue', async ({ page }) => {
  await sendLog();
  await signIn(page);

  await page.goto(BASE_URL);

  await expect(page.getByRole('link', { name: 'ShipperError' })).toBeVisible();
});

test('a cron check-in opens a monitor', async () => {
  await checkIn();

  const count = shell("from pandora.ingest.models import Monitor; print(Monitor.objects.filter(slug='e2e-backup').count())");

  expect(count).toBe('1');
});

test('a source map resolves a minified frame', async ({ page }) => {
  await uploadSourceMap();
  await sendMinifiedEvent();
  await signIn(page);

  await page.goto(BASE_URL);
  await page.getByRole('link', { name: 'TypeError: n' }).click();

  await expect(page.getByText('src/payments.js').first()).toBeVisible();
});
