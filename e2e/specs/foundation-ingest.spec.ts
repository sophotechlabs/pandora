import { BASE_URL } from '../harness/paths';
import { downloadTextAttachment, sendAttachment, sendEvent, signIn } from '../harness/app';
import { expect, test } from '../harness/test';

test('an SDK envelope becomes an issue with its stack frame', async ({ page }) => {
  await sendEvent();
  await signIn(page);

  await page.goto(BASE_URL);
  await page.getByRole('link', { name: 'GatewayError: charge' }).click();

  await expect(page.getByText('charge.py').first()).toBeVisible();
});

test('an attachment reaches the occurrence page', async ({ page }) => {
  await sendAttachment();
  await signIn(page);

  await page.goto(BASE_URL);
  await page.getByRole('link', { name: 'AttachmentError: attachment' }).click();
  await page.getByRole('link', { name: 'Occurrences' }).click();

  await expect(page.getByRole('link', { name: 'debug.txt' })).toBeVisible();
  await expect(downloadTextAttachment(page, 'debug.txt')).resolves.toBe('diagnostic data');
});

test('a stranger is shown the login page', async ({ page }) => {
  await page.goto(BASE_URL);

  await expect(page.getByLabel('Username')).toBeVisible();
});

test('a wrong password stays on the login page', async ({ page }) => {
  await page.goto(`${BASE_URL}/login/`);
  await page.getByLabel('Username').fill('admin');
  await page.getByLabel('Password').fill('wrong');
  await page.getByRole('button', { name: 'Sign in' }).click();

  await expect(page.getByText('do not match an account')).toBeVisible();
});

test('signing out ends the session', async ({ page }) => {
  await signIn(page);

  await page.getByRole('button', { name: 'Sign out' }).click();
  await page.goto(BASE_URL);

  await expect(page.getByLabel('Username')).toBeVisible();
});
