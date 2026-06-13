"""Claude Code provider —— S1 感知 + S4 预热。

感知：读 macOS Keychain 里的 CC oauth token → 打 oauth/usage → 解析 five_hour。
预热：`claude -p "<prompt>"` 发一条极短消息开窗。

安全红线：token 只留内存，不打印、不写盘、不外传。
依赖：纯 stdlib（subprocess + urllib），无第三方包。
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Dict

from .base import Provider, ProviderError, Usage, WindowUsage

KEYCHAIN_SERVICE = "Claude Code-credentials"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
ANTHROPIC_BETA = "oauth-2025-04-20"
USER_AGENT = "quota-butler/0.1"
HTTP_TIMEOUT = 15


class ClaudeProvider(Provider):
    name = "cc"

    # ---- 感知 -----------------------------------------------------------

    def read_usage(self) -> Usage:
        token = self._read_token()
        raw = self._fetch_usage(token)
        return self._parse(raw)

    def _read_token(self) -> str:
        """从 Keychain 读 access token。token 只在本函数返回值里流转，不落盘。"""
        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.SubprocessError, OSError) as e:
            raise ProviderError(f"读取 Keychain 失败: {e}") from e
        if out.returncode != 0:
            raise ProviderError(
                "Keychain 里没有 'Claude Code-credentials'，"
                "请确认本机已登录 Claude Code。"
            )
        try:
            data = json.loads(out.stdout)
            oauth = data["claudeAiOauth"]
            token = oauth["accessToken"]
        except (json.JSONDecodeError, KeyError) as e:
            raise ProviderError(f"Keychain 凭据结构异常: {e}") from e

        # 过期预警：MVP1 不自动刷新，只在已过期时给出明确报错
        expires_at = oauth.get("expiresAt")
        if isinstance(expires_at, (int, float)):
            now_ms = datetime.now().timestamp() * 1000
            if expires_at < now_ms:
                raise ProviderError(
                    "CC token 已过期（MVP1 不自动刷新）。"
                    "用一次 claude CLI 让它刷新 Keychain 后重试。"
                )
        return token

    def _fetch_usage(self, token: str) -> Dict[str, Any]:
        req = urllib.request.Request(
            USAGE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": ANTHROPIC_BETA,
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise ProviderError("oauth/usage 返回 401：token 失效或权限不足。") from e
            raise ProviderError(f"oauth/usage HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise ProviderError(f"oauth/usage 网络错误: {e.reason}") from e
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise ProviderError(f"oauth/usage 返回非 JSON: {e}") from e

    def _parse(self, raw: Dict[str, Any]) -> Usage:
        five = self._window(raw, "five_hour")  # 必须有
        seven = self._window(raw, "seven_day", required=False)
        return Usage(provider=self.name, five_hour=five, seven_day=seven)

    @staticmethod
    def _window(raw: Dict[str, Any], key: str, required: bool = True):
        node = raw.get(key)
        if not node:
            if required:
                raise ProviderError(f"响应缺少 {key} 字段")
            return None
        try:
            return WindowUsage(
                utilization=float(node["utilization"]),
                resets_at=_parse_dt(node["resets_at"]),
            )
        except (KeyError, ValueError, TypeError) as e:
            if required:
                raise ProviderError(f"{key} 字段解析失败: {e}") from e
            return None

    # ---- 预热（S4）------------------------------------------------------

    def warmup(self, prompt: str) -> str:
        """`claude -p "<prompt>"` 发一条极短消息开窗。

        ⚠️ 6/15 起 CC `claude -p` 独立计费——这是产品已知并接受的选择。
        """
        try:
            out = subprocess.run(
                ["claude", "-p", prompt],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired as e:
            raise ProviderError("预热超时（claude -p 120s 未返回）") from e
        except (subprocess.SubprocessError, OSError) as e:
            raise ProviderError(f"调用 claude 失败: {e}") from e
        if out.returncode != 0:
            raise ProviderError(f"claude -p 退出码 {out.returncode}: {out.stderr.strip()[:200]}")
        return out.stdout.strip()[:200]


_FRAC_RE = re.compile(r"\.(\d+)")


def _parse_dt(value: str) -> datetime:
    """解析 ISO8601（带时区）。

    Python 3.9 的 fromisoformat 很挑：只吃自己 isoformat() 的输出——不认 'Z' 后缀，
    也只认 3/6 位小数秒。真实 API 现在给 6 位微秒能跑，但这里做规整以防格式漂移
    （'Z' → '+00:00'，小数秒补/截成 6 位）。
    """
    v = value.strip()
    if v.endswith(("Z", "z")):
        v = v[:-1] + "+00:00"
    v = _FRAC_RE.sub(lambda m: "." + (m.group(1) + "000000")[:6], v, count=1)
    return datetime.fromisoformat(v)
