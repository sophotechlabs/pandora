import { spawn, spawnSync } from 'node:child_process';
import { openSync } from 'node:fs';

interface RunOptions {
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  input?: string;
}

export function run(command: string, args: string[], options: RunOptions = {}) {
  const done = spawnSync(command, args, {
    encoding: 'utf8',
    cwd: options.cwd,
    env: { ...process.env, ...options.env },
    input: options.input,
    maxBuffer: 64 * 1024 * 1024,
  });
  if (done.error) {
    throw done.error;
  }
  let code = 1;
  if (done.status !== null) {
    code = done.status;
  }
  let stdout = '';
  if (done.stdout !== null) {
    stdout = done.stdout;
  }
  let stderr = '';
  if (done.stderr !== null) {
    stderr = done.stderr;
  }
  return {
    code,
    stdout,
    stderr,
  };
}

export function mustRun(command: string, args: string[], options: RunOptions = {}): string {
  const result = run(command, args, options);
  if (result.code !== 0) {
    let output = result.stderr;
    if (output === '') {
      output = result.stdout;
    }
    throw new Error(
      `${command} ${args.join(' ')} exited ${String(result.code)}\n${output}`,
    );
  }
  return result.stdout;
}

export function background(command: string, args: string[]): number {
  const logfile = process.env.PANDORA_E2E_LOGFILE;
  let child;
  if (logfile === undefined) {
    child = spawn(command, args, {
      detached: true,
      stdio: 'ignore',
    });
  } else {
    const sink = openSync(logfile, 'a');
    child = spawn(command, args, {
      detached: true,
      stdio: ['ignore', sink, sink],
    });
  }
  child.unref();
  if (child.pid === undefined) {
    throw new Error(`${command} did not start`);
  }
  return child.pid;
}

export function stop(pid: number): void {
  try {
    process.kill(-pid, 'SIGTERM');
  } catch {
    return;
  }
}

export function alive(pid: number): boolean {
  try {
    process.kill(pid, 0);
  } catch {
    return false;
  }
  return true;
}

export async function waitFor(
  what: string,
  attempts: number,
  gap: number,
  check: () => boolean | Promise<boolean>,
): Promise<void> {
  for (let index = 0; index < attempts; index += 1) {
    if (await check()) {
      return;
    }
    await new Promise((wake) => setTimeout(wake, gap));
  }
  throw new Error(`timed out waiting for ${what}`);
}
