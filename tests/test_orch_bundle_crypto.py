"""Encrypted orchestration bundle format (PBKDF2 + AES-GCM)."""

from __future__ import annotations

import io
import zipfile

import pytest

from orbit.gateway.orch_bundle_crypto import (
    decrypt_bundle,
    encrypt_bundle,
    is_encrypted_bundle,
)


def test_encrypt_decrypt_roundtrip() -> None:
    plain = b"hello zip payload"
    ct = encrypt_bundle(plain, "p4ss-w0rd")
    assert is_encrypted_bundle(ct)
    assert decrypt_bundle(ct, "p4ss-w0rd") == plain


def test_wrong_password() -> None:
    ct = encrypt_bundle(b"x", "good")
    with pytest.raises(ValueError, match="wrong password"):
        decrypt_bundle(ct, "bad")


def test_encrypt_requires_password() -> None:
    with pytest.raises(ValueError, match="password"):
        encrypt_bundle(b"x", "")


def test_zip_roundtrip() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a/b.txt", "inside")
    zbytes = buf.getvalue()
    ct = encrypt_bundle(zbytes, "pw")
    out = decrypt_bundle(ct, "pw")
    with zipfile.ZipFile(io.BytesIO(out)) as zf2:
        assert zf2.read("a/b.txt") == b"inside"
