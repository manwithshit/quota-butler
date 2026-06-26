// macOS launchd 守护：start / stop / status。只守护主进程（run），不做 per-task。

import { writeFileSync, mkdirSync, existsSync, unlinkSync } from 'node:fs';
import { homedir } from 'node:os';
import { join, dirname } from 'node:path';
import { execFileSync } from 'node:child_process';
import { loadConfig } from './config.js';

const LABEL = 'com.quota-butler';
const PLIST = join(homedir(), 'Library', 'LaunchAgents', `${LABEL}.plist`);
const LOG_DIR = join(homedir(), '.quota-butler', 'logs');

function uid(): number {
  return process.getuid?.() ?? 0;
}

export function installDaemon(): void {
  if (process.platform !== 'darwin') {
    console.error('后台守护目前仅支持 macOS。其他平台请用 `quota-butler run` 前台运行。');
    process.exit(1);
  }
  if (!loadConfig()) {
    console.error('还没扫码登录。先运行 `quota-butler run` 扫码创建应用、连通后 Ctrl-C，再执行 `quota-butler start`。');
    process.exit(1);
  }
  mkdirSync(LOG_DIR, { recursive: true });
  const node = process.execPath;
  const cli = process.argv[1] ?? ''; // 正在运行的 cli.mjs 绝对路径
  const path = [
    dirname(node),
    '/usr/bin',
    '/usr/local/bin',
    '/bin',
    '/opt/homebrew/bin',
    join(homedir(), '.local', 'bin'),
    join(homedir(), '.npm-global', 'bin'),
  ].join(':');

  const plist = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${node}</string>
    <string>${cli}</string>
    <string>run</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>${path}</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>${join(LOG_DIR, 'daemon.log')}</string>
  <key>StandardErrorPath</key><string>${join(LOG_DIR, 'daemon.err.log')}</string>
</dict>
</plist>
`;
  writeFileSync(PLIST, plist, 'utf-8');
  try {
    execFileSync('launchctl', ['bootout', `gui/${uid()}/${LABEL}`], { stdio: 'ignore' });
  } catch {
    // 之前没装，忽略
  }
  execFileSync('launchctl', ['bootstrap', `gui/${uid()}`, PLIST], { stdio: 'inherit' });
  console.log(`✅ 额度管家已在后台常驻（${LABEL}）。日志：${LOG_DIR}/daemon.log`);
}

export function uninstallDaemon(): void {
  try {
    execFileSync('launchctl', ['bootout', `gui/${uid()}/${LABEL}`], { stdio: 'ignore' });
  } catch {
    // 没在跑，忽略
  }
  if (existsSync(PLIST)) unlinkSync(PLIST);
  console.log('✅ 已停止后台守护。');
}

export function daemonStatus(): void {
  try {
    const out = execFileSync('launchctl', ['print', `gui/${uid()}/${LABEL}`], {
      encoding: 'utf-8',
      stdio: ['ignore', 'pipe', 'ignore'],
    });
    const m = /pid = (\d+)/.exec(out);
    console.log(m ? `🟢 守护运行中（PID ${m[1]}）。` : '🟡 已加载但未运行（查看日志排查）。');
  } catch {
    console.log('⚪ 守护未安装。用 `quota-butler start` 启动。');
  }
}
