// 检测已安装 / 已登录 / 可调度的 Agent。移植自 Python agent_status.py。
// 新增 TOKEN_STALE：cc 过了 login probe（loggedIn:true）后 readUsage 仍 auth 失败
// = 缓存令牌过期可刷新，不是登出。

import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { access, constants } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { ProviderError, getProvider, type ProviderTier, type Usage } from './providers/index.js';

const execFileAsync = promisify(execFile);

export enum AgentState {
  CONNECTED = 'connected',
  NEEDS_LOGIN = 'needs_login',
  TOKEN_STALE = 'token_stale', // 登录有效但缓存令牌过期（可刷新，非登出）
  UNAVAILABLE = 'unavailable',
  NOT_INSTALLED = 'not_installed',
}

export interface AgentStatus {
  provider: string;
  state: AgentState;
  executable?: string;
  usage?: Usage;
  detail?: string;
}

export interface PlanningUsageSnapshot {
  fiveHourUtil: number | null;
  fiveHourResetAt: string | null;
  sevenDayUtil: number | null;
  sevenDayResetAt?: string | null;
  monthlyUtil?: number | null;
  monthlyResetAt?: string | null;
  capturedAt: string;
}

export function isSchedulable(s: AgentStatus): boolean {
  return s.state === AgentState.CONNECTED && s.usage != null;
}

// 规划候选只看周额度：预热多在夜间/空闲执行，那时 5h 必然满，
// 用"当前 5h 剩余"做判断没意义；周额度才是不会很快回血的真上限。
const PLANNING_MIN_WEEKLY_REMAINING = 10;

/** 是否值得围绕它排计划：
 *  1) 必须有 5h 窗口——预热/双窗口/接力都靠"开 5h 窗口"，免费档 Codex 只有月度窗口，
 *     无 5h 可换挡、且预热会烧月度额度，故不参与规划（仍可在额度卡里查看）。
 *  2) 周额度还有余量（周见底=排了也用不了）。 */
export function usableForPlanning(usage: Usage): boolean {
  return usableForPlanningAt(usage, new Date());
}

export function usableForPlanningAt(usage: Usage, planningAt: Date): boolean {
  if (!usage.fiveHour) return false;
  if (usage.sevenDay && 100 - usage.sevenDay.utilization < PLANNING_MIN_WEEKLY_REMAINING) {
    const reset = usage.sevenDay.resetsAt;
    if (!reset || reset.getTime() > planningAt.getTime()) return false;
  }
  return true;
}

const PLANNING_SNAPSHOT_MAX_AGE_MS = 24 * 3600_000;

export function planningUsageForStatus(
  status: AgentStatus,
  snapshot: PlanningUsageSnapshot | undefined,
  planningAt: Date,
): Usage | null {
  if (status.state === AgentState.CONNECTED && status.usage) {
    return usableForPlanningAt(status.usage, planningAt) ? status.usage : null;
  }
  if (status.state !== AgentState.UNAVAILABLE && status.state !== AgentState.TOKEN_STALE) return null;
  const usage = usageFromSnapshot(status.provider, snapshot);
  if (usage) {
    console.log(`[planning] provider=${status.provider} state=${status.state} snapshot=yes`);
  }
  return usage && usableForPlanningAt(usage, planningAt) ? usage : null;
}

function usageFromSnapshot(provider: string, snapshot: PlanningUsageSnapshot | undefined): Usage | null {
  if (!snapshot || snapshot.fiveHourUtil == null) return null;
  const capturedAt = new Date(snapshot.capturedAt).getTime();
  if (Number.isNaN(capturedAt) || Math.max(0, Date.now() - capturedAt) > PLANNING_SNAPSHOT_MAX_AGE_MS) return null;
  const hasSevenDayReset = Object.prototype.hasOwnProperty.call(snapshot, 'sevenDayResetAt');
  return {
    provider,
    fiveHour: {
      utilization: snapshot.fiveHourUtil,
      resetsAt: parseSnapshotDate(snapshot.fiveHourResetAt),
      windowSeconds: 5 * 3600,
    },
    // 旧版 state 没有 sevenDayResetAt；此时不要把"周额度 100%"当作确定不可用，
    // 否则刚升级后仍会因为旧快照误判明日不可规划。
    sevenDay: snapshot.sevenDayUtil == null || (!hasSevenDayReset && 100 - snapshot.sevenDayUtil < PLANNING_MIN_WEEKLY_REMAINING) ? null : {
      utilization: snapshot.sevenDayUtil,
      resetsAt: parseSnapshotDate(snapshot.sevenDayResetAt),
      windowSeconds: 7 * 86400,
    },
    monthly: snapshot.monthlyUtil == null ? null : {
      utilization: snapshot.monthlyUtil,
      resetsAt: parseSnapshotDate(snapshot.monthlyResetAt),
      windowSeconds: 30 * 86400,
    },
  };
}

function parseSnapshotDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** detectAgents 调用语境。
 *  sensing：本轮是只读感知（15 分钟轮询），不应有任何烧额度的副作用。
 *  knownTiers：上次成功读到的各家档位缓存——感知时若认出免费档 Codex（monthly-only），
 *              便禁止其 401 自动刷新（codex exec 会烧月额度）。 */
export interface DetectOptions {
  sensing?: boolean;
  knownTiers?: Record<string, ProviderTier>;
}

// 进程内 usage 缓存：避免频繁查询（反复发"额度/日报"+ 轮询）把 oauth/usage 打到 429。
// TTL 内复用上次成功结果；真撞 429/网络抖动时也沿用它，显示真实数据而不是"读不到"。
// 间隔参考 cc-switch（分钟级、明确"避免频繁请求"）：取 5 分钟——配合 15min 后台轮询，
// 既不会把接口打爆，手动查到的也至多 5 分钟旧，足够"实时"。
const USAGE_CACHE_TTL_MS = 5 * 60_000;
const usageCache = new Map<string, { usage: Usage; at: number }>();

// 限流退避：一旦 429，就在这段时间内【完全不再打这个接口】——给限流一个安静的窗口去清零。
// 优先用服务端 Retry-After（精确）；没给才用这个默认值。默认必须够长：实测 Anthropic
// oauth/usage 的 Retry-After ≈ 59min，退避若比它短就会提前重试、把限流窗口反复重置（永不解除）。
const RATE_LIMIT_BACKOFF_MS = 60 * 60_000;
const RATE_LIMIT_BACKOFF_CAP_MS = 6 * 3600_000; // 退避上限 6h
const rateLimitUntil = new Map<string, number>();
// 连续 429 计数：每多撞一次就把退避翻倍（顽固限流靠"越退越久"的零请求窗口才能彻底清零）。成功读到即清零。
const rateLimitStrikes = new Map<string, number>();

export async function detectAgents(
  providers: readonly string[] = ['cc', 'codex'],
  opts: DetectOptions = {},
): Promise<Record<string, AgentStatus>> {
  const results: Record<string, AgentStatus> = {};
  for (const provider of providers) {
    const executable = await findAgentExecutable(provider);
    if (!executable) {
      results[provider] = { provider, state: AgentState.NOT_INSTALLED, detail: '本机未检测到 CLI' };
      continue;
    }
    const probe = await probeAgentLogin(provider, executable);
    if (probe) {
      results[provider] = { provider, state: probe[0], executable, detail: probe[1] };
      continue;
    }
    const cached = usageCache.get(provider);
    // TTL 内直接复用上次成功结果，少打一次接口。
    if (cached && Date.now() - cached.at < USAGE_CACHE_TTL_MS) {
      results[provider] = { provider, state: AgentState.CONNECTED, executable, usage: cached.usage };
      continue;
    }
    // 限流退避期内：完全不打接口。有缓存(哪怕已过 TTL)就沿用，否则标暂不可用。
    if ((rateLimitUntil.get(provider) ?? 0) > Date.now()) {
      if (cached) {
        results[provider] = { provider, state: AgentState.CONNECTED, executable, usage: cached.usage };
      } else {
        results[provider] = { provider, state: AgentState.UNAVAILABLE, executable, detail: '查询被限流，退避中，稍后自动重试' };
      }
      continue;
    }
    try {
      // 感知路径：仅当已知是免费档（monthly-only）才禁刷新——付费档/档位未知仍允许刷新（学习档位）。
      const allowRefresh = !opts.sensing || opts.knownTiers?.[provider] !== 'monthly-only';
      const usage = await getProvider(provider).readUsage({ allowRefresh });
      usageCache.set(provider, { usage, at: Date.now() });
      rateLimitStrikes.delete(provider); // 读通了，清零计数
      results[provider] = { provider, state: AgentState.CONNECTED, executable, usage };
    } catch (e) {
      const detail = e instanceof Error ? e.message : String(e);
      const kind = e instanceof ProviderError ? e.kind : undefined;
      // 撞 429 → 进入退避，这段时间不再打接口，让限流有机会清零。
      // 基准用服务端 Retry-After（没给才用默认 60min），再按连续撞 429 次数翻倍，封顶 6h、+60s 余量。
      if (kind === 'ratelimit') {
        const strikes = (rateLimitStrikes.get(provider) ?? 0) + 1;
        rateLimitStrikes.set(provider, strikes);
        const base = e instanceof ProviderError && e.retryAfterMs && e.retryAfterMs > 0 ? e.retryAfterMs : RATE_LIMIT_BACKOFF_MS;
        const backoff = Math.min(base * 2 ** (strikes - 1), RATE_LIMIT_BACKOFF_CAP_MS);
        rateLimitUntil.set(provider, Date.now() + backoff + 60_000);
      }
      // 限流 / 网络抖动：有缓存就沿用真实数据（哪怕略旧），不显示"读不到"。
      if (cached && (kind === 'ratelimit' || kind === 'network')) {
        results[provider] = { provider, state: AgentState.CONNECTED, executable, usage: cached.usage };
        continue;
      }
      // 优先用 provider 抛出的结构化 kind 分类；只有非 ProviderError 才退回脆弱的关键词匹配。
      const isAuthLike = kind ? kind === 'auth' || kind === 'expired' : looksLikeAuthError(detail);
      let state: AgentState;
      if (isAuthLike) {
        // cc 已过 login probe → auth 类失败 = 令牌过期可刷新（TOKEN_STALE）。
        state = provider === 'cc' ? AgentState.TOKEN_STALE : AgentState.NEEDS_LOGIN;
      } else {
        // parse（文件损坏）/ network / http / ratelimit（无缓存时）等都归"暂时不可用"。
        state = AgentState.UNAVAILABLE;
      }
      results[provider] = { provider, state, executable, detail };
    }
  }
  return results;
}

export async function findAgentExecutable(provider: string): Promise<string | null> {
  const binary = provider === 'cc' ? 'claude' : 'codex';
  try {
    const r = await execFileAsync('which', [binary]);
    const p = r.stdout.trim();
    if (p) return p;
  } catch {
    // not on PATH; fall through to common dirs
  }
  for (const dir of [
    '/usr/local/bin',
    '/opt/homebrew/bin',
    join(homedir(), '.local', 'bin'),
    join(homedir(), '.npm-global', 'bin'),
  ]) {
    const cand = join(dir, binary);
    if (await isExecutable(cand)) return cand;
  }
  return null;
}

async function isExecutable(path: string): Promise<boolean> {
  try {
    await access(path, constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

/** cc 专用：读 `claude auth status`，分类登录问题；返回 null 表示可继续读 usage。 */
async function probeAgentLogin(
  provider: string,
  executable: string,
): Promise<[AgentState, string] | null> {
  if (provider !== 'cc') return null;
  let output = '';
  let code = 0;
  try {
    const r = await execFileAsync(executable, ['auth', 'status'], { timeout: 10000 });
    output = (r.stdout || r.stderr || '').trim();
  } catch (e) {
    const err = e as { stdout?: string; stderr?: string; code?: number | string };
    output = (err.stdout || err.stderr || '').trim();
    code = typeof err.code === 'number' ? err.code : 1;
    if (!output) return [AgentState.UNAVAILABLE, 'Claude 登录状态读取失败'];
  }
  let data: { loggedIn?: boolean } = {};
  try {
    data = output ? (JSON.parse(output) as { loggedIn?: boolean }) : {};
  } catch {
    data = {};
  }
  if (data.loggedIn === false) {
    return [AgentState.NEEDS_LOGIN, 'Claude Code 未登录，请运行 claude auth login'];
  }
  if (code !== 0) {
    const low = output.toLowerCase();
    if (['login', 'logged', 'auth', 'token'].some((w) => low.includes(w))) {
      return [AgentState.NEEDS_LOGIN, output.slice(0, 200) || 'Claude Code 未登录'];
    }
    return [AgentState.UNAVAILABLE, output.slice(0, 200) || 'Claude 登录状态暂时无法读取'];
  }
  return null;
}

function looksLikeAuthError(detail: string): boolean {
  const low = detail.toLowerCase();
  return [
    '401',
    'token 失效',
    'token 已过期',
    'expired',
    'auth.json',
    "没有 'claude code-credentials'",
    '请先用 codex cli 登录',
    '缺 access_token',
  ].some((m) => low.includes(m.toLowerCase()));
}
