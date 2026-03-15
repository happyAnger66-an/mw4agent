"""File-based memory search (Phase 1: no vector index, keyword in MEMORY.md + memory/*.md).

Aligns with OpenClaw MemorySearchManager.search / readFile semantics.
Session files (short-term memory) can be added later as another source.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional

# 相对工作区的记忆/引导文件（与 OpenClaw VALID_BOOTSTRAP_NAMES 对齐）
# 根下：AGENTS, SOUL, TOOLS, IDENTITY, USER, HEARTBEAT, BOOTSTRAP, MEMORY；以及 memory/*.md
BOOTSTRAP_ROOT_FILES = (
    "AGENTS.md",
    "SOUL.md",
    "TOOLS.md",
    "IDENTITY.md",
    "USER.md",
    "HEARTBEAT.md",
    "BOOTSTRAP.md",
    "MEMORY.md",
    "memory.md",
)
# Bootstrap 注入顺序：身份与记忆优先，避免总字符上限时 MEMORY.md 被截掉导致“我是谁”无法回答
BOOTSTRAP_ORDER_FOR_PROMPT = (
    "IDENTITY.md",
    "USER.md",
    "MEMORY.md",
    "memory.md",
    "AGENTS.md",
    "SOUL.md",
    "TOOLS.md",
    "HEARTBEAT.md",
    "BOOTSTRAP.md",
)
MEMORY_ROOT_FILES = ("MEMORY.md", "memory.md")  # 兼容旧逻辑
MEMORY_DIR = "memory"
MEMORY_DIR_GLOB = "*.md"


@dataclass
class MemorySearchResult:
    """Single search hit (align with OpenClaw MemorySearchResult)."""
    path: str          # relative path, e.g. MEMORY.md or memory/foo.md
    start_line: int
    end_line: int
    score: float
    snippet: str
    source: str = "memory"  # "memory" | "sessions" when we add sessions


@dataclass
class MemoryReadResult:
    """Result of read_file (align with OpenClaw readFile)."""
    path: str
    text: str
    missing: bool = False


def list_memory_files(workspace_dir: str) -> List[str]:
    """List relative paths of memory/bootstrap files under workspace.

    Includes: AGENTS.md, SOUL.md, TOOLS.md, IDENTITY.md, USER.md, HEARTBEAT.md,
    BOOTSTRAP.md, MEMORY.md, memory.md (if present), and memory/*.md.
    """
    workspace_dir = os.path.normpath(os.path.abspath(workspace_dir))
    out: List[str] = []
    for name in BOOTSTRAP_ROOT_FILES:
        p = os.path.join(workspace_dir, name)
        if os.path.isfile(p):
            out.append(name)
    mem_dir = os.path.join(workspace_dir, MEMORY_DIR)
    if os.path.isdir(mem_dir):
        for name in sorted(os.listdir(mem_dir)):
            if name.endswith(".md"):
                out.append(os.path.join(MEMORY_DIR, name))
    return out


def _read_file_lines(abs_path: str) -> Optional[List[str]]:
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return None


def _normalize_query(query: str) -> List[str]:
    """Tokenize query into words for simple keyword match.
    For CJK multi-char words, also add 2-char substrings so that e.g. "用户身份" matches
    lines containing "身份" or "用户" (otherwise only exact "用户身份" would match).
    """
    s = (query or "").strip().lower()
    if not s:
        return []
    words = re.findall(r"[a-z0-9_\u4e00-\u9fff]+", s)
    words = [w for w in words if len(w) >= 1]
    seen: set[str] = set(words)
    for w in list(words):
        if len(w) >= 2 and all("\u4e00" <= c <= "\u9fff" for c in w):
            for i in range(len(w) - 1):
                bigram = w[i : i + 2]
                if bigram not in seen:
                    seen.add(bigram)
                    words.append(bigram)
    return words


def search(
    query: str,
    workspace_dir: str,
    *,
    max_results: int = 10,
    min_score: float = 0.0,
    session_key: Optional[str] = None,
) -> List[MemorySearchResult]:
    """Keyword search over MEMORY.md + memory/*.md. Returns hits with path, lines, snippet, score.

    Phase 1: no embedding; matches lines that contain any query word. Score is 1.0 per hit.
    session_key reserved for future session-scoped ranking.
    """
    workspace_dir = os.path.normpath(os.path.abspath(workspace_dir))
    results: List[MemorySearchResult] = []
    words = _normalize_query(query)
    if not words:
        return results

    pattern = re.compile("|".join(re.escape(w) for w in words), re.IGNORECASE)
    for rel_path in list_memory_files(workspace_dir):
        abs_path = os.path.join(workspace_dir, rel_path)
        lines = _read_file_lines(abs_path)
        if lines is None:
            continue
        for i, line in enumerate(lines):
            if pattern.search(line):
                # one hit per matching line; snippet is the line (trimmed)
                start = i + 1
                end = i + 1
                snippet = line.strip()[:500]
                results.append(
                    MemorySearchResult(
                        path=rel_path,
                        start_line=start,
                        end_line=end,
                        score=1.0,
                        snippet=snippet,
                        source="memory",
                    )
                )
                if len(results) >= max_results * 2:
                    break
        if len(results) >= max_results * 2:
            break

    # sort by path then line; take top max_results; filter by min_score
    results.sort(key=lambda r: (r.path, r.start_line))
    out = [r for r in results if r.score >= min_score][:max_results]
    return out


def read_file(
    workspace_dir: str,
    rel_path: str,
    *,
    from_line: Optional[int] = None,
    lines: Optional[int] = None,
) -> MemoryReadResult:
    """Read a memory file by relative path (e.g. MEMORY.md, memory/foo.md). Optional from/lines slice."""
    workspace_dir = os.path.normpath(os.path.abspath(workspace_dir))
    rel_path = (rel_path or "").strip().lstrip("/")
    if not rel_path:
        return MemoryReadResult(path=rel_path or "", text="", missing=True)

    # only allow paths that are in the memory file set (MEMORY.md, memory.md, memory/*.md)
    allowed = list_memory_files(workspace_dir)
    if rel_path not in allowed:
        return MemoryReadResult(path=rel_path, text="", missing=True)
    abs_path = os.path.normpath(os.path.join(workspace_dir, rel_path))
    if not os.path.isfile(abs_path):
        return MemoryReadResult(path=rel_path, text="", missing=True)

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.read().splitlines()
    except OSError:
        return MemoryReadResult(path=rel_path, text="", missing=True)

    start = 0
    if from_line is not None and from_line >= 1:
        start = min(from_line - 1, len(all_lines))
    count = len(all_lines) - start
    if lines is not None and lines >= 1:
        count = min(lines, count)
    selected = all_lines[start : start + count]
    text = "\n".join(selected)
    return MemoryReadResult(path=rel_path, text=text, missing=False)


def is_allowed_memory_write_path(rel_path: str) -> bool:
    """True if path is allowed for memory_write: MEMORY.md, memory.md, or memory/*.md."""
    rel_path = (rel_path or "").strip().lstrip("/")
    if not rel_path:
        return False
    if rel_path in ("MEMORY.md", "memory.md"):
        return True
    if rel_path.startswith("memory/") and rel_path.endswith(".md"):
        return True
    return False


def write_memory_file(
    workspace_dir: str,
    rel_path: str,
    content: str,
    *,
    append: bool = False,
) -> tuple[bool, str]:
    """Write or append to a memory file (MEMORY.md or memory/*.md). Returns (success, error_message)."""
    workspace_dir = os.path.normpath(os.path.abspath(workspace_dir))
    rel_path = (rel_path or "").strip().lstrip("/")
    if not rel_path:
        return False, "path is required"
    if not is_allowed_memory_write_path(rel_path):
        return False, f"memory_write only allows MEMORY.md, memory.md, or memory/*.md; got {rel_path!r}"
    abs_path = os.path.normpath(os.path.join(workspace_dir, rel_path))
    if not abs_path.startswith(workspace_dir):
        return False, "path is outside workspace"
    try:
        os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
        if append and os.path.isfile(abs_path):
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                existing = f.read()
            content = existing.rstrip() + "\n\n" + content.strip() if content.strip() else existing
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        return True, ""
    except OSError as e:
        return False, str(e)
