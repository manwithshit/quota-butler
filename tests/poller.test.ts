import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { LarkChannel } from '@larksuite/channel';

// 只替换 detectAgents，保留 isSchedulable 等真实实现。
vi.mock('../src/agent_status.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/agent_status.js')>();
  return { ...actual, detectAgents: vi.fn() };
});

import { Poller } from '../src/poller.js';
import { StateStore } from '../src/state.js';
import { detectAgents, AgentState, type AgentStatus } from '../src/agent_status.js';
import type { Usage } from '../src/providers/index.js';

const mockDetect = detectAgents as unknown as ReturnType<typeof vi.fn>;

function tmpState(): StateStore {
  return new StateStore(join(tmpdir(), `qb-poller-${Date.now()}-${Math.random()}.json`));
}

function ccStatus(util5: number, resetsAt: Date): Record<string, AgentStatus> {
  const usage: Usage = {
    provider: 'cc',
    fiveHour: { utilization: util5, resetsAt, windowSeconds: 18000 },
    sevenDay: { utilization: 10, resetsAt: null, windowSeconds: 604800 },
  };
  return { cc: { provider: 'cc', state: AgentState.CONNECTED, usage } };
}

function fakeChannel(): { channel: LarkChannel; sends: unknown[] } {
  const sends: unknown[] = [];
  const channel = { send: vi.fn(async (_id: string, msg: unknown) => void sends.push(msg)) } as unknown as LarkChannel;
  return { channel, sends };
}

describe('Poller deferred notifications (P0)', () => {
  beforeEach(() => {
    mockDetect.mockReset();
    vi.useFakeTimers();
  });
  afterEach(() => vi.useRealTimers());

  it('recovery detected during quiet hours is queued, not lost, then flushed after quiet', async () => {
    const state = tmpState();
    const { channel, sends } = fakeChannel();
    const poller = new Poller(channel, 'ou_x', state);

    // 上一轮快照：cc 5h 窗口 01:30 重置。
    state.get().providerSnapshots = {
      cc: { utilization: 90, resetAt: new Date(2026, 5, 24, 1, 30).toISOString() },
    };
    state.get().lastBedtimePromptDate = '2026-06-24'; // 屏蔽睡前卡干扰

    // tick #1：凌晨 02:00（安静时段）。检测到恢复（util 0、距重置 30min），当前窗口记录的重置点 03:00。
    vi.setSystemTime(new Date(2026, 5, 24, 2, 0));
    mockDetect.mockResolvedValue(ccStatus(0, new Date(2026, 5, 24, 3, 0)));
    await (poller as unknown as { tick: () => Promise<void> }).tick();

    expect(sends).toHaveLength(0); // 安静时段不打扰
    expect(state.get().pendingNotifications).toHaveLength(1); // 但已入队，未丢

    // tick #2：早上 09:00（非安静）。无新检测（距 03:00 已 6h > 4h 新鲜度），仅补发队列。
    vi.setSystemTime(new Date(2026, 5, 24, 9, 0));
    mockDetect.mockResolvedValue(ccStatus(0, new Date(2026, 5, 24, 3, 0)));
    await (poller as unknown as { tick: () => Promise<void> }).tick();

    expect(sends).toHaveLength(1); // 醒来后收到补发
    expect(state.get().pendingNotifications).toHaveLength(0);
  });

  it('snooze coming due during quiet hours is preserved, not silently dropped', async () => {
    const state = tmpState();
    const { channel, sends } = fakeChannel();
    const poller = new Poller(channel, 'ou_x', state);
    state.get().lastBedtimePromptDate = '2026-06-24';
    mockDetect.mockResolvedValue({}); // 无 agent → 无新检测，单测 snooze

    // 已 snooze 的提醒，dueAt 落在安静时段（02:00）。
    state.get().pendingRecovery = {
      provider: 'cc',
      windowKey: 'cc:2026-06-24T01:30:00.000Z',
      dueAt: new Date(2026, 5, 24, 1, 30).toISOString(),
    };

    // tick #1：02:00 安静时段，snooze 已到期。
    vi.setSystemTime(new Date(2026, 5, 24, 2, 0));
    await (poller as unknown as { tick: () => Promise<void> }).tick();
    expect(sends).toHaveLength(0); // 不在安静时段发
    expect(state.get().pendingRecovery).toBeNull(); // 已移入队列
    expect(state.get().pendingNotifications).toHaveLength(1); // 没丢

    // tick #2：09:00 非安静，补发。
    vi.setSystemTime(new Date(2026, 5, 24, 9, 0));
    await (poller as unknown as { tick: () => Promise<void> }).tick();
    expect(sends).toHaveLength(1);
    expect(state.get().pendingNotifications).toHaveLength(0);
  });

  it('schedules a reset-check at resetsAt+90s that fires recovery promptly (no blind polling)', async () => {
    const state = tmpState();
    const { channel, sends } = fakeChannel();
    const poller = new Poller(channel, 'ou_x', state);
    state.get().lastBedtimePromptDate = '2026-06-24'; // 屏蔽睡前卡

    // tick @12:00：cc 用了 80%，窗口将于 12:05 重置 → 排一个 12:06:30 的复查。
    vi.setSystemTime(new Date(2026, 5, 24, 12, 0, 0));
    mockDetect.mockResolvedValue(ccStatus(80, new Date(2026, 5, 24, 12, 5, 0)));
    await (poller as unknown as { tick: () => Promise<void> }).tick();
    expect(sends).toHaveLength(0); // 还没到重置点

    // 推进到 12:06:30：复查定时器触发，此时 cc 已翻篇（util 0、新窗口 17:05）。
    mockDetect.mockResolvedValue(ccStatus(0, new Date(2026, 5, 24, 17, 5, 0)));
    await vi.advanceTimersByTimeAsync(6 * 60_000 + 30_000);
    expect(sends).toHaveLength(1); // 重置点 +90s 当场抓到回血、发了恢复卡

    poller.stop();
  });
});
