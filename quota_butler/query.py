"""Send the same V3 status card used by text and menu entrypoints."""

from __future__ import annotations

import argparse

from . import config as config_mod
from .agent_status import detect_agents
from .notify import NotifyError, push_status_card

DEFAULT_CONFIG = "~/.quota-butler/config.yaml"


def run(config_path: str, *, dry_run: bool = False) -> int:
    cfg = config_mod.load(config_path)
    try:
        push_status_card(detect_agents(), cfg, dry_run=dry_run)
    except NotifyError as exc:
        print(f"[查询] 发卡失败：{exc}")
        return 3
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="quota-butler-query")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return run(args.config, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
