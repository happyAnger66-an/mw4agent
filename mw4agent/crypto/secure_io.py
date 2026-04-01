"""Encrypted file IO for MW4Agent.

Design goals:

- 统一所有「敏感文件」的读写路径（配置、skills、sessions 等）；
- 使用现代对称加密算法（AES-256-GCM），提供机密性 + 完整性；
- 密钥由环境变量提供，避免硬编码；
- 框架要足够简单，便于后续逐步接入更多文件类型。

格式约定（v1）：

- 文件头：ASCII 文本 `"MW4AGENT_ENC_v1\n"`（固定 16 字节左右，可人眼识别）；
- 之后是一个 JSON 行，形如：
  {"alg":"AES-256-GCM","kdf":"env","nonce":"<base64>","tag":"<base64>"}
- 最后是密文（base64 编码）。

当前实现重点：

- 提供 `EncryptedFileStore`，支持 read_json / write_json；
- 提供 `get_default_encrypted_store()`，统一从 `MW4AGENT_SECRET_KEY` 读取密钥；
- 若环境未配置密钥，抛出明确错误。
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


MAGIC_HEADER = b"MW4AGENT_ENC_v1\n"


class EncryptionConfigError(RuntimeError):
    """Raised when encryption is requested but not properly configured."""


def is_encryption_enabled(env_var: str = "MW4AGENT_IS_ENC") -> bool:
    """Check whether encryption is enabled via env switch.

    - 默认关闭：未设置或为空时视为关闭；
    - 显式开启：当值为 "1" / "true" / "on" / "yes"（忽略大小写）时开启。
    """
    raw = os.getenv(env_var)
    if raw is None:
        return False
    value = raw.strip().lower()
    if not value:
        return False
    return value in ("1", "true", "on", "yes")

def _load_key_from_env(env_var: str = "MW4AGENT_SECRET_KEY") -> bytes:
    """Load symmetric key from env (base64 或原始 32 字节 hex/raw).

    推荐：设置为 base64 编码后的 32 字节随机数：
      python - << 'PY'
      import os, base64
      print(base64.b64encode(os.urandom(32)).decode())
      PY
    """
    raw = os.getenv(env_var, "").strip()
    if not raw:
        raise EncryptionConfigError(
            f"{env_var} is not set; encryption cannot proceed. "
            "Please set a 32-byte secret key (base64-encoded)."
        )
    # 尝试按 base64 解码
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception:
        # 退化为直接使用 utf-8 bytes（仅用于开发/测试，不推荐）
        key = raw.encode("utf-8")
    if len(key) not in (16, 24, 32):
        raise EncryptionConfigError(
            f"{env_var} must decode to 16/24/32 bytes for AES-GCM; got {len(key)} bytes."
        )
    return key


@dataclass
class EncryptedFileStore:
    """Symmetric encrypted file store using AES-GCM."""

    key: bytes

    def _encrypt(self, data: bytes) -> bytes:
        aes = AESGCM(self.key)
        nonce = os.urandom(12)  # 96-bit nonce
        ct = aes.encrypt(nonce, data, associated_data=None)
        # AESGCM.encrypt 返回 nonce+ciphertext+tag? 实际上 cryptography 的 AESGCM.encrypt
        # 返回 ciphertext||tag，我们单独保存 nonce 并从末尾切出 tag。
        tag = ct[-16:]
        ciphertext = ct[:-16]
        meta = {
            "alg": "AES-256-GCM" if len(self.key) == 32 else "AES-GCM",
            "kdf": "env",
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "tag": base64.b64encode(tag).decode("ascii"),
        }
        header = MAGIC_HEADER + json.dumps(meta, separators=(",", ":")).encode("utf-8") + b"\n"
        body = base64.b64encode(ciphertext)
        return header + body

    def _decrypt(self, blob: bytes) -> bytes:
        if not blob.startswith(MAGIC_HEADER):
            raise EncryptionConfigError("Encrypted file missing expected MW4AGENT header")
        rest = blob[len(MAGIC_HEADER) :]
        meta_line, _, b64_ct = rest.partition(b"\n")
        if not meta_line or not b64_ct:
            raise EncryptionConfigError("Encrypted file is truncated or malformed")
        try:
            meta = json.loads(meta_line.decode("utf-8"))
        except Exception as e:
            raise EncryptionConfigError(f"Invalid encrypted header JSON: {e}")
        nonce = base64.b64decode(meta.get("nonce", ""))
        tag = base64.b64decode(meta.get("tag", ""))
        ciphertext = base64.b64decode(b64_ct)
        if len(tag) != 16:
            raise EncryptionConfigError("Invalid tag size in encrypted header")
        ct_plus_tag = ciphertext + tag
        aes = AESGCM(self.key)
        return aes.decrypt(nonce, ct_plus_tag, associated_data=None)

    # --- Public helpers -------------------------------------------------

    def write_json(self, path: str, obj: Any) -> None:
        """Encrypt and write JSON serializable object to file."""
        plain = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        enc = self._encrypt(plain)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(enc)

    def read_json(self, path: str, *, fallback_plaintext: bool = True) -> Any:
        """Read and decrypt JSON file.

        - 如果文件以 MAGIC_HEADER 开头：按加密格式解密再解析 JSON；
        - 否则：
          - fallback_plaintext=True 时，按普通 UTF-8 JSON 文件读取（便于迁移）；
          - fallback_plaintext=False 时，直接报错。
        """
        with open(path, "rb") as f:
            blob = f.read()
        if blob.startswith(MAGIC_HEADER):
            data = self._decrypt(blob)
            return json.loads(data.decode("utf-8"))
        if not fallback_plaintext:
            raise EncryptionConfigError("File is not encrypted but plaintext is disabled")
        return json.loads(blob.decode("utf-8"))


_default_store: Optional[EncryptedFileStore] = None


def get_default_encrypted_store() -> EncryptedFileStore:
    """Get process-wide default encrypted file store.

    Key 来源：`MW4AGENT_SECRET_KEY` 环境变量。
    """
    global _default_store
    if not is_encryption_enabled():
        raise EncryptionConfigError(
            "Encryption disabled via MW4AGENT_IS_ENC; falling back to plaintext."
        )
    if _default_store is None:
        key = _load_key_from_env()
        _default_store = EncryptedFileStore(key=key)
    return _default_store

