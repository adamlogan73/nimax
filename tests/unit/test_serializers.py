"""Unit tests for JSONSerializer and YAMLSerializer."""

from __future__ import annotations

import json
from typing import Any

import pytest

from nimax import JSONSerializer, YAMLSerializer
from nimax._serializers import BUILTIN_SERIALIZERS

SAMPLE: dict[str, Any] = {
    "nimax_version": "0.1.0",
    "http_interactions": [
        {
            "request": {"method": "GET", "uri": "https://example.com"},
            "response": {
                "status": {"code": 200, "message": "OK"},
                "body": {"string": "hello"},
            },
            "recorded_at": "2026-01-01T00:00:00Z",
        },
    ],
    "websocket_sessions": [],
}


# ── JSONSerializer ────────────────────────────────────────────────────────────


class TestJSONSerializer:
    def setup_method(self) -> None:
        self.s = JSONSerializer()

    def test_extension(self) -> None:
        assert self.s.extension == "json"

    def test_round_trip(self) -> None:
        assert self.s.deserialize(self.s.serialize(SAMPLE)) == SAMPLE

    def test_output_is_pretty_printed(self) -> None:
        out = self.s.serialize(SAMPLE)
        assert "\n" in out  # indented

    def test_unicode_not_escaped(self) -> None:
        data = {"key": "héllo wörld"}
        assert "héllo wörld" in self.s.serialize(data)

    def test_deserialize_invalid_raises(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            self.s.deserialize("{not valid json")


# ── YAMLSerializer ────────────────────────────────────────────────────────────


class TestYAMLSerializer:
    def setup_method(self) -> None:
        self.s = YAMLSerializer()

    def test_extension(self) -> None:
        assert self.s.extension == "yaml"

    def test_round_trip(self) -> None:
        assert self.s.deserialize(self.s.serialize(SAMPLE)) == SAMPLE

    def test_output_is_yaml(self) -> None:
        out = self.s.serialize({"key": "val"})
        assert "key: val" in out

    def test_deserialize_empty_string_returns_empty_dict(self) -> None:
        assert self.s.deserialize("") == {}

    def test_unicode_preserved(self) -> None:
        data = {"greeting": "héllo"}
        assert "héllo" in self.s.serialize(data)


# ── Registry ─────────────────────────────────────────────────────────────────


def test_builtin_serializers_registry() -> None:
    assert set(BUILTIN_SERIALIZERS.keys()) == {"json", "yaml"}
