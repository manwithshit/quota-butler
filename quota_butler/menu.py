"""Send a Quota Butler command menu card to Feishu."""

from __future__ import annotations

import argparse

from . import config as config_mod
from .notify import NotifyError, push_command_menu_card

DEFAULT_CONFIG = "~/.quota-butler/config.yaml"


def run(config_path: str, *, dry_run: bool = False) -> int:
    cfg = config_mod.load(config_path)
    try:
        push_command_menu_card(cfg, dry_run=dry_run)
    except NotifyError as e:
        print(f"[菜单] 发卡失败：{e}")
        return 3
    print("[菜单] 已发送测试菜单卡")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="quota-butler-menu")
    p.add_argument("--config", default=DEFAULT_CONFIG)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    return run(args.config, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
