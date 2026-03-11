"""Mention gating (OpenClaw-inspired).

For group chats, some channels require an explicit mention to trigger an agent run.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MentionGateResult:
    effective_was_mentioned: bool
    should_skip: bool


def resolve_mention_gating(
    *,
    require_mention: bool,
    can_detect_mention: bool,
    was_mentioned: bool,
    implicit_mention: bool = False,
    should_bypass_mention: bool = False,
) -> MentionGateResult:
    effective = bool(was_mentioned or implicit_mention or should_bypass_mention)
    should_skip = bool(require_mention and can_detect_mention and not effective)
    return MentionGateResult(effective_was_mentioned=effective, should_skip=should_skip)

