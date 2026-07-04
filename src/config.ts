// 应用凭据持久化（~/.quota-butler/config.json）。凭据来自扫码向导。

import { readFileSync, writeFileSync, mkdirSync, chmodSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';

export const configPath = join(homedir(), '.quota-butler', 'config.json');

export interface AppConfig {
  app: { id: string; secret: string; tenant?: 'feishu' | 'lark' };
}

export function loadConfig(path: string = configPath): AppConfig | null {
  try {
    const raw = JSON.parse(readFileSync(path, 'utf-8')) as AppConfig;
    if (raw?.app?.id && raw?.app?.secret) {
      tightenMode(path); // 存量安装可能是 0644，读到时顺手收紧
      return raw;
    }
    return null;
  } catch {
    return null;
  }
}

export function saveConfig(cfg: AppConfig, path: string = configPath): void {
  mkdirSync(dirname(path), { recursive: true });
  // 含飞书 app secret：新建 0600；文件已存在时 writeFileSync 的 mode 不生效，再 chmod 一次。
  writeFileSync(path, JSON.stringify(cfg, null, 2), { encoding: 'utf-8', mode: 0o600 });
  tightenMode(path);
}

function tightenMode(path: string): void {
  try {
    chmodSync(path, 0o600);
  } catch {
    // 权限收紧失败不阻塞主流程
  }
}
