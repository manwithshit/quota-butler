// Provider 接口与统一数据结构（移植自 Python providers/base.py）。
// 安全红线：token 只在内存流转，不打印、不写盘、不外传。

import http from 'node:http';
import tls from 'node:tls';

/** 失败归因：让上层精确分类（auth/expired→登录类；其余→暂时不可用），
 *  不再靠错误文案子串匹配（旧 looksLikeAuthError 会把"auth.json 损坏"误判成未登录）。
 *  stale：令牌过期但本轮是只读感知、不允许做会烧额度的刷新——按"暂时不可用"处理，
 *         不归登录类（免费档 Codex 不该因感知触发 codex exec 烧月额度）。 */
export type ProviderErrorKind = 'auth' | 'expired' | 'parse' | 'network' | 'http' | 'stale' | 'ratelimit' | 'unknown';

export class ProviderError extends Error {
  readonly kind: ProviderErrorKind;
  readonly retryAfterMs?: number; // 429 时服务端 Retry-After（毫秒），上层据此精确退避
  constructor(message: string, kind: ProviderErrorKind = 'unknown', retryAfterMs?: number) {
    super(message);
    this.kind = kind;
    this.retryAfterMs = retryAfterMs;
  }
}

/** 解析 Retry-After 头：秒数（常见）或 HTTP 日期，返回毫秒。 */
export function parseRetryAfter(value: string | null | undefined): number | undefined {
  if (!value) return undefined;
  const secs = Number(value);
  if (Number.isFinite(secs)) return Math.max(0, secs * 1000);
  const date = Date.parse(value);
  return Number.isFinite(date) ? Math.max(0, date - Date.now()) : undefined;
}

/** 一个额度窗口的快照。 */
export interface WindowUsage {
  utilization: number; // 已用百分比 0–100
  resetsAt: Date | null; // 该窗口重置的绝对时间
  windowSeconds?: number | null; // 窗口时长（秒），区分 5h / 7天 / 月度
}

/** 一次感知的统一结果。
 *  窗口按真实时长归位：5h / 7天 / 月度。
 *  - CC、付费档 Codex：有 fiveHour（+ sevenDay）。
 *  - 免费档 Codex：只有 monthly（fiveHour=null）——无 5h 窗口可预热/接力。 */
export interface Usage {
  provider: string; // "cc" | "codex"
  fiveHour: WindowUsage | null;
  sevenDay?: WindowUsage | null;
  monthly?: WindowUsage | null;
}

/** 读 usage 的调用语境。
 *  allowRefresh=false：本轮只读感知，禁止任何会消耗额度的副作用（如 codex exec 刷 token）。
 *  默认 true，保持用户主动操作（查看额度/排计划）一如既往地刷新。 */
export interface ReadUsageOptions {
  allowRefresh?: boolean;
}

export interface Provider {
  readonly name: string;
  readUsage(opts?: ReadUsageOptions): Promise<Usage>;
  warmup(prompt: string): Promise<string>;
}

/** 账户档位：免费档 Codex 只有月度窗口（无 5h），付费档/CC 有 5h 窗口。
 *  缓存它，是为了"在成功读过一次后"就能在只读感知时认出免费档，跳过烧额度的刷新。 */
export type ProviderTier = 'monthly-only' | 'has-5h';

/** 由一次成功的 usage 推断档位：有 5h 窗口=付费档/CC，否则=免费档（仅月度）。 */
export function usageTier(usage: Usage): ProviderTier {
  return usage.fiveHour ? 'has-5h' : 'monthly-only';
}

/** GET JSON，返回 status + 原始 body；只在网络/超时失败时抛 ProviderError。
 *  非 2xx 不抛——由各 provider 自行判读状态码（Claude 401 文案 / Codex 401 重刷）。*/
export async function httpGetJson(
  url: string,
  headers: Record<string, string>,
  timeoutMs: number,
): Promise<{ status: number; body: string; retryAfterMs?: number }> {
  const proxyUrl = proxyFor(url);
  if (proxyUrl) {
    return httpGetJsonViaHttpProxy(url, headers, timeoutMs, proxyUrl);
  }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const resp = await fetch(url, { headers, signal: ctrl.signal });
    const body = await resp.text();
    return { status: resp.status, body, retryAfterMs: parseRetryAfter(resp.headers.get('retry-after')) };
  } catch (e) {
    throw new ProviderError(`网络错误: ${(e as Error)?.message ?? e}`, 'network');
  } finally {
    clearTimeout(timer);
  }
}

function proxyFor(url: string): string | null {
  const target = new URL(url);
  if (target.protocol !== 'https:') return null;
  if (isNoProxyHost(target.hostname)) return null;
  return process.env.HTTPS_PROXY || process.env.https_proxy || process.env.HTTP_PROXY || process.env.http_proxy || null;
}

function isNoProxyHost(host: string): boolean {
  const rules = (process.env.NO_PROXY || process.env.no_proxy || '')
    .split(',')
    .map((part) => part.trim().toLowerCase())
    .filter(Boolean);
  const normalized = host.toLowerCase();
  return rules.some((rule) => rule === '*' || normalized === rule || normalized.endsWith(`.${rule}`));
}

async function httpGetJsonViaHttpProxy(
  url: string,
  headers: Record<string, string>,
  timeoutMs: number,
  proxyUrl: string,
): Promise<{ status: number; body: string; retryAfterMs?: number }> {
  const target = new URL(url);
  const proxy = new URL(proxyUrl);
  if (proxy.protocol !== 'http:') {
    throw new ProviderError(`不支持的代理协议: ${proxy.protocol}`, 'network');
  }
  const proxyPort = proxy.port ? Number(proxy.port) : 80;
  const targetPort = target.port ? Number(target.port) : 443;

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      connectReq.destroy(new Error('request timeout'));
    }, timeoutMs);

    const done = (fn: () => void) => {
      clearTimeout(timer);
      fn();
    };

    const connectReq = http.request({
      host: proxy.hostname,
      port: proxyPort,
      method: 'CONNECT',
      path: `${target.hostname}:${targetPort}`,
      headers: { Host: `${target.hostname}:${targetPort}` },
    });

    connectReq.once('connect', (res, socket) => {
      if (res.statusCode !== 200) {
        socket.destroy();
        done(() => reject(new ProviderError(`代理 CONNECT HTTP ${res.statusCode ?? 'unknown'}`, 'network')));
        return;
      }

      const tlsSocket = tls.connect({ socket, servername: target.hostname });
      const chunks: Buffer[] = [];

      tlsSocket.once('secureConnect', () => {
        const requestHeaders = {
          ...headers,
          Host: target.host,
          Connection: 'close',
        };
        const headerLines = Object.entries(requestHeaders).map(([key, value]) => `${key}: ${value}`);
        const requestPath = `${target.pathname}${target.search}`;
        tlsSocket.write(`GET ${requestPath} HTTP/1.1\r\n${headerLines.join('\r\n')}\r\n\r\n`);
      });
      tlsSocket.on('data', (chunk) => chunks.push(Buffer.from(chunk)));
      tlsSocket.once('error', (err) => {
        done(() => reject(new ProviderError(`网络错误: ${err.message}`, 'network')));
      });
      tlsSocket.once('end', () => {
        try {
          const raw = Buffer.concat(chunks).toString('utf-8');
          const splitAt = raw.indexOf('\r\n\r\n');
          if (splitAt < 0) throw new Error('响应头解析失败');
          const head = raw.slice(0, splitAt);
          let body = raw.slice(splitAt + 4);
          const [statusLine, ...headerLines] = head.split('\r\n');
          if (!statusLine) throw new Error('状态行缺失');
          const status = Number(statusLine.split(' ')[1]);
          if (!Number.isFinite(status)) throw new Error(`状态行解析失败: ${statusLine}`);
          const responseHeaders = new Map<string, string>();
          for (const line of headerLines) {
            const idx = line.indexOf(':');
            if (idx > 0) responseHeaders.set(line.slice(0, idx).toLowerCase(), line.slice(idx + 1).trim());
          }
          if (responseHeaders.get('transfer-encoding')?.toLowerCase().includes('chunked')) {
            body = decodeChunkedBody(body);
          }
          done(() => resolve({ status, body, retryAfterMs: parseRetryAfter(responseHeaders.get('retry-after')) }));
        } catch (err) {
          done(() => reject(new ProviderError(`网络错误: ${(err as Error).message}`, 'network')));
        }
      });
    });
    connectReq.once('error', (err) => {
      done(() => reject(new ProviderError(`网络错误: ${err.message}`, 'network')));
    });
    connectReq.end();
  });
}

function decodeChunkedBody(body: string): string {
  let pos = 0;
  let out = '';
  for (;;) {
    const lineEnd = body.indexOf('\r\n', pos);
    if (lineEnd < 0) throw new Error('chunk size line missing');
    const sizeText = body.slice(pos, lineEnd).split(';')[0] ?? '';
    const size = Number.parseInt(sizeText, 16);
    if (!Number.isFinite(size)) throw new Error(`invalid chunk size: ${sizeText}`);
    pos = lineEnd + 2;
    if (size === 0) return out;
    out += body.slice(pos, pos + size);
    pos += size + 2;
  }
}

export function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
