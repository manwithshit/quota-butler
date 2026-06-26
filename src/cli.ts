import { Command } from 'commander';

const program = new Command();

program
  .name('quota-butler')
  .description('飞书额度管家：看得见 Claude Code / Codex 额度，恢复时提醒，明日重度使用前自动预热。')
  .version('0.1.0');

program
  .command('run')
  .description('前台运行：首次进入扫码向导，连上后常驻监听飞书')
  .action(async () => {
    const { run } = await import('./run.js');
    await run();
  });

program
  .command('selftest')
  .description('离线自检：检测本机 Agent 并在终端预览额度卡（不连飞书）')
  .action(async () => {
    const { selftest } = await import('./selftest.js');
    await selftest();
  });

program
  .command('report')
  .description('预览晚间日报卡（日报 + 明日计划）：检测本机 Agent 并在终端打印，不连飞书')
  .action(async () => {
    const { report } = await import('./report.js');
    await report();
  });

program
  .command('start')
  .description('后台常驻（macOS launchd 守护）')
  .action(async () => {
    const { installDaemon } = await import('./daemon.js');
    installDaemon();
  });

program
  .command('stop')
  .description('停止后台守护')
  .action(async () => {
    const { uninstallDaemon } = await import('./daemon.js');
    uninstallDaemon();
  });

program
  .command('status')
  .description('查看后台守护状态')
  .action(async () => {
    const { daemonStatus } = await import('./daemon.js');
    daemonStatus();
  });

program.parseAsync(process.argv);
