import { expect, test as base } from '@playwright/test';
import { resetApplication } from './cluster';

export const test = base.extend<{ applicationReset: void }>({
  applicationReset: [async ({}, use) => {
    resetApplication();
    await use();
  }, { auto: true }],
});

export { expect };
