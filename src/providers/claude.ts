// Claude Code provider —— 读 macOS Keychain 的 OAuth token → 打 oauth/usage → 解析。
// 移植自 Python providers/claude.py。安全红线：token 只留内存。

import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { ProviderError, httpGetJson, type Provider, type ReadUsageOptions, type Usage, type WindowUsage } from './base.js';

const execFileAsync = promisify(execFile);

const KEYCHAIN_SERVICE = 'Claude Code-credentials';
const USAGE_URL = 'https://api.anthropic.com/api/oauth/usage';
const ANTHROPIC_BETA = 'oauth-2025-04-20';
const USER_AGENT = 'quota-butler/0.1';
const HTTP_TIMEOUT_MS = 15000;
const WINDOW_SECONDS: Record<string, number> = { five_hour: 5 * 3600, seven_day: 7 * 86400 };

export class ClaudeProvider implements Provider {
  readonly name = 'cc';

  // CC 始终有 5h 窗口（付费档），不存在免费档烧额度问题：忽略 opts，签名仅为对齐 Provider 接口。
  async readUsage(_opts?: ReadUsageOptions): Promise<Usage> {
    const token = await this.readToken();
    const raw = await fetchUsage(token);
    return parseUsage(raw);
  }

  /** 从 Keychain 读 access token；token 只在返回值里流转，不落盘。 */
  private async readToken(): Promise<string> {
    let stdout: string;
    try {
      const r = await execFileAsync(
        'security',
        ['find-generic-password', '-s', KEYCHAIN_SERVICE, '-w'],
        { timeout: 10000 },
      );
      stdout = r.stdout;
    } catch {
      throw new ProviderError(
        "Keychain 里没有 'Claude Code-credentials'，请确认本机已登录 Claude Code。",
        'auth',
      );
    }
    let oauth: Record<string, unknown> | undefined;
    try {
      oauth = (JSON.parse(stdout) as { claudeAiOauth?: Record<string, unknown> }).claudeAiOauth;
    } catch (e) {
      throw new ProviderError(`Keychain 凭据结构异常: ${(e as Error).message}`, 'parse');
    }
    const token = oauth?.['accessToken'];
    if (typeof token !== 'string' || !token) {
      throw new ProviderError('Keychain 凭据缺 accessToken', 'parse');
    }
    // 不再因本地 expiresAt 提前判过期：本地时间戳可能偏保守，会把"其实还能用"的 token
    // 误判成过期、只给 last-good 快照。直接拿 token 打 API，由服务端裁决——
    // 真过期会返回 401（上层据此判 TOKEN_STALE），否则拿到的是实时真实额度。
    return token;
  }

  /** `claude -p "<prompt>"` 发一条极短消息开窗。⚠️ 会产生一次真实请求。 */
  async warmup(prompt: string): Promise<string> {
    try {
      // 立刻给 stdin EOF：launchd 等无 TTY 环境下，claude -p 会等管道输入
      // （"no stdin data received in 3s …"）然后报错；关掉 stdin 等于 `< /dev/null`。
      const p = execFileAsync('claude', ['-p', prompt], { timeout: 120000 });
      p.child.stdin?.end();
      const r = await p;
      return r.stdout.trim().slice(0, 200);
    } catch (e) {
      const err = e as { stderr?: string; stdout?: string; message?: string; killed?: boolean };
      const raw = (err.stderr || err.stdout || err.message || '').slice(0, 200);
      let reason = err.killed ? '预热超时被终止（>120s，可能在调用工具）' : raw;
      if (/authenticat|invalid.*credential|401/i.test(raw)) {
        reason = 'Claude Code 登录已失效，请在终端运行 `claude`（或 claude auth login）重新登录后再试';
      }
      throw new ProviderError(`调用 claude 失败: ${reason}`);
    }
  }
}

async function fetchUsage(token: string): Promise<Record<string, unknown>> {
  const { status, body, retryAfterMs } = await httpGetJson(
    USAGE_URL,
    {
      Authorization: `Bearer ${token}`,
      'anthropic-beta': ANTHROPIC_BETA,
      Accept: 'application/json',
      'User-Agent': USER_AGENT,
    },
    HTTP_TIMEOUT_MS,
  );
  if (status === 401) throw new ProviderError('oauth/usage 返回 401：token 失效或权限不足。', 'auth');
  if (status === 429) throw new ProviderError('oauth/usage 429：查询太频繁被限流。', 'ratelimit', retryAfterMs);
  if (status < 200 || status >= 300) throw new ProviderError(`oauth/usage HTTP ${status}`, 'http');
  try {
    return JSON.parse(body) as Record<string, unknown>;
  } catch {
    throw new ProviderError('oauth/usage 返回非 JSON', 'parse');
  }
}

function parseUsage(raw: Record<string, unknown>): Usage {
  const five = windowUsage(raw['five_hour'], 'five_hour', true);
  const seven = windowUsage(raw['seven_day'], 'seven_day', false);
  return { provider: 'cc', fiveHour: five as WindowUsage, sevenDay: seven };
}

function windowUsage(node: unknown, key: string, required: boolean): WindowUsage | null {
  if (!node || typeof node !== 'object') {
    if (required) throw new ProviderError(`响应缺少 ${key} 字段`, 'parse');
    return null;
  }
  const n = node as Record<string, unknown>;
  const util = Number(n['utilization']);
  if (Number.isNaN(util)) {
    if (required) throw new ProviderError(`${key} 字段解析失败`, 'parse');
    return null;
  }
  return {
    utilization: util,
    resetsAt: parseDt(n['resets_at']),
    windowSeconds: WINDOW_SECONDS[key] ?? null,
  };
}

function parseDt(v: unknown): Date | null {
  if (v == null) return null;
  const d = new Date(String(v));
  return Number.isNaN(d.getTime()) ? null : d;
}
