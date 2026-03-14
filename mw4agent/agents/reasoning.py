"""Reasoning block parsing - split <think>...</think> and final text.

OpenClaw-style: many models return <think>...</think> before the final answer.
We split so the frontend can show reasoning separately when reasoning_level is on/stream.
"""

import re
from typing import Tuple

# Match <think> or </think> (case-insensitive, allow whitespace in tag name).
_THINKING_OPEN = re.compile(r"<\s*think(?:ing)?\s*>", re.IGNORECASE)
_THINKING_CLOSE = re.compile(r"<\s*/\s*think(?:ing)?\s*>", re.IGNORECASE)


def split_reasoning_and_text(content: str) -> Tuple[str, str]:
    """Split content into reasoning (inside <think>...</think>) and final text (rest).

    Returns:
        (reasoning, text). Either can be empty. Text has <think> blocks stripped
        so it's safe to show as the main reply when reasoning_level is off.
    """
    if not content or not isinstance(content, str):
        return ("", content or "")

    text = content
    reasoning_parts: list[str] = []
    result_text_parts: list[str] = []

    while True:
        open_m = _THINKING_OPEN.search(text)
        if not open_m:
            result_text_parts.append(text)
            break
        # Append everything before <think> to result text.
        result_text_parts.append(text[: open_m.start()])
        rest = text[open_m.end() :]
        close_m = _THINKING_CLOSE.search(rest)
        if not close_m:
            # Unclosed <think>: treat remainder as text.
            result_text_parts.append(rest)
            break
        reasoning_parts.append(rest[: close_m.start()].strip())
        text = rest[close_m.end() :]

    reasoning = "\n\n".join(p for p in reasoning_parts if p).strip()
    final_text = "".join(result_text_parts).strip()
    return (reasoning, final_text)
