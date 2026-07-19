// Codex provider 的"感知不烧额度"红线：只读感知（allowRefresh=false）下，
// token 过期（401）绝不触发 codex exec 刷新——否则免费档每 15 分钟轮询都会啃掉月额度。

import { describe, it, expect, vi } from 'vitest';
import { CodexProvider, formatCodexExecFailure } from '../src/providers/codex.js';
import { ProviderError } from '../src/providers/base.js';

const AUTH = { token: 't', accountId: 'a' };

// 免费档返回：月度 primary（~30 天）、secondary=null。
const FREE_BODY = JSON.stringify({
  rate_limit: {
    primary_window: { used_percent: 12, limit_window_seconds: 2592000, reset_at: 1700000000 },
    secondary_window: null,
  },
});

const WEEKLY_ONLY_BODY = JSON.stringify({
  rate_limit: {
    primary_window: { used_percent: 23, limit_window_seconds: 604800, reset_at: 1785070591 },
    secondary_window: null,
  },
});

const FIVE_PLUS_WEEKLY_BODY = JSON.stringify({
  rate_limit: {
    primary_window: { used_percent: 40, limit_window_seconds: 18000, reset_at: 1785070591 },
    secondary_window: { used_percent: 30, limit_window_seconds: 604800, reset_at: 1785502591 },
  },
});

describe('CodexProvider —— 感知侧刷新闸门', () => {
  it('allowRefresh=false 且 401：不跑 codex exec，抛 stale（→上层 UNAVAILABLE，不误判登出）', async () => {
    const refreshToken = vi.fn(async () => {});
    const fetchUsage = vi.fn(async () => ({ status: 401, body: '' }));
    const p = new CodexProvider({ readAuth: async () => AUTH, fetchUsage, refreshToken });

    await expect(p.readUsage({ allowRefresh: false })).rejects.toMatchObject({
      kind: 'stale',
    });
    expect(refreshToken).not.toHaveBeenCalled();
    expect(fetchUsage).toHaveBeenCalledTimes(1); // 不重试、不刷新
  });

  it('allowRefresh=true 且 401→200：刷新一次后成功（付费/用户主动路径维持原行为）', async () => {
    const refreshToken = vi.fn(async () => {});
    const fetchUsage = vi
      .fn()
      .mockResolvedValueOnce({ status: 401, body: '' })
      .mockResolvedValueOnce({ status: 200, body: FREE_BODY });
    const p = new CodexProvider({ readAuth: async () => AUTH, fetchUsage, refreshToken });

    const usage = await p.readUsage({ allowRefresh: true });
    expect(refreshToken).toHaveBeenCalledTimes(1);
    expect(usage.fiveHour).toBeNull();
    expect(usage.monthly?.utilization).toBe(12);
  });

  it('默认（无 opts）保持原行为：401 时允许刷新', async () => {
    const refreshToken = vi.fn(async () => {});
    const fetchUsage = vi
      .fn()
      .mockResolvedValueOnce({ status: 401, body: '' })
      .mockResolvedValueOnce({ status: 200, body: FREE_BODY });
    const p = new CodexProvider({ readAuth: async () => AUTH, fetchUsage, refreshToken });

    await p.readUsage();
    expect(refreshToken).toHaveBeenCalledTimes(1);
  });

  it('classifies a weekly-only Codex response without inventing a 5h window', async () => {
    const p = new CodexProvider({
      readAuth: async () => AUTH,
      fetchUsage: async () => ({ status: 200, body: WEEKLY_ONLY_BODY }),
      refreshToken: async () => {},
    });
    const usage = await p.readUsage({ allowRefresh: false });
    expect(usage.fiveHour).toBeNull();
    expect(usage.sevenDay?.utilization).toBe(23);
    expect(usage.monthly ?? null).toBeNull();
  });

  it('keeps the legacy 5h + weekly response shape unchanged', async () => {
    const p = new CodexProvider({
      readAuth: async () => AUTH,
      fetchUsage: async () => ({ status: 200, body: FIVE_PLUS_WEEKLY_BODY }),
      refreshToken: async () => {},
    });
    const usage = await p.readUsage({ allowRefresh: false });
    expect(usage.fiveHour?.utilization).toBe(40);
    expect(usage.sevenDay?.utilization).toBe(30);
    expect(usage.monthly ?? null).toBeNull();
  });

  it('stale 错误归类为 ProviderError，便于上层 kind 分流', async () => {
    const p = new CodexProvider({
      readAuth: async () => AUTH,
      fetchUsage: async () => ({ status: 401, body: '' }),
      refreshToken: async () => {},
    });
    await expect(p.readUsage({ allowRefresh: false })).rejects.toBeInstanceOf(ProviderError);
  });

  it('codex exec 模型不兼容 ChatGPT 账号时，给出远端配置指引', () => {
    const raw = `Reading prompt from stdin...
ERROR: {"type":"error","status":400,"error":{"type":"invalid_request_error","message":"The 'gpt-5.2-codex' model is not supported when using Codex with a ChatGPT account."}}`;

    const reason = formatCodexExecFailure(raw);

    expect(reason).toContain('gpt-5.2-codex');
    expect(reason).toContain('不支持 ChatGPT 账号');
    expect(reason).toContain('QUOTA_BUTLER_CODEX_MODEL');
    expect(reason).toContain('~/.codex/config.toml');
    expect(reason).not.toContain('Reading prompt from stdin');
  });
});
