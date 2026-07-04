import { describe, it, expect } from 'vitest';
import { existsSync, mkdtempSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { StateStore } from '../src/state.js';

function tmpPath(): string {
  return join(mkdtempSync(join(tmpdir(), 'qb-state-')), 'state.json');
}

describe('StateStore 持久化（P0-1 / P1-9）', () => {
  it('save 后文件可解析，且不留 .tmp 残迹', () => {
    const path = tmpPath();
    const store = new StateStore(path);
    store.get().lastAction = 'test';
    store.save();

    const parsed = JSON.parse(readFileSync(path, 'utf-8')) as { lastAction: string };
    expect(parsed.lastAction).toBe('test');
    expect(existsSync(`${path}.tmp`)).toBe(false);
  });

  it('save 出来的文件权限是 0600', () => {
    const path = tmpPath();
    const store = new StateStore(path);
    store.save();
    expect(statSync(path).mode & 0o777).toBe(0o600);
  });

  it('损坏的 state.json：备份为 .corrupt-*、重置为默认态、loadWarning 提示用户', () => {
    const path = tmpPath();
    writeFileSync(path, '{"activePlan": {"plan_id": "p1", ...TRUNCATED', 'utf-8'); // 模拟写一半断电

    const store = new StateStore(path);

    expect(store.get().activePlan).toBeNull(); // 重置为默认态
    expect(store.loadWarning).toMatch(/state\.json 损坏/);
    const backups = readdirSync(join(path, '..')).filter((f) => f.startsWith('state.json.corrupt-'));
    expect(backups).toHaveLength(1);
    expect(store.loadWarning).toContain(backups[0]!); // 警告里带备份路径
    // 损坏原文再保存：新文件正常、备份保留原坏内容
    store.save();
    expect(JSON.parse(readFileSync(path, 'utf-8'))).toBeTruthy();
    expect(readFileSync(join(path, '..', backups[0]!), 'utf-8')).toContain('TRUNCATED');
  });

  it('文件不存在（首次运行）：默认态且无警告', () => {
    const store = new StateStore(tmpPath());
    expect(store.loadWarning).toBeNull();
    expect(store.get().plansByDate).toEqual({});
  });

  it('正常文件：照常加载且无警告', () => {
    const path = tmpPath();
    const first = new StateStore(path);
    first.get().lastAction = 'kept';
    first.save();

    const second = new StateStore(path);
    expect(second.loadWarning).toBeNull();
    expect(second.get().lastAction).toBe('kept');
  });
});
