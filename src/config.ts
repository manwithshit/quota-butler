// 应用凭据持久化（~/.quota-butler/config.json）。凭据来自扫码向导。

import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';

export const configPath = join(homedir(), '.quota-butler', 'config.json');

export interface AppConfig {
  app: { id: string; secret: string; tenant?: 'feishu' | 'lark' };
}

export function loadConfig(path: string = configPath): AppConfig | null {
  try {
    const raw = JSON.parse(readFileSync(path, 'utf-8')) as AppConfig;
    if (raw?.app?.id && raw?.app?.secret) return raw;
    return null;
  } catch {
    return null;
  }
}

export function saveConfig(cfg: AppConfig, path: string = configPath): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(cfg, null, 2), 'utf-8');
}
