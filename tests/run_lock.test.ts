import { describe, it, expect } from 'vitest';
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { acquireRunLock } from '../src/run_lock.js';

function tmpLock(): string {
  const dir = join(tmpdir(), `qb-lock-${Date.now()}-${Math.random()}`);
  mkdirSync(dir, { recursive: true });
  return join(dir, 'quota-butler.lock');
}

describe('run lock', () => {
  it('rejects a second live process lock', () => {
    const path = tmpLock();
    const lock = acquireRunLock(path);
    try {
      expect(() => acquireRunLock(path)).toThrow(`PID ${process.pid}`);
    } finally {
      lock.release();
      rmSync(dirname(path), { recursive: true, force: true });
    }
  });

  it('replaces a stale lock file', () => {
    const path = tmpLock();
    writeFileSync(path, '99999999\n2026-06-27T00:00:00.000Z\n', 'utf-8');

    const lock = acquireRunLock(path);
    try {
      expect(readFileSync(path, 'utf-8')).toContain(String(process.pid));
    } finally {
      lock.release();
      expect(existsSync(path)).toBe(false);
      rmSync(dirname(path), { recursive: true, force: true });
    }
  });
});
