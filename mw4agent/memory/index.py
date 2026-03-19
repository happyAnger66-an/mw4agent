"""SQLite-based MemoryIndex (Phase 1: FTS-like search over workspace files).

Phase 1 goals:
- Provide a unified on-disk index for file-based memory sources (MEMORY.md, memory/*.md, etc.).
- Keep behavior equivalent to existing keyword search while routing through a single index.
- NO embeddings / vector search yet; simple LIKE-based search is sufficient.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

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
MEMORY_DIR = "memory"


@dataclass
class Chunk:
    id: int
    source: str
    path: str
    content: str
    created_at: int
    updated_at: int


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ensure_dir(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def _open_db(path: str) -> sqlite3.Connection:
    _ensure_dir(path)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            path TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source)")
    conn.commit()
    return conn


def _read_file_text(abs_path: str) -> Optional[str]:
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _session_id_from_chunk_path(source: str, path: str) -> Optional[str]:
    """Derive session id from synthetic path sessions/<id>.jsonl (Phase 2)."""
    if str(source or "").strip() != "session":
        return None
    p = str(path or "")
    prefix = "sessions/"
    if p.startswith(prefix) and p.endswith(".jsonl"):
        return p[len(prefix) : -len(".jsonl")] or None
    return None


def _normalize_query_local(query: str) -> List[str]:
    """Local copy of the keyword tokenizer used for LIKE-based search.

    We intentionally duplicate minimal logic here to avoid importing the full
    memory.search module (which would cause circular imports during tool
    registration).
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


def index_files(
    *,
    db_path: str,
    workspace_dir: str,
    sources: Iterable[str] = ("memory",),
) -> None:
    """Rebuild index for file-based memory sources under workspace_dir.

    Currently only supports source="memory" (MEMORY.md + memory/*.md).
    The index is rebuilt from scratch each time this is called.
    """
    workspace_dir = os.path.normpath(os.path.abspath(workspace_dir))
    conn = _open_db(db_path)
    try:
        cur = conn.cursor()
        # For Phase 1 keep it simple: drop existing memory chunks and re-insert.
        if "memory" in sources:
            cur.execute("DELETE FROM chunks WHERE source = ?", ("memory",))
            now = _now_ms()
            # Root-level bootstrap/memory files
            for name in BOOTSTRAP_ROOT_FILES:
                abs_path = os.path.join(workspace_dir, name)
                if not os.path.isfile(abs_path):
                    continue
                text = _read_file_text(abs_path)
                if not text:
                    continue
                cur.execute(
                    "INSERT INTO chunks (source, path, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    ("memory", name, text, now, now),
                )
            # memory/*.md
            mem_dir = os.path.join(workspace_dir, MEMORY_DIR)
            if os.path.isdir(mem_dir):
                for entry in sorted(os.listdir(mem_dir)):
                    if not entry.endswith(".md"):
                        continue
                    rel_path = os.path.join(MEMORY_DIR, entry)
                    abs_path = os.path.join(workspace_dir, rel_path)
                    if not os.path.isfile(abs_path):
                        continue
                    text = _read_file_text(abs_path)
                    if not text:
                        continue
                    cur.execute(
                        "INSERT INTO chunks (source, path, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                        ("memory", rel_path, text, now, now),
                    )
        conn.commit()
    finally:
        conn.close()


def upsert_chunk(
    *,
    db_path: str,
    source: str,
    path: str,
    content: str,
    updated_at_ms: Optional[int] = None,
) -> None:
    """Upsert a single chunk by (source, path).

    Schema stays minimal; emulate upsert via delete+insert.
    """
    source = str(source or "").strip() or "memory"
    path = str(path or "").strip()
    if not path:
        return
    content = str(content or "")
    now = int(updated_at_ms or _now_ms())
    conn = _open_db(db_path)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM chunks WHERE source = ? AND path = ?", (source, path))
        cur.execute(
            "INSERT INTO chunks (source, path, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (source, path, content, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def search_index(
    *,
    db_path: str,
    query: str,
    max_results: int = 10,
    min_score: float = 0.0,
    sources: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Search the index using simple LIKE-based matching over content.

    This is intentionally minimal for Phase 1; later phases can add FTS/embeddings.
    """
    query = (query or "").strip()
    if not query:
        return []
    conn = _open_db(db_path)
    try:
        cur = conn.cursor()
        words = _normalize_query_local(query)
        if not words:
            return []
        # Build OR conditions with parameters: content LIKE ? OR content LIKE ? ...
        clauses = []
        params: List[str] = []
        for w in words:
            clauses.append("content LIKE ?")
            params.append(f"%{w}%")
        where = " OR ".join(clauses)
        extra = ""
        if sources:
            srcs = [str(s).strip() for s in sources if str(s).strip()]
            if srcs:
                extra = " AND source IN (" + ",".join(["?"] * len(srcs)) + ")"
                params.extend(srcs)
        sql = f"SELECT id, source, path, content, created_at, updated_at FROM chunks WHERE ({where}){extra} LIMIT ?"
        params.append(str(max_results * 2))
        cur.execute(sql, params)
        rows = cur.fetchall()
    finally:
        conn.close()

    results: List[Dict[str, Any]] = []
    for _id, source, path, content, created_at, updated_at in rows:
        text = str(content or "")
        # Very rough snippet: first 500 chars.
        snippet = text.strip()[:500]
        src = str(source or "memory") or "memory"
        results.append(
            {
                "path": str(path or ""),
                "start_line": 1,
                "end_line": 1,
                "score": 1.0,
                "snippet": snippet,
                "source": src,
                "session_id": _session_id_from_chunk_path(src, str(path or "")),
                "created_at": int(created_at) if created_at is not None else None,
                "updated_at": int(updated_at) if updated_at is not None else None,
            }
        )
        if len(results) >= max_results:
            break

    if min_score > 0.0:
        results = [r for r in results if float(r.get("score") or 0.0) >= float(min_score)]
    return results

