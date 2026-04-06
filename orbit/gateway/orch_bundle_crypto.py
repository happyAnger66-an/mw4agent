"""Password-based encryption for orchestration bundle exports (ZIP bytes in, ciphertext out)."""

from __future__ import annotations

import os
import struct
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC: Final[bytes] = b"ORCHENC1"
FORMAT_VERSION: Final[int] = 1
PBKDF2_ITERATIONS_DEFAULT: Final[int] = 390_000
SALT_LEN: Final[int] = 16
NONCE_LEN: Final[int] = 12
KEY_LEN: Final[int] = 32
_HEADER_LEN: Final[int] = len(MAGIC) + 4 + 4 + SALT_LEN + NONCE_LEN


def is_encrypted_bundle(data: bytes) -> bool:
    return bool(data) and data.startswith(MAGIC)


def _derive_key(password: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_LEN,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive((password or "").encode("utf-8"))


def encrypt_bundle(plaintext: bytes, password: str) -> bytes:
    """AES-GCM encrypt ZIP bytes; output includes magic header and PBKDF2 parameters."""
    if not password:
        raise ValueError("password is required for encrypted export")
    salt = os.urandom(SALT_LEN)
    key = _derive_key(password, salt, PBKDF2_ITERATIONS_DEFAULT)
    aesgcm = AESGCM(key)
    nonce = os.urandom(NONCE_LEN)
    ct = aesgcm.encrypt(nonce, plaintext, None)
    return (
        MAGIC
        + struct.pack(">II", FORMAT_VERSION, PBKDF2_ITERATIONS_DEFAULT)
        + salt
        + nonce
        + ct
    )


def decrypt_bundle(ciphertext: bytes, password: str) -> bytes:
    """Decrypt bundle produced by ``encrypt_bundle``; raises ``ValueError`` on bad password or data."""
    if not is_encrypted_bundle(ciphertext):
        raise ValueError("not an encrypted orchestration bundle")
    if len(ciphertext) < _HEADER_LEN + 16:
        raise ValueError("encrypted bundle truncated")
    off = len(MAGIC)
    ver = struct.unpack(">I", ciphertext[off : off + 4])[0]
    off += 4
    if ver != FORMAT_VERSION:
        raise ValueError(f"unsupported bundle crypto format version {ver}")
    iters = struct.unpack(">I", ciphertext[off : off + 4])[0]
    off += 4
    if iters < 100_000 or iters > 10_000_000:
        raise ValueError("invalid PBKDF2 iteration count in bundle")
    salt = ciphertext[off : off + SALT_LEN]
    off += SALT_LEN
    nonce = ciphertext[off : off + NONCE_LEN]
    off += NONCE_LEN
    ct = ciphertext[off:]
    key = _derive_key(password, salt, iters)
    aesgcm = AESGCM(key)
    try:
        return aesgcm.decrypt(nonce, ct, None)
    except InvalidTag as e:
        raise ValueError("wrong password or corrupted bundle") from e
