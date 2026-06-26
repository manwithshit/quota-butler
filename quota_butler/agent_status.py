"""Detect installed, authenticated, and schedulable AI agents."""

from __future__ import annotations

import os
import json
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Sequence

from .providers import get_provider
from .providers.base import ProviderError, Usage


class AgentState(str, Enum):
    CONNECTED = "connected"
    NEEDS_LOGIN = "needs_login"
    TOKEN_STALE = "token_stale"  # 登录有效但缓存令牌过期（可刷新，非登出）
    UNAVAILABLE = "unavailable"
    NOT_INSTALLED = "not_installed"


@dataclass(frozen=True)
class AgentStatus:
    provider: str
    state: AgentState
    executable: Optional[str] = None
    usage: Optional[Usage] = None
    detail: str = ""

    @property
    def queryable(self) -> bool:
        return self.state == AgentState.CONNECTED and self.usage is not None

    @property
    def plan_eligible(self) -> bool:
        if not self.queryable:
            return False
        window = self.usage.five_hour
        has_five_hour = window.kind == "five_hour" or window.window_seconds == 5 * 3600
        if not has_five_hour or window.utilization >= 100:
            return False
        weekly = self.usage.seven_day
        if weekly and (
            weekly.kind == "weekly" or weekly.window_seconds == 7 * 24 * 3600
        ):
            return weekly.utilization < 100
        return True

    @property
    def schedulable(self) -> bool:
        if not self.queryable:
            return False
        window = self.usage.five_hour
        has_five_hour = (
            window.kind == "five_hour" or window.window_seconds == 5 * 3600
        )
        if not has_five_hour:
            return False
        weekly = self.usage.seven_day
        if weekly and (
            weekly.kind == "weekly" or weekly.window_seconds == 7 * 24 * 3600
        ):
            return weekly.utilization < 100
        return True


def find_agent_executable(provider: str) -> Optional[str]:
    binary = "claude" if provider == "cc" else "codex"
    found = shutil.which(binary)
    if found:
        return found
    for directory in (
        "/usr/local/bin",
        "/opt/homebrew/bin",
        os.path.expanduser("~/.local/bin"),
        os.path.expanduser("~/.npm-global/bin"),
    ):
        candidate = os.path.join(directory, binary)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def detect_agents(
    providers: Sequence[str] = ("cc", "codex"),
) -> Dict[str, AgentStatus]:
    results: Dict[str, AgentStatus] = {}
    for provider in providers:
        executable = find_agent_executable(provider)
        if not executable:
            results[provider] = AgentStatus(
                provider,
                AgentState.NOT_INSTALLED,
                detail="本机未检测到 CLI",
            )
            continue
        login_probe = probe_agent_login(provider, executable)
        if login_probe is not None:
            state, detail = login_probe
            results[provider] = AgentStatus(
                provider,
                state,
                executable=executable,
                detail=detail,
            )
            continue
        try:
            usage = get_provider(provider).read_usage()
        except (ProviderError, NotImplementedError) as exc:
            detail = str(exc)
            if _looks_like_auth_error(detail):
                # cc 已先过 login probe（loggedIn:true 才会走到这里），
                # 此处 auth 类失败 = 缓存令牌过期可刷新，不是登出。
                state = (
                    AgentState.TOKEN_STALE
                    if provider == "cc"
                    else AgentState.NEEDS_LOGIN
                )
            else:
                state = AgentState.UNAVAILABLE
            results[provider] = AgentStatus(
                provider,
                state,
                executable=executable,
                detail=detail,
            )
            continue
        results[provider] = AgentStatus(
            provider,
            AgentState.CONNECTED,
            executable=executable,
            usage=usage,
        )
    return results


def probe_agent_login(provider: str, executable: str):
    """Return a classified Claude login problem, or None when usage may be read."""
    if provider != "cc":
        return None
    try:
        result = subprocess.run(
            [executable, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return AgentState.UNAVAILABLE, f"Claude 登录状态读取失败: {exc}"
    output = (result.stdout or result.stderr or "").strip()
    try:
        data = json.loads(output) if output else {}
    except json.JSONDecodeError:
        data = {}
    if data.get("loggedIn") is False:
        return AgentState.NEEDS_LOGIN, "Claude Code 未登录，请运行 claude auth login"
    if result.returncode != 0:
        lowered = output.lower()
        if any(word in lowered for word in ("login", "logged", "auth", "token")):
            return AgentState.NEEDS_LOGIN, output[:200] or "Claude Code 未登录"
        return AgentState.UNAVAILABLE, output[:200] or "Claude 登录状态暂时无法读取"
    return None


def _looks_like_auth_error(detail: str) -> bool:
    lowered = detail.lower()
    return any(
        marker in lowered
        for marker in (
            "401",
            "token 失效",
            "token 已过期",
            "expired",
            "auth.json",
            "没有 'claude code-credentials'",
            "请先用 codex cli 登录",
            "缺 access_token",
        )
    )
