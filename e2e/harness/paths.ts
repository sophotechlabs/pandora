import { createHash } from 'node:crypto';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));

export const E2E_DIR = resolve(here, '..');
export const REPO_DIR = resolve(E2E_DIR, '..');
export const TMP_DIR = join(E2E_DIR, '.tmp');
export const KUBECONFIG = join(TMP_DIR, 'kubeconfig');
export const STATE_FILE = join(TMP_DIR, 'state.json');

const DEFAULT_CLUSTER = 'pandora-ci';
const DEFAULT_PORT = 34216;
const SESSION_PORT_BASE = 25000;
const SESSION_PORT_SPAN = 1000;

let cluster = process.env.PANDORA_KIND_CLUSTER;
if (cluster === undefined) {
  cluster = process.env.SPINOZA_KIND_CLUSTER;
}
if (cluster === '') {
  cluster = process.env.SPINOZA_KIND_CLUSTER;
}
if (cluster === undefined) {
  cluster = DEFAULT_CLUSTER;
}
if (cluster === '') {
  cluster = DEFAULT_CLUSTER;
}

function setting(name: string, fallback: string): string {
  const value = process.env[name];
  if (value === undefined) {
    return fallback;
  }
  if (value === '') {
    return fallback;
  }
  return value;
}

function portFor(session: string): number {
  if (session === DEFAULT_CLUSTER) {
    return DEFAULT_PORT;
  }
  const digest = createHash('sha256').update(session).digest();
  return SESSION_PORT_BASE + (digest.readUInt16BE(0) % SESSION_PORT_SPAN);
}

export const CLUSTER = cluster;
export const CONTEXT = `kind-${CLUSTER}`;
export const NAMESPACE = setting('PANDORA_KIND_NAMESPACE', 'pandora-kind');
export const RELEASE = setting('PANDORA_KIND_RELEASE', 'pandora');
export const DEPLOYMENT = `${RELEASE}-pandora`;
export const PORT = portFor(CLUSTER);
export const BASE_URL = `http://localhost:${String(PORT)}`;
