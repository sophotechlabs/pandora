import {
  downloadTextAttachment,
  maintenanceState,
  seedMaintenanceState,
  sendAttachment,
  signIn,
} from '../harness/app';
import { recreatePod, runMaintenanceJobs, upgrade } from '../harness/cluster';
import { restartForward } from '../harness/endpoint';
import { BASE_URL } from '../harness/paths';
import { expect, test } from '../harness/test';

test('data and maintenance survive restart and upgrade', async ({ page }) => {
  await sendAttachment();
  await signIn(page);
  await page.goto(BASE_URL);
  await expect(page.getByRole('link', { name: 'AttachmentError: attachment' })).toBeVisible();

  recreatePod();
  await restartForward();
  await page.goto(BASE_URL);
  await expect(page.getByRole('link', { name: 'AttachmentError: attachment' })).toBeVisible();

  upgrade();
  await restartForward();
  await page.goto(BASE_URL);
  await expect(page.getByRole('link', { name: 'AttachmentError: attachment' })).toBeVisible();
  await page.getByRole('link', { name: 'AttachmentError: attachment' }).click();
  await page.getByRole('link', { name: 'Occurrences' }).click();
  await expect(downloadTextAttachment(page, 'debug.txt')).resolves.toBe('diagnostic data');

  seedMaintenanceState();
  runMaintenanceJobs();
  expect(maintenanceState()).toBe('0:0');
});
