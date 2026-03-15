"""Memory 模块：基于文件的记忆检索（Phase 1 无向量，对齐 OpenClaw memory_tool / memory-cli）。

来源：工作区 MEMORY.md、memory.md、memory/*.md。
短期记忆（会话文件）后续可扩展为同一套 search/read_file 接口下的另一数据源。
"""

from .search import (
    list_memory_files,
    search,
    read_file,
    write_memory_file,
    is_allowed_memory_write_path,
    MemorySearchResult,
    MemoryReadResult,
)

__all__ = [
    "list_memory_files",
    "search",
    "read_file",
    "write_memory_file",
    "is_allowed_memory_write_path",
    "MemorySearchResult",
    "MemoryReadResult",
]
