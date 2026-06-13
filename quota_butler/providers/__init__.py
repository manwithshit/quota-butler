"""Provider 抽象：感知额度 + 预热开窗。MVP1 只实现 Claude Code。"""

from .base import Provider, Usage, WindowUsage, ProviderError
from .claude import ClaudeProvider

__all__ = [
    "Provider",
    "Usage",
    "WindowUsage",
    "ProviderError",
    "ClaudeProvider",
    "get_provider",
]


def get_provider(name: str) -> Provider:
    name = (name or "cc").lower()
    if name in ("cc", "claude", "claude-code"):
        return ClaudeProvider()
    raise ProviderError(f"未知 provider: {name!r}（MVP1 只支持 cc）")
