"""群聊主动查询：读 CC + Codex 当前额度 → 回一张状态卡。

## 它在产品里的位置

quota-butler 原本只会「到点主动喊你」（main.py）。本模块补上另一条腿：
**你在飞书群里主动问一句，它即时回当前额度**——把单向推送变成可问可答。

## 集成契约（给 bridge / 承接 agent）

群里收到触发词消息（状态 / 额度 / quota，大小写不敏感）时，调：

    python3 -m quota_butler.query

即往配置的飞书目标回一张当前额度卡。被调起才回，不常驻。触发词→命令的路由
在 bridge / 承接侧配置（和 handler 的 [card-click] 契约同理）。

## 用法

    python3 -m quota_butler.query                正常：读额度 → 发卡
    python3 -m quota_butler.query --dry-run      只打印卡片，不真发飞书
    python3 -m quota_butler.query --config PATH
"""

from __future__ import annotations

import argparse

from . import config as config_mod
from .notify import push_status_card, NotifyError
from .providers import get_provider
from .providers.base import ProviderError

DEFAULT_CONFIG = "~/.quota-butler/config.yaml"
QUERY_PROVIDERS = ("cc", "codex")  # 查询时尽量都读；读不到的在卡片里标原因


def run(config_path: str, dry_run: bool = False) -> int:
    cfg = config_mod.load(config_path)
    results = []
    for name in QUERY_PROVIDERS:
        try:
            usage = get_provider(name).read_usage()
            results.append((name, usage, None))
            print(f"[查询] {name} 已读到：{usage.five_hour.utilization:.0f}%")
        except (ProviderError, NotImplementedError) as e:
            results.append((name, None, str(e)))
            print(f"[查询] {name} 读取失败：{e}")

    try:
        push_status_card(results, cfg, dry_run=dry_run)
    except NotifyError as e:
        print(f"[查询] 发卡失败：{e}")
        return 3
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="quota-butler-query")
    p.add_argument("--config", default=DEFAULT_CONFIG)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    return run(args.config, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
