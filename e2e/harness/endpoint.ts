import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { background, alive, stop, waitFor } from './run';
import { BASE_URL, CONTEXT, DEPLOYMENT, KUBECONFIG, NAMESPACE, PORT, STATE_FILE } from './paths';

export async function startForward(): Promise<number> {
  let pid = 0;
  await waitFor('Pandora port-forward', 60, 1000, async () => {
    let needsForward = pid === 0;
    if (pid !== 0) {
      needsForward = !alive(pid);
    }
    if (needsForward) {
      if (pid !== 0) {
        stop(pid);
      }
      pid = background('kubectl', [
        '--kubeconfig',
        KUBECONFIG,
        '--context',
        CONTEXT,
        '--namespace',
        NAMESPACE,
        'port-forward',
        `service/${DEPLOYMENT}`,
        `${String(PORT)}:8000`,
      ]);
    }
    try {
      const response = await fetch(`${BASE_URL}/ready/`);
      return response.ok;
    } catch {
      return false;
    }
  });
  return pid;
}

export async function restartForward(): Promise<void> {
  if (existsSync(STATE_FILE)) {
    const previous = JSON.parse(readFileSync(STATE_FILE, 'utf8')) as { pid: number };
    stop(previous.pid);
  }
  const pid = await startForward();
  writeFileSync(STATE_FILE, JSON.stringify({ pid, baseURL: BASE_URL }, null, 2));
}
