import { closeSync, mkdirSync, openSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';

const DEFAULT_LOCK_PATH = join(homedir(), '.quota-butler', 'quota-butler.lock');

export interface RunLock {
  path: string;
  release: () => void;
}

export function acquireRunLock(path = DEFAULT_LOCK_PATH): RunLock {
  mkdirSync(dirname(path), { recursive: true });
  let fd = tryCreate(path);
  if (fd == null) {
    const pid = readLockPid(path);
    if (pid && processIsAlive(pid)) {
      throw new Error(`quota-butler run 已在运行（PID ${pid}）。请先停止旧进程，或执行 quota-butler stop 后再启动。`);
    }
    try {
      unlinkSync(path);
    } catch {
      // 可能被另一个启动中的进程抢先清理，交给下一次 open 判断。
    }
    fd = tryCreate(path);
    if (fd == null) throw new Error('quota-butler run 正在启动中，请稍后重试。');
  }

  writeFileSync(fd, `${process.pid}\n${new Date().toISOString()}\n`, 'utf-8');
  let released = false;
  const release = () => {
    if (released) return;
    released = true;
    try {
      closeSync(fd);
    } catch {
      // 已关闭则忽略。
    }
    try {
      if (readLockPid(path) === process.pid) unlinkSync(path);
    } catch {
      // 退出清理失败不影响主流程。
    }
  };

  process.once('exit', release);
  for (const signal of ['SIGINT', 'SIGTERM'] as const) {
    process.once(signal, () => {
      release();
      process.exit(signal === 'SIGINT' ? 130 : 143);
    });
  }

  return { path, release };
}

function tryCreate(path: string): number | null {
  try {
    return openSync(path, 'wx');
  } catch {
    return null;
  }
}

function readLockPid(path: string): number | null {
  try {
    const first = readFileSync(path, 'utf-8').split(/\r?\n/)[0] ?? '';
    const pid = Number(first.trim());
    return Number.isInteger(pid) && pid > 0 ? pid : null;
  } catch {
    return null;
  }
}

function processIsAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}
