"""ChannelDock: lightweight channel metadata and shared policies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .types import ChannelCapabilities, ChannelId


RequireMentionResolver = Callable[[str | None], bool]


@dataclass(frozen=True)
class ChannelDock:
    """Lightweight dock entry.

    Keep this *cheap* to import (no channel SDKs, no network calls).
    """

    id: ChannelId
    capabilities: ChannelCapabilities

    # Group policy hooks (first version: require mention or not)
    resolve_require_mention: Optional[RequireMentionResolver] = None

    def require_mention(self, account_id: str | None = None) -> bool:
        if self.resolve_require_mention is None:
            return True
        return bool(self.resolve_require_mention(account_id))

