import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import type { Page } from '@playwright/test';
import { zipSync, strToU8 } from 'fflate';
import { BASE_URL } from './paths';
import { shell } from './cluster';

const PUBLIC_KEY = 'e2epublickey000000000000000000ff';
const CI_TOKEN = 'e2e-ci-token';
const PASSWORD = 'e2e-password';
const DEBUG_ID = '3f2504e0-4f89-11d3-9a0c-0305e82c3301';

function projectID(): number {
  return Number(shell("from pandora.core.models import Project; print(Project.objects.get(slug='e2e').pk)"));
}

function envelope(parts: Array<string | Uint8Array>): Uint8Array {
  const buffers = parts.map((part) => {
    if (typeof part === 'string') {
      return Buffer.from(part);
    }
    return Buffer.from(part);
  });
  return Buffer.concat(buffers.flatMap((part, index) => {
    if (index === buffers.length - 1) {
      return [part];
    }
    return [part, Buffer.from('\n')];
  }));
}

async function postEnvelope(body: Uint8Array): Promise<Response> {
  return fetch(`${BASE_URL}/api/${String(projectID())}/envelope/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-sentry-envelope',
      'X-Sentry-Auth': `Sentry sentry_key=${PUBLIC_KEY}`,
    },
    body: Buffer.from(body),
  });
}

function eventPayload(eventID: string, type: string, filename: string, line: number) {
  const parts = filename.split('/');
  const leaf = parts[parts.length - 1];
  let functionName = leaf;
  if (leaf.includes('.')) {
    functionName = leaf.split('.')[0];
  }
  return {
    event_id: eventID,
    level: 'error',
    platform: 'python',
    environment: 'e2e',
    exception: {
      values: [
        {
          type,
          value: 'e2e failure',
          stacktrace: {
            frames: [{ filename, lineno: line, function: functionName }],
          },
        },
      ],
    },
  };
}

export async function sendEvent(
  eventID = 'e'.repeat(32),
  type = 'GatewayError',
  filename = 'src/payments/charge.py',
  line = 42,
): Promise<void> {
  const response = await postEnvelope(envelope([
    JSON.stringify({ event_id: eventID }),
    JSON.stringify({ type: 'event' }),
    JSON.stringify(eventPayload(eventID, type, filename, line)),
  ]));
  if (!response.ok) {
    throw new Error(`event ingest failed: ${await response.text()}`);
  }
}

export async function sendAttachment(): Promise<void> {
  const eventID = 'a'.repeat(32);
  const data = Buffer.from('diagnostic data');
  const response = await postEnvelope(envelope([
    JSON.stringify({ event_id: eventID }),
    JSON.stringify({ type: 'event' }),
    JSON.stringify(eventPayload(eventID, 'AttachmentError', 'src/payments/attachment.py', 9)),
    JSON.stringify({
      type: 'attachment',
      length: data.length,
      filename: 'debug.txt',
      content_type: 'text/plain',
    }),
    data,
  ]));
  if (!response.ok) {
    throw new Error(`attachment ingest failed: ${await response.text()}`);
  }
}

export async function downloadTextAttachment(page: Page, name: string): Promise<string> {
  const pendingDownload = page.waitForEvent('download');
  await page.getByRole('link', { name }).click();
  const download = await pendingDownload;
  const path = await download.path();
  if (path === null) {
    throw new Error(`attachment ${name} did not produce a local download`);
  }
  return readFileSync(path, 'utf8');
}

export async function signIn(page: Page, username = 'admin'): Promise<void> {
  await page.goto(`${BASE_URL}/login/`);
  await page.getByLabel('Username').fill(username);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.waitForURL(`${BASE_URL}/**`);
}

export function makeOwnership(): void {
  shell([
    'from pandora.people.models import OwnershipRule, Team',
    "team = Team.objects.create(name='e2e-payments')",
    "OwnershipRule.objects.create(name='e2e payments', pattern='src/payments/*', field='path', team=team)",
    "print('created')",
  ].join('\n'));
}

export function makeStalledDeploy(): void {
  shell([
    'from datetime import timedelta',
    'from django.utils import timezone',
    'from pandora.core.models import Project',
    'from pandora.releases.models import Deploy, Release',
    "project = Project.objects.get(slug='e2e')",
    "release = Release.objects.create(project=project, version='2.4.1')",
    "Deploy.objects.create(project=project, release=release, identifier='stalled', environment='production', started_at=timezone.now() - timedelta(hours=2))",
    "print('created')",
  ].join('\n'));
}

export async function sendClientReport(): Promise<void> {
  const response = await postEnvelope(envelope([
    JSON.stringify({}),
    JSON.stringify({ type: 'client_report' }),
    JSON.stringify({
      timestamp: new Date().toISOString(),
      discarded_events: [{ reason: 'queue_overflow', category: 'error', quantity: 13 }],
    }),
  ]));
  if (!response.ok) {
    throw new Error(`client report failed: ${await response.text()}`);
  }
}

export async function sendLog(): Promise<void> {
  const response = await fetch(`${BASE_URL}/api/${String(projectID())}/logs/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-ndjson',
      'X-Sentry-Auth': `Sentry sentry_key=${PUBLIC_KEY}`,
    },
    body: JSON.stringify({
      message: 'e2e shipper failure',
      level: 'error',
      service: 'vector',
      'error.kind': 'ShipperError',
    }),
  });
  if (!response.ok) {
    throw new Error(`log ingest failed: ${await response.text()}`);
  }
}

export async function checkIn(): Promise<void> {
  const response = await fetch(
    `${BASE_URL}/api/${String(projectID())}/cron/e2e-backup/${PUBLIC_KEY}/`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'ok' }),
    },
  );
  if (!response.ok) {
    throw new Error(`check-in failed: ${await response.text()}`);
  }
}

function sourceMapBundle(): Uint8Array {
  const sourceMap = JSON.stringify({
    version: 3,
    file: 'app.js',
    sources: ['src/payments.js'],
    names: ['charge'],
    mappings: 'AAAAA,SAAS',
    sourcesContent: ["export function charge(order) {\n  throw new Error('x')\n}\n"],
    debug_id: DEBUG_ID,
  });
  return zipSync({ 'app.js.map': strToU8(sourceMap) });
}

export async function uploadSourceMap(): Promise<void> {
  const bundle = sourceMapBundle();
  const checksum = createHash('sha1').update(bundle).digest('hex');
  const form = new FormData();
  form.append(checksum, new Blob([Uint8Array.from(bundle)]), checksum);
  let response = await fetch(`${BASE_URL}/api/0/organizations/pandora/chunk-upload/`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${CI_TOKEN}` },
    body: form,
  });
  if (!response.ok) {
    throw new Error(`source map chunk failed: ${await response.text()}`);
  }
  response = await fetch(
    `${BASE_URL}/api/0/organizations/pandora/artifactbundle/assemble/`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${CI_TOKEN}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ checksum, chunks: [checksum], projects: ['e2e'] }),
    },
  );
  const result = await response.json() as { state?: string };
  if (!response.ok) {
    throw new Error(`source map assembly failed: ${JSON.stringify(result)}`);
  }
  if (result.state !== 'ok') {
    throw new Error(`source map assembly failed: ${JSON.stringify(result)}`);
  }
}

export async function sendMinifiedEvent(): Promise<void> {
  const eventID = 'c'.repeat(32);
  const payload = {
    event_id: eventID,
    level: 'error',
    platform: 'javascript',
    environment: 'e2e',
    exception: {
      values: [{
        type: 'TypeError',
        value: 'undefined is not a function',
        stacktrace: {
          frames: [{
            abs_path: 'app://basket.4c9e10.js',
            filename: 'basket.4c9e10.js',
            function: 'n',
            lineno: 1,
            colno: 0,
            in_app: true,
          }],
        },
      }],
    },
    debug_meta: {
      images: [{ type: 'sourcemap', code_file: 'app://basket.4c9e10.js', debug_id: DEBUG_ID }],
    },
  };
  const response = await postEnvelope(envelope([
    JSON.stringify({ event_id: eventID }),
    JSON.stringify({ type: 'event' }),
    JSON.stringify(payload),
  ]));
  if (!response.ok) {
    throw new Error(`minified event failed: ${await response.text()}`);
  }
}

export async function createDeploy(): Promise<Response> {
  return fetch(`${BASE_URL}/api/0/organizations/pandora/releases/2.4.2/deploys/`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${CI_TOKEN}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ environment: 'production', projects: ['e2e'] }),
  });
}

export function seedMaintenanceState(): void {
  shell([
    'from datetime import timedelta',
    'from django.utils import timezone',
    'from pandora.artifacts.service import store_chunk',
    'from pandora.core.models import Project',
    'from pandora.ingest.models import EnvelopeState, RawEnvelope',
    "project = Project.objects.get(slug='e2e')",
    "store_chunk(project, b'stale-e2e-chunk', timezone.now() - timedelta(hours=7))",
    "RawEnvelope.objects.create(project=project, source='sdk', payload={'event_id': 'f' * 32, 'message': 'replay'}, state=EnvelopeState.FAILED, error='e2e replay')",
    "print('created')",
  ].join('\n'));
}

export function maintenanceState(): string {
  return shell([
    'from pandora.artifacts.models import UploadChunk',
    'from pandora.ingest.models import EnvelopeState, RawEnvelope',
    "print(f'{UploadChunk.objects.count()}:{RawEnvelope.objects.filter(state=EnvelopeState.FAILED).count()}')",
  ].join('\n'));
}
