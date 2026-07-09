// 连接飞书：createLarkChannel + connect + 解析 owner。

import { createLarkChannel, type LarkChannel } from '@larksuite/channel';
import type { AppConfig } from './config.js';

export interface Connected {
  channel: LarkChannel;
  ownerId: string | undefined;
  botOpenId: string | undefined;
}

export async function connectChannel(cfg: AppConfig): Promise<Connected> {
  let exitingForReconnect = false;
  const channel = createLarkChannel({
    appId: cfg.app.id,
    appSecret: cfg.app.secret,
    source: 'quota-butler',
    respectProxyEnv: true,
    keepalive: {
      enabled: true,
      onUnrecoverable: (err) => {
        if (exitingForReconnect) return;
        exitingForReconnect = true;
        console.error('[quota-butler] channel unrecoverable; exiting for launchd restart:', err);
        setTimeout(() => process.exit(75), 0);
      },
    },
  });
  await channel.connect();
  let ownerId: string | undefined;
  try {
    ownerId = (await channel.getAppInfo({ userIdType: 'open_id' })).ownerId;
  } catch {
    ownerId = undefined;
  }
  return { channel, ownerId, botOpenId: channel.botIdentity?.openId };
}
