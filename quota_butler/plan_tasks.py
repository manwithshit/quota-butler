"""Persist adopted plans and install their future warmups as launchd jobs."""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from .config import Config
from .planner import SUPPORTED_AGENTS, SchedulePlan


class PlanTaskError(Exception):
    pass


def plan_record(plan: SchedulePlan) -> Dict[str, Any]:
    core = {
        "plan_version": plan.plan_version,
        "agents": list(plan.agents),
        "work_start": plan.work_start.isoformat(),
        "work_end": plan.work_end.isoformat(),
        "reason": plan.reason,
        "events": [
            {
                "agent": event.agent,
                "kind": event.kind,
                "at": event.at.isoformat(),
                "purpose": event.purpose,
            }
            for event in plan.events
        ],
        "request": {
            "target_date": plan.request.target_date.isoformat(),
            "time_mode": plan.request.time_mode,
            "work_start": plan.request.work_start,
            "work_end": plan.request.work_end,
            "agent_strategy": plan.request.agent_strategy,
            "first_warmup": plan.request.first_warmup,
            "second_warmup": plan.request.second_warmup,
        },
    }
    digest = hashlib.sha256(
        json.dumps(core, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()[:16]
    return {"plan_id": digest, "status": "proposed", **core}


def validate_plan_record(value: object) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise PlanTaskError("计划 payload 缺失")
    record = dict(value)
    if record.get("plan_version") != 3:
        raise PlanTaskError("该计划已失效，请重新规划")
    plan_id = str(record.get("plan_id") or "").strip()
    if not plan_id or not plan_id.replace("-", "").isalnum():
        raise PlanTaskError("plan_id 非法")
    events = record.get("events")
    if not isinstance(events, list):
        raise PlanTaskError("计划 events 缺失")
    work_start = _parse_datetime(record.get("work_start"))
    work_end = _parse_datetime(record.get("work_end"))
    if work_end <= work_start:
        raise PlanTaskError("计划工作结束时间必须晚于开始时间")
    if len(events) > 50:
        raise PlanTaskError("计划事件过多")
    for event in events:
        if not isinstance(event, dict):
            raise PlanTaskError("计划事件格式非法")
        if event.get("agent") not in SUPPORTED_AGENTS:
            raise PlanTaskError(f"计划包含不支持的 Agent: {event.get('agent')!r}")
        if event.get("kind") != "warmup":
            raise PlanTaskError(f"计划事件类型非法: {event.get('kind')!r}")
        _parse_datetime(event.get("at"))
    return record


def install_plan_tasks(
    record: Dict[str, Any],
    config: Config,
    *,
    now: Optional[datetime] = None,
    config_path: str = "~/.quota-butler/config.yaml",
) -> List[Dict[str, str]]:
    record = validate_plan_record(record)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    task_dir = os.path.expanduser(config.plan_tasks_dir)
    os.makedirs(task_dir, exist_ok=True)

    installed: List[Dict[str, str]] = []
    try:
        work_end = _parse_datetime(record["work_end"])
        future_warmups = [
            event for event in record["events"]
            if event["kind"] == "warmup"
            and now < _parse_datetime(event["at"]) <= work_end
        ]
        for index, event in enumerate(future_warmups):
            item = _install_event(
                record["plan_id"], index, event, task_dir, config_path
            )
            installed.append(item)
    except Exception:
        cancel_plan_tasks(installed)
        raise
    return installed


def cancel_plan_tasks(tasks: Sequence[Dict[str, str]]) -> None:
    for task in tasks:
        label = str(task.get("label") or "")
        path = os.path.expanduser(str(task.get("plist_path") or ""))
        if label:
            try:
                subprocess.run(
                    ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        if path and os.path.exists(path):
            os.unlink(path)


def _install_event(
    plan_id: str,
    index: int,
    event: Dict[str, Any],
    task_dir: str,
    config_path: str,
) -> Dict[str, str]:
    at = _parse_datetime(event["at"]).astimezone()
    label = f"com.quota-butler.plan.{plan_id}.{index}"
    path = os.path.join(task_dir, f"{label}.plist")
    payload = {
        "action": "scheduled_warmup",
        "plan_id": plan_id,
        "provider": event["agent"],
        "scheduled_for": event["at"],
    }
    plist = {
        "Label": label,
        "ProgramArguments": [
            sys.executable,
            "-m",
            "quota_butler.handler",
            "--config",
            os.path.abspath(os.path.expanduser(config_path)),
            json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        ],
        "StartCalendarInterval": {
            "Month": at.month,
            "Day": at.day,
            "Hour": at.hour,
            "Minute": at.minute,
        },
        "WorkingDirectory": _repo_root(),
        "EnvironmentVariables": _task_environment(config_path),
        "StandardOutPath": os.path.join(task_dir, f"{label}.log"),
        "StandardErrorPath": os.path.join(task_dir, f"{label}.err.log"),
    }
    with open(path, "wb") as f:
        plistlib.dump(plist, f, sort_keys=True)
    try:
        result = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{os.getuid()}", path],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if os.path.exists(path):
            os.unlink(path)
        raise PlanTaskError(f"调用 launchctl 失败: {exc}") from exc
    if result.returncode != 0:
        if os.path.exists(path):
            os.unlink(path)
        raise PlanTaskError(
            f"launchctl bootstrap 失败: {(result.stderr or result.stdout).strip()[:200]}"
        )
    return {
        "label": label,
        "plist_path": path,
        "scheduled_for": event["at"],
        "provider": event["agent"],
        "status": "pending",
    }


def _parse_datetime(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise PlanTaskError(f"非法计划时间: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_local_timezone())
    return parsed


def _local_timezone():
    return datetime.now().astimezone().tzinfo or timezone.utc


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _task_environment(config_path: str) -> Dict[str, str]:
    return {
        "PATH": _launchd_path(),
        "LARK_CHANNEL": os.environ.get("LARK_CHANNEL", "1"),
        "LARKSUITE_CLI_CONFIG_DIR": os.path.expanduser(
            os.environ.get(
                "LARKSUITE_CLI_CONFIG_DIR",
                "~/.lark-channel/profiles/codex/lark-cli",
            )
        ),
        "QUOTA_BUTLER_ROOT": _repo_root(),
        "QUOTA_BUTLER_PYTHON": sys.executable,
        "QUOTA_BUTLER_CONFIG": os.path.abspath(os.path.expanduser(config_path)),
    }


def _launchd_path() -> str:
    directories = [
        os.path.dirname(sys.executable),
        *(os.path.dirname(path) for path in _which_all("lark-cli", "claude", "codex")),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ]
    current = os.environ.get("PATH", "")
    directories.extend(path for path in current.split(os.pathsep) if path)
    seen = set()
    unique = []
    for directory in directories:
        if directory and directory not in seen:
            seen.add(directory)
            unique.append(directory)
    return os.pathsep.join(unique)


def _which_all(*commands: str) -> List[str]:
    paths = []
    for command in commands:
        found = shutil.which(command)
        if found:
            paths.append(found)
    return paths
