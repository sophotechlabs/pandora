import { existsSync, readFileSync } from 'node:fs';
import { STATE_FILE } from './paths';
import { stop } from './run';

export default function globalTeardown(): void {
  if (!existsSync(STATE_FILE)) {
    return;
  }
  const state = JSON.parse(readFileSync(STATE_FILE, 'utf8')) as { pid: number };
  stop(state.pid);
}
