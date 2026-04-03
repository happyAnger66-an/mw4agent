"""Tests for config secret redaction and merge-on-save."""

from mw4agent.config.redact import (
    REDACTED_SECRET_PLACEHOLDER,
    is_redacted_placeholder,
    merge_preserve_redacted_secrets,
    redact_secrets,
)


def test_redact_llm_api_key() -> None:
    out = redact_secrets({"provider": "x", "api_key": "secret123", "model_id": "m"})
    assert out["provider"] == "x"
    assert out["model_id"] == "m"
    assert out["api_key"] == REDACTED_SECRET_PLACEHOLDER


def test_redact_nested_search_keys() -> None:
    out = redact_secrets(
        {
            "web": {
                "search": {
                    "apiKey": "k1",
                    "perplexity": {"api_key": "k2"},
                }
            }
        }
    )
    assert out["web"]["search"]["apiKey"] == REDACTED_SECRET_PLACEHOLDER
    assert out["web"]["search"]["perplexity"]["api_key"] == REDACTED_SECRET_PLACEHOLDER


def test_merge_preserves_placeholder() -> None:
    old = {"api_key": "real", "model_id": "m"}
    new = {"api_key": REDACTED_SECRET_PLACEHOLDER, "model_id": "m2"}
    merged = merge_preserve_redacted_secrets(old, new)
    assert merged["api_key"] == "real"
    assert merged["model_id"] == "m2"


def test_merge_allows_clear_secret() -> None:
    old = {"api_key": "real"}
    new = {"api_key": ""}
    merged = merge_preserve_redacted_secrets(old, new)
    assert merged["api_key"] == ""


def test_is_redacted_placeholder() -> None:
    assert is_redacted_placeholder("********")
    assert is_redacted_placeholder("****")
    assert not is_redacted_placeholder("")
    assert not is_redacted_placeholder("sk-real")
