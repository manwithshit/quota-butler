"""Codex provider —— 感知（read_usage）。

读 ~/.codex/auth.json 的 access_token + account_id → 打 wham/usage → 解析。
窗口结构（本机实测 dump）：
  - 免费档：primary_window 是「月度」额度（limit_window_seconds≈2592000），secondary_window 为 null；
  - 付费档：primary_window 是 5h、secondary_window 是 7天。
统一映射：primary → five_hour 槽位，secondary → seven_day 槽位，真实窗口长度写进 window_seconds。

预热未实现：免费档无 5h 窗口可换挡，且 token 刷新走 `codex exec` 会烧月度额度，留 BACKLOG。
安全红线：token 只留内存，不打印、不写盘、不外传。依赖纯 stdlib。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .base import Provider, ProviderError, Usage, WindowUsage

AUTH_PATH = "~/.codex/auth.json"
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
USER_AGENT = "quota-butler/0.1"
HTTP_TIMEOUT = 15


class CodexProvider(Provider):
    name = "codex"

    # ---- 感知 -----------------------------------------------------------

    def read_usage(self) -> Usage:
        token, account_id = self._read_auth()
        refreshed = False
        server_retries = 1
        while True:
            try:
                raw = self._fetch_usage(token, account_id)
                break
            except ProviderError as e:
                cause = e.__cause__
                code = cause.code if isinstance(cause, urllib.error.HTTPError) else None
                if code == 401 and not refreshed:
                    refreshed = True
                    print("[Codex] Token expired (401). Attempting to auto-refresh token...")
                    self._refresh_token()
                    token, account_id = self._read_auth()
                    continue
                if code is not None and 500 <= code < 600 and server_retries > 0:
                    server_retries -= 1
                    time.sleep(0.5)
                    continue
                raise
        return self._parse(raw)

    def _refresh_token(self) -> None:
        """调用 `codex exec "ping"` 强制 codex CLI 刷新 ~/.codex/auth.json 中的 Token。"""
        try:
            out = subprocess.run(
                ["codex", "exec", "ping"],
                capture_output=True,
                text=True,
                timeout=45,
            )
        except subprocess.TimeoutExpired as e:
            raise ProviderError("刷新 Codex Token 超时（codex exec 45s 未返回）") from e
        except (subprocess.SubprocessError, OSError) as e:
            raise ProviderError(f"调用 codex 刷新 Token 失败: {e}") from e
        if out.returncode != 0:
            raise ProviderError(
                f"调用 codex 刷新 Token 失败，退出码 {out.returncode}: {out.stderr.strip()[:200]}"
            )
        print("[Codex] Token refreshed successfully.")

    def _read_auth(self):
        """读 auth.json 拿 token + account_id。token 只在返回值里流转，不落盘。"""
        path = os.path.expanduser(AUTH_PATH)
        if not os.path.exists(path):
            raise ProviderError(
                f"{AUTH_PATH} 不存在；请先用 codex CLI 登录（codex login）。"
            )
        try:
            with open(path, "r", encoding="utf-8") as f:
                tokens = json.load(f)["tokens"]
            token = tokens["access_token"]
            account_id = tokens["account_id"]
        except (json.JSONDecodeError, OSError, KeyError, TypeError) as e:
            raise ProviderError(f"Codex auth.json 结构异常: {e}") from e
        if not token or not account_id:
            raise ProviderError("Codex auth.json 缺 access_token / account_id")
        return token, account_id

    def _fetch_usage(self, token: str, account_id: str) -> Dict[str, Any]:
        req = urllib.request.Request(
            USAGE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "chatgpt-account-id": account_id,
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise ProviderError(
                    "wham/usage 返回 401：Codex token 失效，"
                    "用一次 codex CLI 让它刷新后重试。"
                ) from e
            raise ProviderError(f"wham/usage HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise ProviderError(f"wham/usage 网络错误: {e.reason}") from e
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise ProviderError(f"wham/usage 返回非 JSON: {e}") from e

    def _parse(self, raw: Dict[str, Any]) -> Usage:
        rl = raw.get("rate_limit") or {}
        primary = self._window(rl.get("primary_window"))
        if primary is None:
            raise ProviderError("wham/usage 缺 rate_limit.primary_window")
        secondary = self._window(rl.get("secondary_window"))
        return Usage(provider=self.name, five_hour=primary, seven_day=secondary)

    @staticmethod
    def _window(node: Optional[Dict[str, Any]]) -> Optional[WindowUsage]:
        if not node:
            return None
        try:
            win = node.get("limit_window_seconds")
            window_seconds = int(win) if win is not None else None
            reset_at = node.get("reset_at")
            return WindowUsage(
                utilization=float(node["used_percent"]),
                resets_at=(
                    datetime.fromtimestamp(int(reset_at), tz=timezone.utc)
                    if reset_at is not None
                    else None
                ),
                window_seconds=window_seconds,
                kind=_window_kind(window_seconds),
            )
        except (KeyError, ValueError, TypeError) as e:
            raise ProviderError(f"Codex 窗口字段解析失败: {e}") from e

    # ---- 预热 -----------------------------------------------------------

    def warmup(self, prompt: str) -> str:
        """`codex exec "<prompt>"` 走订阅且不计费，以此预热并开窗。"""
        try:
            out = subprocess.run(
                ["codex", "exec", prompt],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired as e:
            raise ProviderError("预热超时（codex exec 120s 未返回）") from e
        except (subprocess.SubprocessError, OSError) as e:
            raise ProviderError(f"调用 codex 失败: {e}") from e
        if out.returncode != 0:
            raise ProviderError(f"codex exec 退出码 {out.returncode}: {out.stderr.strip()[:200]}")
        return out.stdout.strip()[:200]


def _window_kind(seconds: Optional[int]) -> str:
    if seconds == 5 * 3600:
        return "five_hour"
    if seconds == 7 * 86400:
        return "weekly"
    if seconds and 28 * 86400 <= seconds <= 31 * 86400:
        return "monthly"
    return "unknown"
