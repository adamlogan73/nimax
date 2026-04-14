"""Integration tests for WebSocket recording and replay (sync + async)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import niquests
import pytest

from nimax import RecordMode
from nimax._cassette import Cassette
from nimax._websocket import AsyncFakeExtension
from nimax._websocket import FakeExtension
from nimax._websocket import Frame
from nimax._websocket import WebSocketSession
from tests._utils import write_cassette


# ── Helpers ───────────────────────────────────────────────────────────────────


def _ws_cassette(
    cassette_dir: Path,
    name: str,
    sessions: list[dict[str, Any]],
) -> Path:
    data: dict[str, Any] = {
        "nimax_version": "0.1.0",
        "http_interactions": [],
        "websocket_sessions": sessions,
    }
    path = cassette_dir / f"{name}.json"
    write_cassette(path, data)
    return path


def _ws_session_dict(
    uri: str,
    frames: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "uri": uri,
        "handshake_recorded_at": "2026-01-01T00:00:00Z",
        "protocol": None,
        "frames": frames,
    }


# ── FakeExtension (sync replay proxy) ─────────────────────────────────────────


class TestFakeExtension:
    def _session(self, payloads: list[str]) -> WebSocketSession:
        s = WebSocketSession(uri="ws://x", handshake_recorded_at="", protocol=None)
        s.frames = [Frame(direction="recv", type="text", payload=p) for p in payloads]
        return s

    def test_next_payload_returns_frames_in_order(self) -> None:
        ext = FakeExtension(self._session(["a", "b"]))
        assert ext.next_payload() == "a"
        assert ext.next_payload() == "b"

    def test_next_payload_returns_none_when_exhausted(self) -> None:
        ext = FakeExtension(self._session(["x"]))
        ext.next_payload()
        assert ext.next_payload() is None

    def test_next_payload_empty_session(self) -> None:
        assert FakeExtension(self._session([])).next_payload() is None

    def test_send_payload_is_noop(self) -> None:
        FakeExtension(self._session([])).send_payload("data")  # must not raise

    def test_close_is_noop(self) -> None:
        FakeExtension(self._session([])).close()  # must not raise


# ── AsyncFakeExtension (async replay proxy) ───────────────────────────────────


class TestAsyncFakeExtension:
    def _session(self, payloads: list[str]) -> WebSocketSession:
        s = WebSocketSession(uri="ws://x", handshake_recorded_at="", protocol=None)
        s.frames = [Frame(direction="recv", type="text", payload=p) for p in payloads]
        return s

    async def test_next_payload_in_order(self) -> None:
        ext = AsyncFakeExtension(self._session(["hello", "world"]))
        assert await ext.next_payload() == "hello"
        assert await ext.next_payload() == "world"

    async def test_next_payload_exhausted(self) -> None:
        ext = AsyncFakeExtension(self._session(["only"]))
        await ext.next_payload()
        assert await ext.next_payload() is None

    async def test_send_and_close_are_noops(self) -> None:
        ext = AsyncFakeExtension(self._session([]))
        await ext.send_payload("ignored")
        await ext.close()


# ── WS replay through Cassette context ────────────────────────────────────────


class TestWebSocketReplay:
    def test_sync_ws_response_has_101_status(self, cassette_dir: Path) -> None:
        path = _ws_cassette(
            cassette_dir,
            "ws",
            [_ws_session_dict("ws://example.com/chat", [])],
        )
        with Cassette(path=path, record_mode=RecordMode.NONE):
            resp = niquests.Session().get("ws://example.com/chat")
        assert resp.status_code == 101

    def test_sync_extension_replays_recv_frames(self, cassette_dir: Path) -> None:
        frames = [
            {"direction": "send", "type": "text", "payload": "ping", "offset_ms": 0},
            {"direction": "recv", "type": "text", "payload": "pong", "offset_ms": 5},
        ]
        path = _ws_cassette(
            cassette_dir,
            "ws",
            [_ws_session_dict("ws://example.com/chat", frames)],
        )
        with Cassette(path=path, record_mode=RecordMode.NONE):
            resp = niquests.Session().get("ws://example.com/chat")
        assert resp.raw.extension.next_payload() == "pong"
        assert resp.raw.extension.next_payload() is None

    def test_fifo_for_multiple_sessions_same_uri(self, cassette_dir: Path) -> None:
        sessions = [
            _ws_session_dict(
                "ws://example.com/chat",
                [{"direction": "recv", "type": "text", "payload": "s1", "offset_ms": 0}],
            ),
            _ws_session_dict(
                "ws://example.com/chat",
                [{"direction": "recv", "type": "text", "payload": "s2", "offset_ms": 0}],
            ),
        ]
        path = _ws_cassette(cassette_dir, "ws_fifo", sessions)
        with Cassette(path=path, record_mode=RecordMode.NONE):
            session = niquests.Session()
            resp1 = session.get("ws://example.com/chat")
            resp2 = session.get("ws://example.com/chat")
        assert resp1.raw.extension.next_payload() == "s1"
        assert resp2.raw.extension.next_payload() == "s2"

    def test_no_recorded_ws_session_raises(self, cassette_dir: Path) -> None:
        data: dict[str, Any] = {
            "nimax_version": "0.1.0",
            "http_interactions": [],
            "websocket_sessions": [],
        }
        path = cassette_dir / "empty.json"
        write_cassette(path, data)
        with pytest.raises(KeyError, match="No recorded WS session"):
            with Cassette(path=path, record_mode=RecordMode.NONE):
                niquests.Session().get("ws://example.com/chat")

    async def test_async_ws_response_has_101_status(self, cassette_dir: Path) -> None:
        path = _ws_cassette(
            cassette_dir,
            "ws_async",
            [_ws_session_dict("ws://example.com/chat", [])],
        )
        with Cassette(path=path, record_mode=RecordMode.NONE):
            resp = await niquests.AsyncSession().get("ws://example.com/chat")
        assert resp.status_code == 101

    async def test_async_extension_replays_recv_frames(self, cassette_dir: Path) -> None:
        frames = [
            {"direction": "recv", "type": "text", "payload": "async-msg", "offset_ms": 0},
        ]
        path = _ws_cassette(
            cassette_dir,
            "ws_async",
            [_ws_session_dict("ws://example.com/chat", frames)],
        )
        with Cassette(path=path, record_mode=RecordMode.NONE):
            resp = await niquests.AsyncSession().get("ws://example.com/chat")
        assert await resp.raw.extension.next_payload() == "async-msg"


# ── WS recording through Cassette context ─────────────────────────────────────


class TestWebSocketRecording:
    def test_sync_recording_writes_cassette(
        self, cassette_dir: Path, echo_ws_server: str
    ) -> None:
        path = cassette_dir / "ws_record.json"
        with Cassette(path=path, record_mode=RecordMode.ALL):
            session = niquests.Session()
            resp = session.get(echo_ws_server)
            ext = resp.extension  # RecordingExtension wrapping the live WS
            ext.send_payload("hello")
            ext.next_payload()  # echo "hello" back
            ext.close()

        assert path.exists()
        data = json.loads(path.read_text())
        assert len(data["websocket_sessions"]) == 1
        frames = data["websocket_sessions"][0]["frames"]
        send_frames = [f for f in frames if f["direction"] == "send"]
        recv_frames = [f for f in frames if f["direction"] == "recv"]
        assert send_frames[0]["payload"] == "hello"
        assert recv_frames[0]["payload"] == "hello"  # echo server mirrors

    def test_sync_recording_includes_offset_ms(
        self, cassette_dir: Path, echo_ws_server: str
    ) -> None:
        path = cassette_dir / "ws_offset.json"
        with Cassette(path=path, record_mode=RecordMode.ALL):
            resp = niquests.Session().get(echo_ws_server)
            ext = resp.extension
            ext.send_payload("timing-test")
            ext.next_payload()
            ext.close()

        frames = json.loads(path.read_text())["websocket_sessions"][0]["frames"]
        assert all("offset_ms" in f for f in frames)

    def test_sync_recording_followed_by_replay(
        self, cassette_dir: Path, echo_ws_server: str
    ) -> None:
        """Record once then replay from the cassette (no server needed for replay)."""
        path = cassette_dir / "ws_replay.json"
        # Pass 1: record
        with Cassette(path=path, record_mode=RecordMode.ALL):
            resp = niquests.Session().get(echo_ws_server)
            ext = resp.extension
            ext.send_payload("record-me")
            ext.next_payload()
            ext.close()

        # Pass 2: replay — server not involved
        with Cassette(path=path, record_mode=RecordMode.ONCE):
            resp = niquests.Session().get(echo_ws_server)
        assert resp.raw.extension.next_payload() == "record-me"

    async def test_async_recording_writes_cassette(
        self, cassette_dir: Path, echo_ws_server: str
    ) -> None:
        path = cassette_dir / "ws_async_record.json"
        with Cassette(path=path, record_mode=RecordMode.ALL):
            resp = await niquests.AsyncSession().get(echo_ws_server)
            ext = resp.extension  # AsyncRecordingExtension
            await ext.send_payload("async-hello")
            await ext.next_payload()
            await ext.close()

        data = json.loads(path.read_text())
        frames = data["websocket_sessions"][0]["frames"]
        assert any(f["payload"] == "async-hello" and f["direction"] == "send" for f in frames)
        assert any(f["payload"] == "async-hello" and f["direction"] == "recv" for f in frames)
