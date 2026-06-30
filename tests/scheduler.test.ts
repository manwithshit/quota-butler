import { describe, it, expect, vi, afterEach } from 'vitest';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { WarmupScheduler } from '../src/scheduler.js';
import { StateStore } from '../src/state.js';
import type { LarkChannel } from '@larksuite/channel';

function tmpState(): StateStore {
  return new StateStore(join(tmpdir(), `qb-test-${Date.now()}-${Math.random()}.json`));
}

const fakeChannel = {} as unknown as LarkChannel;

function fakeChannelWithSends(): { channel: LarkChannel; sends: unknown[] } {
  const sends: unknown[] = [];
  const channel = { send: vi.fn(async (_id: string, msg: unknown) => void sends.push(msg)) } as unknown as LarkChannel;
  return { channel, sends };
}

describe('WarmupScheduler.arm', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('skips past and already-executed nodes, arms only future ones', () => {
    const state = tmpState();
    const sch = new WarmupScheduler(fakeChannel, 'ou_x', state);
    const past = new Date(Date.now() - 3600000).toISOString();
    const future = new Date(Date.now() + 3600000).toISOString();
    const future2 = new Date(Date.now() + 7200000).toISOString();
    state.get().executedWarmups = [`p1:cc:${future}`]; // 这个未来节点已执行过

    const result = sch.arm({
      plan_id: 'p1',
      status: 'active',
      events: [
        { agent: 'cc', kind: 'warmup', at: past, purpose: '' }, // 过期 → 跳过
        { agent: 'cc', kind: 'warmup', at: future, purpose: '' }, // 已执行 → 跳过
        { agent: 'codex', kind: 'warmup', at: future2, purpose: '' }, // 未来 → 布上
      ],
    });

    expect(result).toEqual({ armed: 1, skipped: 2 });
    expect(sch.pending).toBe(1);
    sch.cancelAll();
    expect(sch.pending).toBe(0);
  });

  it('arms legacy warmup events that do not have a kind field', () => {
    const state = tmpState();
    const sch = new WarmupScheduler(fakeChannel, 'ou_x', state);
    const future = new Date(Date.now() + 3600000).toISOString();

    const result = sch.arm({
      plan_id: 'legacy',
      status: 'active',
      events: [{ agent: 'cc', at: future, purpose: '' }],
    });

    expect(result).toEqual({ armed: 1, skipped: 0 });
    expect(sch.pending).toBe(1);
    sch.cancelAll();
  });

  it('queues scheduled warmup receipts during quiet hours and flushes them after 8am', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 5, 24, 6, 30));
    const state = tmpState();
    const { channel, sends } = fakeChannelWithSends();
    const sch = new WarmupScheduler(channel, 'ou_x', state);

    await (sch as unknown as { notify: (text: string, opts?: { respectQuiet?: boolean }) => Promise<void> })
      .notify('✅ Claude Code 已按计划预热（06:30）。', { respectQuiet: true });

    expect(sends).toHaveLength(0);
    expect(state.get().pendingQuietMessages).toMatchObject([
      { text: '✅ Claude Code 已按计划预热（06:30）。', kind: 'warmup' },
    ]);
    expect(state.get().pendingQuietMessages[0]?.dueAt).toBe(new Date(2026, 5, 24, 8, 0).toISOString());

    await vi.advanceTimersByTimeAsync(90 * 60_000);

    expect(sends).toEqual([{ text: '✅ Claude Code 已按计划预热（06:30）。' }]);
    expect(state.get().pendingQuietMessages).toHaveLength(0);
    sch.cancelAll();
  });

  it('keeps explicit test/debug notifications immediate even during quiet hours', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 5, 24, 6, 30));
    const state = tmpState();
    const { channel, sends } = fakeChannelWithSends();
    const sch = new WarmupScheduler(channel, 'ou_x', state);

    await (sch as unknown as { notify: (text: string, opts?: { respectQuiet?: boolean }) => Promise<void> })
      .notify('测试预热完成');

    expect(sends).toEqual([{ text: '测试预热完成' }]);
    expect(state.get().pendingQuietMessages).toHaveLength(0);
    sch.cancelAll();
  });
});
