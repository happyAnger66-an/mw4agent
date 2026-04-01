"""Basic tests for the MW4Agent encrypted IO framework.

These tests exercise:
- `EncryptedFileStore` round‑trip encryption / decryption;
- header / plaintext fallback behavior;
- environment‑driven key loading contract used by `get_default_encrypted_store`.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path

from mw4agent.crypto import EncryptedFileStore, get_default_encrypted_store  # type: ignore[attr-defined]
from mw4agent.crypto.secure_io import (  # type: ignore[attr-defined]
    MAGIC_HEADER,
    EncryptionConfigError,
    _load_key_from_env,
    is_encryption_enabled,
)


def _make_temp_file() -> Path:
    fd, path = tempfile.mkstemp(prefix="mw4agent.crypto.", suffix=".json")
    os.close(fd)
    return Path(path)


def _random_key_b64(length: int = 32) -> str:
    return base64.b64encode(os.urandom(length)).decode("ascii")


def test_encrypted_file_store_round_trip_json() -> None:
    """Encrypt + decrypt a JSON payload using a random AES‑GCM key."""
    key = base64.b64decode(_random_key_b64(32))
    store = EncryptedFileStore(key=key)
    tmp = _make_temp_file()
    try:
        payload = {"hello": "world", "n": 42}
        store.write_json(str(tmp), payload)

        # File on disk should start with the magic header and not contain plaintext JSON.
        raw = tmp.read_bytes()
        assert raw.startswith(MAGIC_HEADER)
        assert b"hello" not in raw

        loaded = store.read_json(str(tmp))
        assert loaded == payload
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def test_read_json_plaintext_fallback() -> None:
    """Plaintext JSON files are still readable for migration when header missing."""
    key = base64.b64decode(_random_key_b64(32))
    store = EncryptedFileStore(key=key)
    tmp = _make_temp_file()
    try:
        payload = {"mode": "plaintext", "value": 1}
        tmp.write_text(json.dumps(payload), encoding="utf-8")

        loaded = store.read_json(str(tmp), fallback_plaintext=True)
        assert loaded == payload

        # When plaintext is disabled, a non‑encrypted file should raise.
        try:
            store.read_json(str(tmp), fallback_plaintext=False)
        except EncryptionConfigError:
            pass
        else:  # pragma: no cover - defensive
            raise AssertionError("expected EncryptionConfigError for non‑encrypted file")
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def test_load_key_from_env_and_default_store(monkeypatch) -> None:
    """`_load_key_from_env` and `get_default_encrypted_store` obey env contract."""
    env_key = _random_key_b64(32)
    monkeypatch.setenv("MW4AGENT_SECRET_KEY", env_key)
    monkeypatch.setenv("MW4AGENT_IS_ENC", "1")  # 确保加密开启，不受外部环境影响
    import mw4agent.crypto.secure_io as secure_io_mod
    secure_io_mod._default_store = None  # type: ignore[attr-defined]

    key = _load_key_from_env()
    assert key in (base64.b64decode(env_key), env_key.encode("utf-8"))

    # Default store should initialize successfully with the same env var.
    store = get_default_encrypted_store()
    assert isinstance(store, EncryptedFileStore)


def test_load_key_from_env_missing_raises(monkeypatch) -> None:
    """Missing env var should result in a clear configuration error."""
    monkeypatch.delenv("MW4AGENT_SECRET_KEY", raising=False)
    try:
        _load_key_from_env()
    except EncryptionConfigError as e:
        assert "MW4AGENT_SECRET_KEY is not set" in str(e)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected EncryptionConfigError when env is missing")


def test_is_encryption_enabled_switch(monkeypatch) -> None:
    """MW4AGENT_IS_ENC controls encryption enable/disable with sensible defaults."""
    # 默认：未设置或为空 → 关闭
    monkeypatch.delenv("MW4AGENT_IS_ENC", raising=False)
    assert is_encryption_enabled() is False
    monkeypatch.setenv("MW4AGENT_IS_ENC", "")
    assert is_encryption_enabled() is False

    # 显式关闭（以及其他未知值）都视为关闭
    for val in ("0", "false", "False", "OFF", "no", "No", "random"):
        monkeypatch.setenv("MW4AGENT_IS_ENC", val)
        assert is_encryption_enabled() is False

    # 显式开启
    for val in ("1", "true", "True", "yes", "Yes", "on", "ON"):
        monkeypatch.setenv("MW4AGENT_IS_ENC", val)
        assert is_encryption_enabled() is True

