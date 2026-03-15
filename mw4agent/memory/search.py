"""File-based memory search (Phase 1: no vector index, keyword in MEMORY.md + memory/*.md).

Aligns with OpenClaw MemorySearchManager.search / readFile semantics.
Session files (short-term memory) can be added later as another source.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional

# 相对工作区的记忆文件：根下 MEMORY.md / memory.md，以及 memory/*.md
MEMORY_ROOT_FILES = ("MEMORY.md", "memory.md")
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
    """List relative paths of memory files under workspace (MEMORY.md, memory.md, memory/*.md)."""
    workspace_dir = os.path.normpath(os.path.abspath(workspace_dir))
    out: List[str] = []
    for name in MEMORY_ROOT_FILES:
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
    """Tokenize query into words for simple keyword match."""
    s = (query or "").strip().lower()
    if not s:
        return []
    words = re.findall(r"[a-z0-9_\u4e00-\u9fff]+", s)
    return [w for w in words if len(w) >= 1]


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
