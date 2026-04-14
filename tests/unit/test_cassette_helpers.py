"""Unit tests for the private helper functions in _cassette.py."""

from __future__ import annotations

from nimax._cassette import (
    _is_ws,
    _migrate_interaction,
    _normalize_body,
    _normalize_headers,
    _normalize_status,
)

# ── _normalize_status ─────────────────────────────────────────────────────────


class TestNormalizeStatus:
    def test_dict_passthrough(self) -> None:
        s = {"code": 200, "message": "OK"}
        assert _normalize_status(s) == s

    def test_int_known_status(self) -> None:
        assert _normalize_status(200) == {"code": 200, "message": "OK"}
        assert _normalize_status(404) == {"code": 404, "message": "Not Found"}

    def test_int_unknown_status(self) -> None:
        result = _normalize_status(999)
        assert result == {"code": 999, "message": ""}

    def test_string_int(self) -> None:
        result = _normalize_status("201")
        assert result["code"] == 201


# ── _normalize_body ───────────────────────────────────────────────────────────


class TestNormalizeBody:
    def test_none_returns_none(self) -> None:
        assert _normalize_body(None) is None

    def test_dict_passthrough(self) -> None:
        b = {"string": "hello"}
        assert _normalize_body(b) == b

    def test_string(self) -> None:
        assert _normalize_body("hello") == {"string": "hello"}

    def test_bytes(self) -> None:
        assert _normalize_body(b"hello") == {"string": "hello"}

    def test_empty_string_returns_none(self) -> None:
        assert _normalize_body("") is None

    def test_empty_bytes_returns_none(self) -> None:
        assert _normalize_body(b"") is None


# ── _normalize_headers ────────────────────────────────────────────────────────


class TestNormalizeHeaders:
    def test_string_value_wrapped_in_list(self) -> None:
        result = _normalize_headers({"Content-Type": "application/json"})
        assert result == {"Content-Type": ["application/json"]}

    def test_list_value_passthrough(self) -> None:
        result = _normalize_headers({"X-Header": ["a", "b"]})
        assert result == {"X-Header": ["a", "b"]}

    def test_mixed(self) -> None:
        result = _normalize_headers({"A": "x", "B": ["y", "z"]})
        assert result == {"A": ["x"], "B": ["y", "z"]}

    def test_empty_dict(self) -> None:
        assert _normalize_headers({}) == {}


# ── _migrate_interaction ──────────────────────────────────────────────────────


class TestMigrateInteraction:
    def _entry(self, **overrides: object) -> dict:
        base = {
            "request": {"url": "https://example.com", "method": "GET"},
            "response": {"status": 200, "body": "hello", "headers": {}},
        }
        base.update(overrides)
        return base

    def test_url_renamed_to_uri(self) -> None:
        result = _migrate_interaction(self._entry())
        assert "uri" in result["request"]
        assert "url" not in result["request"]

    def test_uri_key_preserved_if_present(self) -> None:
        entry = {
            "request": {"uri": "https://example.com", "method": "GET"},
            "response": {"status": 200, "body": None, "headers": {}},
        }
        result = _migrate_interaction(entry)
        assert result["request"]["uri"] == "https://example.com"

    def test_legacy_status_int_converted(self) -> None:
        result = _migrate_interaction(self._entry())
        assert result["response"]["status"] == {"code": 200, "message": "OK"}

    def test_legacy_body_string_wrapped(self) -> None:
        result = _migrate_interaction(self._entry())
        assert result["response"]["body"] == {"string": "hello"}

    def test_null_body_stays_none(self) -> None:
        entry = {
            "request": {"url": "https://example.com"},
            "response": {"status": 204, "body": None, "headers": {}},
        }
        assert _migrate_interaction(entry)["response"]["body"] is None

    def test_recorded_at_preserved(self) -> None:
        entry = {**self._entry(), "recorded_at": "2026-01-01T00:00:00Z"}
        assert _migrate_interaction(entry)["recorded_at"] == "2026-01-01T00:00:00Z"

    def test_missing_recorded_at_defaults_empty(self) -> None:
        result = _migrate_interaction(self._entry())
        assert result["recorded_at"] == ""

    def test_headers_defaults_added(self) -> None:
        entry = {
            "request": {"url": "https://x.com"},
            "response": {"status": 200, "body": None, "headers": {}},
        }
        result = _migrate_interaction(entry)
        assert "headers" in result["request"]
        assert "body" in result["request"]


# ── _is_ws ────────────────────────────────────────────────────────────────────


class TestIsWs:
    def test_ws_scheme(self) -> None:
        assert _is_ws("ws://example.com/chat")

    def test_wss_scheme(self) -> None:
        assert _is_ws("wss://example.com/chat")

    def test_http_is_not_ws(self) -> None:
        assert not _is_ws("http://example.com")

    def test_https_is_not_ws(self) -> None:
        assert not _is_ws("https://example.com")

    def test_empty_string(self) -> None:
        assert not _is_ws("")
