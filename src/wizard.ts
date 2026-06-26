// 扫码创建飞书应用向导。基于 @larksuite/channel 的 registerApp（同 bridge 做法）。

import { registerApp } from '@larksuite/channel';
import qrcode from 'qrcode-terminal';
import type { AppConfig } from './config.js';

export async function runRegistrationWizard(): Promise<AppConfig> {
  console.log('\n未检测到飞书应用配置，进入扫码创建向导。\n');

  const result = await registerApp({
    source: 'quota-butler',
    onQRCodeReady: (info: { url: string; expireIn: number }) => {
      console.log('请用飞书 App 扫描以下二维码完成应用创建：\n');
      qrcode.generate(info.url, { small: true });
      const mins = Math.max(1, Math.round(info.expireIn / 60));
      console.log(`\n二维码有效期：约 ${mins} 分钟`);
      console.log(`也可以直接在浏览器打开：${info.url}\n`);
    },
  });

  const tenant = (result.user_info?.tenant_brand ?? 'feishu') as 'feishu' | 'lark';
  console.log('\n✓ 应用创建成功');
  console.log(`  App ID: ${result.client_id}`);
  console.log(`  Tenant: ${tenant}`);
  console.log('  扫码的你即应用 owner，额度管家只会私聊/响应你。\n');

  return { app: { id: result.client_id, secret: result.client_secret, tenant } };
}
