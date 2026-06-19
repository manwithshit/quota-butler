"""CLI entrypoint that opens the V3 tomorrow-plan time card."""

from __future__ import annotations

import argparse

from . import handler

DEFAULT_CONFIG = "~/.quota-butler/config.yaml"


def run(config_path: str, *, intent: str = "tomorrow", dry_run: bool = False) -> int:
    return handler.handle(
        {"action": "schedule_intent", "intent": intent},
        config_path=config_path,
        dry_run=dry_run,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="quota-butler-schedule")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--intent", default="tomorrow")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return run(args.config, intent=args.intent, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
