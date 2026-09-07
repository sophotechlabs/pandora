import { defineConfig } from '@playwright/test';
import type { ReporterDescription } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { BASE_URL } from './harness/paths';

const isCI = process.env.CI !== undefined;

interface SuiteGroup {
  id: string;
  runner: string;
  specs: string[];
}

interface Suite {
  groups: SuiteGroup[];
}

function browser(): 'chromium' | 'firefox' | 'webkit' {
  const value = process.env.PANDORA_E2E_BROWSER;
  if (value === undefined) {
    return 'chromium';
  }
  if (value === 'chromium') {
    return value;
  }
  if (value === 'firefox') {
    return value;
  }
  if (value === 'webkit') {
    return value;
  }
  throw new Error(`unknown E2E browser ${value}`);
}

function projects() {
  const browserName = browser();
  const groupID = process.env.PANDORA_E2E_GROUP;
  if (groupID === undefined) {
    return [{ name: browserName, testDir: './specs', use: { browserName } }];
  }
  if (groupID === '') {
    return [{ name: browserName, testDir: './specs', use: { browserName } }];
  }
  const suite = JSON.parse(readFileSync(resolve(import.meta.dirname, 'suite.json'), 'utf8')) as Suite;
  const group = suite.groups.find((candidate) => candidate.id === groupID);
  if (group === undefined) {
    throw new Error(`unknown E2E group ${groupID}`);
  }
  if (group.runner !== 'playwright') {
    throw new Error(`E2E group ${groupID} uses ${group.runner}`);
  }
  return [{ name: browserName, testDir: '.', testMatch: group.specs, use: { browserName } }];
}

function reporters(): ReporterDescription[] {
  if (!isCI) {
    return [['list'], ['html', { open: 'never' }]];
  }
  return [
    ['github'],
    ['html', { open: 'never' }],
    ['junit', { outputFile: 'test-results/junit.xml' }],
  ];
}

export default defineConfig({
  projects: projects(),
  globalSetup: './harness/globalSetup.ts',
  globalTeardown: './harness/globalTeardown.ts',
  workers: 1,
  fullyParallel: false,
  forbidOnly: isCI,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 20_000 },
  reporter: reporters(),
  use: {
    baseURL: BASE_URL,
    viewport: { width: 1600, height: 1000 },
    actionTimeout: 20_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
});
