import { EventEmitter } from 'node:events';
import { afterEach, describe, expect, it, vi } from 'vitest';

const { requestMock } = vi.hoisted(() => ({ requestMock: vi.fn() }));

vi.mock('node:https', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:https')>();
  return { ...actual, default: { ...actual, request: requestMock } };
});

import { shouldRestartWithLaunchd } from '../src/channel.js';
import { daemonEnvironment } from '../src/daemon.js';
import { httpGetJson } from '../src/providers/base.js';

const ORIGINAL_ENV = { ...process.env };

afterEach(() => {
  process.env = { ...ORIGINAL_ENV };
  requestMock.mockReset();
});

describe('后台网络配置', () => {
  it('未配置代理时保持直连，只标记 launchd 守护身份', () => {
    expect(daemonEnvironment('/usr/bin', {})).toEqual({
      PATH: '/usr/bin',
      QUOTA_BUTLER_DAEMON: '1',
    });
  });

  it('显式代理会传给飞书和 provider，并保留 no_proxy', () => {
    expect(
      daemonEnvironment('/usr/bin', {
        QUOTA_BUTLER_PROXY: 'http://127.0.0.1:7890',
        NO_PROXY: 'localhost,internal.example.com',
      }),
    ).toMatchObject({
      HTTP_PROXY: 'http://127.0.0.1:7890',
      HTTPS_PROXY: 'http://127.0.0.1:7890',
      http_proxy: 'http://127.0.0.1:7890',
      https_proxy: 'http://127.0.0.1:7890',
      NO_PROXY: 'localhost,internal.example.com',
      no_proxy: 'localhost,internal.example.com',
    });
  });

  it('只有 launchd 守护进程会在连接不可恢复时退出重启', () => {
    expect(shouldRestartWithLaunchd({})).toBe(false);
    expect(shouldRestartWithLaunchd({ QUOTA_BUTLER_DAEMON: '1' })).toBe(true);
  });

  it('代理响应由 Node HTTP 栈组装，UTF-8 跨数据块不会损坏', async () => {
    process.env = { ...ORIGINAL_ENV, QUOTA_BUTLER_PROXY: 'http://127.0.0.1:7890', NO_PROXY: '' };
    requestMock.mockImplementation((_url, _options, onResponse) => {
      const req = new EventEmitter() as EventEmitter & { end: () => void; destroy: (err: Error) => void };
      req.end = () => {
        const res = new EventEmitter() as EventEmitter & {
          statusCode: number;
          headers: Record<string, string>;
        };
        res.statusCode = 200;
        res.headers = { 'retry-after': '2' };
        onResponse(res);
        const bytes = Buffer.from('{"message":"你好"}', 'utf-8');
        res.emit('data', bytes.subarray(0, bytes.length - 2));
        res.emit('data', bytes.subarray(bytes.length - 2));
        res.emit('end');
      };
      req.destroy = (err) => req.emit('error', err);
      return req;
    });

    const result = await httpGetJson('https://example.com/usage', { Authorization: 'Bearer test' }, 1000);

    expect(result).toEqual({ status: 200, body: '{"message":"你好"}', retryAfterMs: 2000 });
    expect(requestMock).toHaveBeenCalledOnce();
  });

  it('不接受 HTTP CONNECT 无法处理的代理协议', async () => {
    process.env = { ...ORIGINAL_ENV, QUOTA_BUTLER_PROXY: 'socks5://127.0.0.1:1080', NO_PROXY: '' };

    await expect(httpGetJson('https://example.com/usage', {}, 1000)).rejects.toMatchObject({
      kind: 'network',
      message: expect.stringContaining('不支持的代理协议'),
    });
    expect(requestMock).not.toHaveBeenCalled();
  });
});
