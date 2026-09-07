import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { install, exportKubeconfig, refuseAnythingButKind } from './cluster';
import { startForward } from './endpoint';
import { BASE_URL, STATE_FILE, TMP_DIR } from './paths';

export default async function globalSetup(): Promise<void> {
  mkdirSync(TMP_DIR, { recursive: true });
  rmSync(STATE_FILE, { force: true });
  install();
  exportKubeconfig();
  refuseAnythingButKind();
  const pid = await startForward();
  writeFileSync(STATE_FILE, JSON.stringify({ pid, baseURL: BASE_URL }, null, 2));
}
