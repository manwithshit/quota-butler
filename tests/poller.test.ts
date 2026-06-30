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

function statusFor(provider: string, usage: Usage): Record<string, AgentStatus> {
  return { [provider]: { provider, state: AgentState.CONNECTED, usage } };
}

function windowSnap(utilization: number, resetAt: Date): { utilization: number; resetAt: string; capturedAt: string } {
  return {
    utilization,
    resetAt: resetAt.toISOString(),
    capturedAt: new Date(resetAt.getTime() - 3600000).toISOString(),
  };
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

  it('dedups recovery across resetAt drift within tolerance (no duplicate card)', async () => {
    const state = tmpState();
    const { channel, sends } = fakeChannel();
    const poller = new Poller(channel, 'ou_x', state);
    state.get().lastBedtimePromptDate = '2026-06-24';
    state.get().providerSnapshots = {
      cc: { utilization: 90, resetAt: new Date(2026, 5, 24, 11, 0).toISOString() },
    };

    // tick #1 @11:10：cc 翻篇（util 0），上一窗口重置点 11:00 → 发一张恢复卡。
    vi.setSystemTime(new Date(2026, 5, 24, 11, 10));
    mockDetect.mockResolvedValue(ccStatus(0, new Date(2026, 5, 24, 16, 0)));
    await (poller as unknown as { tick: () => Promise<void> }).tick();
    expect(sends).toHaveLength(1);

    // tick #2 @11:12：后端把同一窗口的 resetAt 漂了 +40s（11:00:40）。
    // 精确等值会判成"新窗口"重发；容差去重应判为同窗 → 不再发。
    vi.setSystemTime(new Date(2026, 5, 24, 11, 12));
    state.get().providerSnapshots = {
      cc: { utilization: 90, resetAt: new Date(2026, 5, 24, 11, 0, 40).toISOString() },
    };
    mockDetect.mockResolvedValue(ccStatus(0, new Date(2026, 5, 24, 16, 0)));
    await (poller as unknown as { tick: () => Promise<void> }).tick();
    expect(sends).toHaveLength(1); // 仍是 1 张，没重发
  });

  it('cooldown backstop drops a re-queued recovery card within 30min', async () => {
    const state = tmpState();
    const { channel, sends } = fakeChannel();
    const poller = new Poller(channel, 'ou_x', state);

    vi.setSystemTime(new Date(2026, 5, 24, 11, 0));
    // 队列里已有一张待发；模拟"刚发过"（10:50）。
    state.get().pendingNotifications = [{ provider: 'codex', windowKey: 'codex:2026-06-24T11:00:00.000Z' }];
    state.get().lastRecoverySentAt = { codex: new Date(2026, 5, 24, 10, 50).toISOString() };

    await (poller as unknown as { flushNotifications: () => Promise<void> }).flushNotifications();
    expect(sends).toHaveLength(0); // 冷却期内丢弃，不发
    expect(state.get().pendingNotifications).toHaveLength(0); // 已出队，不会无限重试
  });

  it('schedules a reset-check from last-good snapshot when CC is currently unreadable', async () => {
    const state = tmpState();
    const { channel, sends } = fakeChannel();
    const poller = new Poller(channel, 'ou_x', state);
    state.get().lastBedtimePromptDate = '2026-06-24';
    // last-good 快照：cc 5h 窗口将于 12:05 重置（但当前读不到 cc）。
    state.get().usageSnapshots = {
      cc: {
        fiveHourUtil: 95,
        fiveHourResetAt: new Date(2026, 5, 24, 12, 5, 0).toISOString(),
        sevenDayUtil: 10,
        capturedAt: new Date(2026, 5, 24, 11, 0, 0).toISOString(),
      },
    };

    // tick @12:00：cc 令牌过期读不到（detect 返回空）→ 仍应据快照排 12:06:30 复查。
    vi.setSystemTime(new Date(2026, 5, 24, 12, 0, 0));
    mockDetect.mockResolvedValue({});
    await (poller as unknown as { tick: () => Promise<void> }).tick();
    expect(sends).toHaveLength(0);

    // 12:06:30：cc 恢复可读（token 自愈，util 0、新窗口 17:05），复查当场抓到 → 发卡。
    state.get().providerSnapshots = {
      cc: { utilization: 95, resetAt: new Date(2026, 5, 24, 12, 5, 0).toISOString() },
    };
    mockDetect.mockResolvedValue(ccStatus(0, new Date(2026, 5, 24, 17, 5, 0)));
    await vi.advanceTimersByTimeAsync(6 * 60_000 + 30_000);
    expect(sends).toHaveLength(1);

    poller.stop();
  });

  it('emits weekly recovery instead of a duplicate five-hour recovery when both reset together', async () => {
    const state = tmpState();
    const { channel, sends } = fakeChannel();
    const poller = new Poller(channel, 'ou_x', state);
    const reset = new Date(2026, 5, 24, 12, 5, 0);
    state.get().lastBedtimePromptDate = '2026-06-24';
    state.get().providerWindowSnapshots = {
      cc: {
        fiveHour: windowSnap(90, reset),
        sevenDay: windowSnap(95, reset),
      },
    };

    vi.setSystemTime(new Date(2026, 5, 24, 12, 10, 0));
    mockDetect.mockResolvedValue(statusFor('cc', {
      provider: 'cc',
      fiveHour: { utilization: 0, resetsAt: new Date(2026, 5, 24, 17, 5, 0), windowSeconds: 18000 },
      sevenDay: { utilization: 0, resetsAt: new Date(2026, 6, 1, 12, 5, 0), windowSeconds: 604800 },
    }));

    await (poller as unknown as { tick: () => Promise<void> }).tick();

    expect(sends).toHaveLength(1);
    expect(JSON.stringify(sends[0])).toContain('周额度已刷新');
    expect(state.get().lastRecoveryNotifiedWindows['cc:sevenDay']).toBe('cc:sevenDay:2026-06-24T04:05:00.000Z');
    expect(state.get().lastRecoveryNotifiedWindows['cc:fiveHour']).toBeUndefined();
  });

  it('keeps a plain five-hour recovery when weekly quota did not reset', async () => {
    const state = tmpState();
    const { channel, sends } = fakeChannel();
    const poller = new Poller(channel, 'ou_x', state);
    const reset = new Date(2026, 5, 24, 12, 5, 0);
    state.get().lastBedtimePromptDate = '2026-06-24';
    state.get().providerWindowSnapshots = {
      cc: {
        fiveHour: windowSnap(90, reset),
        sevenDay: windowSnap(10, new Date(2026, 5, 30, 12, 5, 0)),
      },
    };

    vi.setSystemTime(new Date(2026, 5, 24, 12, 10, 0));
    mockDetect.mockResolvedValue(statusFor('cc', {
      provider: 'cc',
      fiveHour: { utilization: 0, resetsAt: new Date(2026, 5, 24, 17, 5, 0), windowSeconds: 18000 },
      sevenDay: { utilization: 10, resetsAt: new Date(2026, 5, 30, 12, 5, 0), windowSeconds: 604800 },
    }));

    await (poller as unknown as { tick: () => Promise<void> }).tick();

    expect(sends).toHaveLength(1);
    expect(JSON.stringify(sends[0])).toContain('5 小时额度已恢复');
    expect(state.get().lastRecoveryNotifiedWindows['cc:fiveHour']).toBe('cc:fiveHour:2026-06-24T04:05:00.000Z');
  });

  it('detects paid Codex weekly recovery when its five-hour reset triggers the check', async () => {
    const state = tmpState();
    const { channel, sends } = fakeChannel();
    const poller = new Poller(channel, 'ou_x', state);
    const reset = new Date(2026, 5, 24, 12, 5, 0);
    state.get().lastBedtimePromptDate = '2026-06-24';
    state.get().providerWindowSnapshots = {
      codex: {
        fiveHour: windowSnap(90, reset),
        sevenDay: windowSnap(80, reset),
      },
    };

    vi.setSystemTime(new Date(2026, 5, 24, 12, 10, 0));
    mockDetect.mockResolvedValue(statusFor('codex', {
      provider: 'codex',
      fiveHour: { utilization: 0, resetsAt: new Date(2026, 5, 24, 17, 5, 0), windowSeconds: 18000 },
      sevenDay: { utilization: 2, resetsAt: new Date(2026, 6, 1, 12, 5, 0), windowSeconds: 604800 },
    }));

    await (poller as unknown as { tick: () => Promise<void> }).tick();

    expect(sends).toHaveLength(1);
    expect(JSON.stringify(sends[0])).toContain('Codex 周额度已刷新');
  });

  it('does not send monthly-only Codex recovery reminders', async () => {
    const state = tmpState();
    const { channel, sends } = fakeChannel();
    const poller = new Poller(channel, 'ou_x', state);
    const reset = new Date(2026, 5, 24, 12, 5, 0);
    state.get().lastBedtimePromptDate = '2026-06-24';
    state.get().providerWindowSnapshots = {
      codex: {
        monthly: windowSnap(92, reset),
      },
    };

    vi.setSystemTime(new Date(2026, 5, 24, 12, 10, 0));
    mockDetect.mockResolvedValue(statusFor('codex', {
      provider: 'codex',
      fiveHour: null,
      monthly: { utilization: 0, resetsAt: new Date(2026, 6, 24, 12, 5, 0), windowSeconds: 2592000 },
    }));

    await (poller as unknown as { tick: () => Promise<void> }).tick();

    expect(sends).toHaveLength(0);
    expect(state.get().pendingNotifications).toHaveLength(0);
  });

  it('queues weekly recovery during quiet hours and flushes it later', async () => {
    const state = tmpState();
    const { channel, sends } = fakeChannel();
    const poller = new Poller(channel, 'ou_x', state);
    const reset = new Date(2026, 5, 24, 1, 30, 0);
    state.get().lastBedtimePromptDate = '2026-06-24';
    state.get().providerWindowSnapshots = {
      cc: {
        fiveHour: windowSnap(95, reset),
        sevenDay: windowSnap(100, reset),
      },
    };

    vi.setSystemTime(new Date(2026, 5, 24, 2, 0, 0));
    mockDetect.mockResolvedValue(statusFor('cc', {
      provider: 'cc',
      fiveHour: { utilization: 0, resetsAt: new Date(2026, 5, 24, 6, 30, 0), windowSeconds: 18000 },
      sevenDay: { utilization: 0, resetsAt: new Date(2026, 6, 1, 1, 30, 0), windowSeconds: 604800 },
    }));
    await (poller as unknown as { tick: () => Promise<void> }).tick();

    expect(sends).toHaveLength(0);
    expect(state.get().pendingNotifications).toMatchObject([{ provider: 'cc', window: 'sevenDay' }]);

    vi.setSystemTime(new Date(2026, 5, 24, 9, 0, 0));
    mockDetect.mockResolvedValue(statusFor('cc', {
      provider: 'cc',
      fiveHour: { utilization: 0, resetsAt: new Date(2026, 5, 24, 6, 30, 0), windowSeconds: 18000 },
      sevenDay: { utilization: 0, resetsAt: new Date(2026, 6, 1, 1, 30, 0), windowSeconds: 604800 },
    }));
    await (poller as unknown as { tick: () => Promise<void> }).tick();

    expect(sends).toHaveLength(1);
    expect(state.get().pendingNotifications).toHaveLength(0);
  });

  it('uses per-window cooldown so one recovery window does not suppress another', async () => {
    const state = tmpState();
    const { channel, sends } = fakeChannel();
    const poller = new Poller(channel, 'ou_x', state);

    vi.setSystemTime(new Date(2026, 5, 24, 11, 0));
    state.get().pendingNotifications = [
      { provider: 'cc', window: 'fiveHour', windowKey: 'cc:fiveHour:2026-06-24T10:00:00.000Z' },
      { provider: 'cc', window: 'sevenDay', windowKey: 'cc:sevenDay:2026-06-24T10:00:00.000Z' },
    ];
    state.get().lastRecoverySentAt = { 'cc:fiveHour': new Date(2026, 5, 24, 10, 50).toISOString() };

    await (poller as unknown as { flushNotifications: () => Promise<void> }).flushNotifications();

    expect(sends).toHaveLength(1);
    expect(JSON.stringify(sends[0])).toContain('周额度已刷新');
    expect(state.get().pendingNotifications).toHaveLength(0);
  });

  it('logs provider state and recovery details without token fields', async () => {
    const state = tmpState();
    const { channel } = fakeChannel();
    const poller = new Poller(channel, 'ou_x', state);
    const spy = vi.spyOn(console, 'log').mockImplementation(() => {});
    state.get().lastBedtimePromptDate = '2026-06-24';
    state.get().providerSnapshots = {
      cc: { utilization: 90, resetAt: new Date(2026, 5, 24, 11, 0).toISOString() },
    };

    vi.setSystemTime(new Date(2026, 5, 24, 11, 10));
    mockDetect.mockResolvedValue(ccStatus(0, new Date(2026, 5, 24, 16, 0)));
    await (poller as unknown as { tick: () => Promise<void> }).tick();

    const logs = spy.mock.calls.map((call) => call.join(' ')).join('\n');
    spy.mockRestore();
    expect(logs).toContain('provider=cc state=connected');
    expect(logs).toContain('recovery provider=cc window=fiveHour');
    expect(logs).not.toMatch(/accessToken|refreshToken|accountId/i);
  });
});
