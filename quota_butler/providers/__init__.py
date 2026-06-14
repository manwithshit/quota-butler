"""Provider 抽象：感知额度 + 预热开窗。CC 全功能；Codex 仅感知。"""

from .base import Provider, Usage, WindowUsage, ProviderError
from .claude import ClaudeProvider
from .codex import CodexProvider

__all__ = [
    "Provider",
    "Usage",
    "WindowUsage",
    "ProviderError",
    "ClaudeProvider",
    "CodexProvider",
    "get_provider",
]


def get_provider(name: str) -> Provider:
    name = (name or "cc").lower()
    if name in ("cc", "claude", "claude-code"):
        return ClaudeProvider()
    if name in ("codex", "cx"):
        return CodexProvider()
    raise ProviderError(f"未知 provider: {name!r}（支持 cc / codex）")
