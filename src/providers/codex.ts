// Codex provider —— 读 ~/.codex/auth.json 的 token → 打 wham/usage → 解析。
// 移植自 Python providers/codex.py。
// 窗口按真实时长归类：付费档 primary=5h/secondary=7天；免费档 primary=月度/secondary=null。
// 安全红线：token 只留内存。

import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { readFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';
import {
  ProviderError,
  httpGetJson,
  sleep,
  type Provider,
  type ReadUsageOptions,
  type Usage,
  type WindowUsage,
} from './base.js';

const execFileAsync = promisify(execFile);

const AUTH_PATH = join(homedir(), '.codex', 'auth.json');
const USAGE_URL = 'https://chatgpt.com/backend-api/wham/usage';
const USER_AGENT = 'quota-butler/0.1';
const HTTP_TIMEOUT_MS = 15000;
const CODEX_MODEL_ENV = 'QUOTA_BUTLER_CODEX_MODEL';

/** 副作用依赖注入口子：默认接真实实现，测试时可替换，免去 fs/网络/codex 二进制。 */
export interface CodexHooks {
  readAuth?: () => Promise<{ token: string; accountId: string }>;
  fetchUsage?: (token: string, accountId: string) => Promise<{ status: number; body: string; retryAfterMs?: number }>;
  refreshToken?: () => Promise<void>;
}

export class CodexProvider implements Provider {
  readonly name = 'codex';
  private readonly readAuthFn: NonNullable<CodexHooks['readAuth']>;
  private readonly fetchUsageFn: NonNullable<CodexHooks['fetchUsage']>;
  private readonly refreshTokenFn: NonNullable<CodexHooks['refreshToken']>;

  constructor(hooks: CodexHooks = {}) {
    this.readAuthFn = hooks.readAuth ?? readAuth;
    this.fetchUsageFn = hooks.fetchUsage ?? fetchUsage;
    this.refreshTokenFn = hooks.refreshToken ?? refreshToken;
  }

  async readUsage(opts: ReadUsageOptions = {}): Promise<Usage> {
    const allowRefresh = opts.allowRefresh ?? true;
    let { token, accountId } = await this.readAuthFn();
    let refreshed = false;
    let serverRetries = 1;
    for (;;) {
      const { status, body, retryAfterMs } = await this.fetchUsageFn(token, accountId);
      if (status === 401) {
        // 只读感知（allowRefresh=false）下绝不触发 codex exec：免费档会因此烧月额度。
        // 沿用上次快照、标记暂不可用（stale→UNAVAILABLE），待用户主动操作时再刷新。
        if (!allowRefresh) {
          throw new ProviderError(
            'wham/usage 返回 401：Codex token 过期。本轮为只读感知，跳过 codex exec 刷新（免费档会烧月额度），沿用上次快照并标记暂不可用。',
            'stale',
          );
        }
        if (!refreshed) {
          refreshed = true;
          await this.refreshTokenFn();
          ({ token, accountId } = await this.readAuthFn());
          continue;
        }
        throw new ProviderError('wham/usage 返回 401：Codex token 失效，用一次 codex CLI 让它刷新后重试。', 'expired');
      }
      if (status >= 500 && status < 600 && serverRetries > 0) {
        serverRetries -= 1;
        await sleep(500);
        continue;
      }
      if (status === 429) throw new ProviderError('wham/usage 429：查询太频繁被限流。', 'ratelimit', retryAfterMs);
      if (status < 200 || status >= 300) throw new ProviderError(`wham/usage HTTP ${status}`, 'http');
      let raw: Record<string, unknown>;
      try {
        raw = JSON.parse(body) as Record<string, unknown>;
      } catch {
        throw new ProviderError('wham/usage 返回非 JSON', 'parse');
      }
      return parseUsage(raw);
    }
  }

  /** 预热开窗：等价 `echo "<prompt>" | codex exec --skip-git-repo-check`。 */
  async warmup(prompt: string): Promise<string> {
    try {
      // 非 TTY（后台管道）下 codex exec 从 stdin 读 prompt——把 prompt 写进 stdin 再关闭。
      // --skip-git-repo-check：后台 CWD 多半不是 git 受信目录，不加 codex 直接退出码 1。
      const p = execFileAsync('codex', codexExecArgs(), { timeout: 120000 });
      p.child.stdin?.end(`${prompt}\n`); // 带换行，跟 `echo "..." | codex exec` 完全一致
      const r = await p;
      return r.stdout.trim().slice(0, 200);
    } catch (e) {
      const err = e as { stderr?: string; stdout?: string; message?: string; killed?: boolean };
      const raw = err.stderr || err.stdout || err.message || '';
      const reason = formatCodexExecFailure(raw, Boolean(err.killed), '预热超时被终止（>120s）');
      throw new ProviderError(`codex exec 失败: ${reason}`);
    }
  }
}

/** 读 ~/.codex/auth.json 的 token / account_id。token 只在返回值里流转，不落盘。 */
async function readAuth(): Promise<{ token: string; accountId: string }> {
  let text: string;
  try {
    text = await readFile(AUTH_PATH, 'utf-8');
  } catch {
    throw new ProviderError(`${AUTH_PATH} 不存在；请先用 codex CLI 登录（codex login）。`, 'auth');
  }
  let tokens: Record<string, unknown> | undefined;
  try {
    tokens = (JSON.parse(text) as { tokens?: Record<string, unknown> }).tokens;
  } catch (e) {
    // 文件损坏 ≠ 未登录：归 parse（上层走 UNAVAILABLE，不误导用户去 login）。
    throw new ProviderError(`Codex auth.json 结构异常: ${(e as Error).message}`, 'parse');
  }
  const token = tokens?.['access_token'];
  const accountId = tokens?.['account_id'];
  if (typeof token !== 'string' || !token || typeof accountId !== 'string' || !accountId) {
    throw new ProviderError('Codex auth.json 缺 access_token / account_id', 'parse');
  }
  return { token, accountId };
}

/** `codex exec "ping"` 强制 codex CLI 刷新 ~/.codex/auth.json 里的 token。会消耗额度，仅限非感知路径。 */
async function refreshToken(): Promise<void> {
  try {
    const p = execFileAsync('codex', codexExecArgs(), { timeout: 45000 });
    p.child.stdin?.end('ping\n'); // codex exec 从 stdin 读 prompt（带换行）
    await p;
  } catch (e) {
    const err = e as { stderr?: string; stdout?: string; message?: string; killed?: boolean };
    const raw = err.stderr || err.stdout || err.message || '';
    throw new ProviderError(`调用 codex 刷新 token 失败: ${formatCodexExecFailure(raw, Boolean(err.killed), '刷新超时被终止（>45s）')}`, 'network');
  }
}

function codexExecArgs(): string[] {
  const args = ['exec', '--skip-git-repo-check'];
  const model = process.env[CODEX_MODEL_ENV]?.trim();
  if (model) args.push('--model', model);
  return args;
}

export function formatCodexExecFailure(raw: string, killed = false, timeoutMessage = '预热超时被终止'): string {
  if (killed) return timeoutMessage;
  // codex 会在前面刷一堆 skill 加载 ERROR + "Reading prompt from stdin"——过滤掉，真正的失败原因在后面。
  const meaningful = raw
    .split('\n')
    .filter((l) => l.trim() && !/failed to load skill|Reading (prompt|additional input)/i.test(l))
    .join(' ')
    .trim();
  const reason = meaningful || raw.trim();
  const unsupportedChatgptModel = /The '([^']+)' model is not supported when using Codex with a ChatGPT account/i.exec(reason);
  if (unsupportedChatgptModel) {
    const model = unsupportedChatgptModel[1];
    return `Codex CLI 当前模型 ${model} 不支持 ChatGPT 账号。请在远端机器的 ~/.codex/config.toml 改成账号可用模型，或给 quota-butler 进程设置 ${CODEX_MODEL_ENV}=<可用模型> 后重启。原始错误：${reason.slice(-200)}`;
  }
  return reason.slice(-200);
}

async function fetchUsage(token: string, accountId: string): Promise<{ status: number; body: string }> {
  return httpGetJson(
    USAGE_URL,
    {
      Authorization: `Bearer ${token}`,
      'chatgpt-account-id': accountId,
      Accept: 'application/json',
      'User-Agent': USER_AGENT,
    },
    HTTP_TIMEOUT_MS,
  );
}

const SIX_HOURS = 6 * 3600;
const TEN_DAYS = 10 * 86400;

/** 按窗口真实时长（limit_window_seconds）归类，而不是假设 primary=5h。
 *  付费档：primary=5h、secondary=7天；免费档：primary=月度、secondary=null。 */
function classifyWindow(seconds: number | null | undefined): 'five' | 'week' | 'month' {
  if (seconds == null) return 'five'; // 未给时长 → 退回旧默认（按短窗处理）
  if (seconds <= SIX_HOURS) return 'five';
  if (seconds <= TEN_DAYS) return 'week';
  return 'month';
}

function parseUsage(raw: Record<string, unknown>): Usage {
  const rl = (raw['rate_limit'] as Record<string, unknown> | undefined) ?? {};
  const primary = windowUsage(rl['primary_window']);
  if (!primary) throw new ProviderError('wham/usage 缺 rate_limit.primary_window', 'parse');
  const secondary = windowUsage(rl['secondary_window']);
  const usage: Usage = { provider: 'codex', fiveHour: null };
  for (const w of [primary, secondary]) {
    if (!w) continue;
    const slot = classifyWindow(w.windowSeconds);
    if (slot === 'five') usage.fiveHour = w;
    else if (slot === 'week') usage.sevenDay = w;
    else usage.monthly = w;
  }
  return usage;
}

function windowUsage(node: unknown): WindowUsage | null {
  if (!node || typeof node !== 'object') return null;
  const n = node as Record<string, unknown>;
  const util = Number(n['used_percent']);
  if (Number.isNaN(util)) throw new ProviderError('Codex 窗口字段解析失败', 'parse');
  const win = Number(n['limit_window_seconds']);
  return {
    utilization: util,
    resetsAt: epochToDate(n['reset_at']),
    windowSeconds: Number.isFinite(win) ? win : null,
  };
}

/** epoch → Date：秒级（10 位）补成毫秒；非数/越界返回 null（避免 NaN/几十年错位静默漂移）。 */
function epochToDate(value: unknown): Date | null {
  if (value == null) return null;
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return null;
  const ms = n > 1e12 ? n : n * 1000;
  const d = new Date(ms);
  return Number.isNaN(d.getTime()) ? null : d;
}
