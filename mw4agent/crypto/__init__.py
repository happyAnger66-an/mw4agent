"""Crypto helpers for MW4Agent.

This module provides a thin abstraction over symmetric encryption for
config/session/skill files. The goal is:

- 统一加密算法与密钥管理
- 提供简单的加密读写 API，逐步替换裸 open()/read()/write()
"""

from .secure_io import (
    EncryptionConfigError,
    EncryptedFileStore,
    get_default_encrypted_store,
    is_encryption_enabled,
)

__all__ = [
    "EncryptionConfigError",
    "EncryptedFileStore",
    "get_default_encrypted_store",
    "is_encryption_enabled",
]

