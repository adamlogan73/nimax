"""Shared test utilities (not fixtures — import directly)."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import niquests
from niquests.structures import CaseInsensitiveDict


@dataclass
class FakeRequest:
    """Minimal stand-in for niquests.PreparedRequest used in matcher/cassette tests."""

    method: str = "GET"
    url: str = "https://example.com/api"
    headers: dict[str, str] = field(default_factory=dict)
    body: str | bytes | None = None


def fake_response(status_code: int, body: str = "") -> niquests.Response:
    """Build a minimal niquests.Response for use in recording tests."""
    resp = niquests.Response()
    resp.status_code = status_code
    resp._content = body.encode("utf-8") if body else b""
    resp._content_consumed = True
    resp.headers = CaseInsensitiveDict()
    resp.encoding = "utf-8"
    return resp


def write_cassette(path: Path, data: dict[str, Any], fmt: str = "json") -> None:
    """Serialise *data* to *path* using the JSON or YAML serializer."""
    from nimax._serializers import JSONSerializer
    from nimax._serializers import YAMLSerializer

    serializer = YAMLSerializer() if fmt == "yaml" else JSONSerializer()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serializer.serialize(data), encoding="utf-8")


def minimal_cassette(
    interactions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the smallest valid cassette dict with one GET /api interaction."""
    return {
        "nimax_version": "0.1.0",
        "http_interactions": interactions
        if interactions is not None
        else [
            {
                "request": {
                    "method": "GET",
                    "uri": "https://example.com/api",
                    "headers": {},
                    "body": None,
                },
                "response": {
                    "status": {"code": 200, "message": "OK"},
                    "headers": {"Content-Type": ["application/json"]},
                    "body": {"string": '{"ok": true}'},
                    "protocol": None,
                    "url": "https://example.com/api",
                },
                "recorded_at": "2026-01-01T00:00:00Z",
            }
        ],
        "websocket_sessions": [],
    }
