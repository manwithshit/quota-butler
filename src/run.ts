// 前台运行：扫码（首次）→ 连接 → 监听飞书消息/卡片回调 → 走 handler。

import type { LarkChannel, CardActionEvent, NormalizedMessage } from '@larksuite/channel';
import { loadConfig, saveConfig } from './config.js';
import { runRegistrationWizard } from './wizard.js';
import { connectChannel } from './channel.js';
import { handleAction, type HandlerCtx } from './handler.js';
import type { Card } from './notify.js';
import { Poller } from './poller.js';
import { WarmupScheduler } from './scheduler.js';
import { StateStore } from './state.js';
import { acquireRunLock } from './run_lock.js';

export async function run(): Promise<void> {
  const lock = acquireRunLock();
  try {
    let cfg = loadConfig();
    if (!cfg) {
      cfg = await runRegistrationWizard();
      saveConfig(cfg);
    }

    const state = new StateStore();
    const { channel, ownerId } = await connectChannel(cfg);
    const scheduler = new WarmupScheduler(channel, ownerId, state);
    scheduler.rearmFromState(); // 重启后按 active plan 重新布预热点
    const poller = new Poller(channel, ownerId, state);
    poller.start(); // 15min 轮询恢复提醒 + 22:00 睡前

    console.log(`[quota-butler] 单实例锁已获取：${lock.path}`);
    console.log(`[quota-butler] 已连接飞书。owner=${ownerId ?? '(未解析到，先不做 owner 过滤)'}`);
    console.log('[quota-butler] 私聊发"额度"查额度、"菜单"看功能、"明日计划"做计划。Ctrl-C 退出。');

    channel.on('message', async (m: NormalizedMessage) => {
      if (ownerId && m.senderId !== ownerId) return;
      const action = textToAction(m.content || '');
      if (!action) return;
      await safe(() => handleAction(action, makeCtx(channel, m.chatId, state, scheduler, m.senderId)));
    });

    channel.on('cardAction', (evt: CardActionEvent) => {
      if (ownerId && evt.operator.openId !== ownerId) return;
      const payload: Record<string, unknown> = {
        ...((evt.action.value as Record<string, unknown>) ?? {}),
        form_value: evt.action.formValue,
      };
      // 立即返回让 SDK 快速 ack 回调（否则飞书弹"回调未响应"），实际处理异步进行。
      void safe(() => handleAction(payload, makeCtx(channel, evt.chatId, state, scheduler, evt.operator.openId)));
    });

    channel.on('error', (err) => console.error('[quota-butler] channel error:', err));

    await new Promise<never>(() => {}); // 常驻
  } finally {
    lock.release();
  }
}

function makeCtx(
  channel: LarkChannel,
  chatId: string,
  state: StateStore,
  scheduler: WarmupScheduler,
  userId?: string,
): HandlerCtx {
  return {
    state,
    scheduler,
    chatId,
    userId,
    send: async (card: Card) => {
      await channel.send(chatId, { card });
    },
    receipt: async (text: string) => {
      await channel.send(chatId, { text });
    },
  };
}

function textToAction(text: string): Record<string, unknown> | null {
  const t = text.toLowerCase();
  // 调试：白天即时验证"定时器→预热→飞书回执"整条链路。
  if (t.includes('测试预热') || t.includes('测预热')) {
    return { action: 'debug_test_warmup', provider: t.includes('codex') ? 'codex' : 'cc' };
  }
  if (t.includes('手动预热') || t.includes('手动开窗')) return { action: 'manual_warmup' };
  if (t.includes('日报') || t.includes('小结')) return { action: 'daily_report' };
  if (t.includes('额度') || t.includes('quota')) return { action: 'query_status' };
  if (t.includes('菜单') || t.includes('menu')) return { action: 'menu' };
  // 先判"查看/当前计划"，再判"计划"——否则"查看计划"会被当成"明日计划"。
  if (t.includes('查看') || t.includes('当前计划')) return { action: 'view_schedule' };
  if (t.includes('明日') || t.includes('明天') || t.includes('计划')) {
    return { action: 'schedule_intent', intent: 'tomorrow' };
  }
  return { action: 'menu' }; // 其它任何消息都回菜单，方便发现功能
}

async function safe(fn: () => Promise<void>): Promise<void> {
  try {
    await fn();
  } catch (e) {
    console.error('[quota-butler] 处理失败：', e);
  }
}
