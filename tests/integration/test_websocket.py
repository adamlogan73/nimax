"""Integration tests for WebSocket recording and replay (sync + async)."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import TYPE_CHECKING, Any

import niquests
import pytest

from nimax import RecordMode
from nimax._cassette import Cassette
from nimax._websocket import (
    AsyncFakeExtension,
    FakeExtension,
    Frame,
    WebSocketSession,
    dotted_json_id_extractor,
)
from tests._utils import write_cassette

if TYPE_CHECKING:
    from pathlib import Path

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

    def test_next_payload_blocks_until_matching_send(self) -> None:
        s = WebSocketSession(uri="ws://x", handshake_recorded_at="", protocol=None)
        s.frames = [
            Frame(direction="send", type="text", payload="ping"),
            Frame(direction="recv", type="text", payload="pong"),
        ]
        ext = FakeExtension(s)
        released = threading.Event()

        def reader() -> None:
            ext.next_payload()
            released.set()

        thread = threading.Thread(target=reader)
        thread.start()
        try:
            assert not released.wait(timeout=0.2), "recv released before matching send"
            ext.send_payload("ping")
            assert released.wait(timeout=1), "recv never released after matching send"
        finally:
            thread.join(timeout=1)


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

    async def test_next_payload_blocks_until_matching_send(self) -> None:
        s = WebSocketSession(uri="ws://x", handshake_recorded_at="", protocol=None)
        s.frames = [
            Frame(direction="send", type="text", payload="ping"),
            Frame(direction="recv", type="text", payload="pong"),
        ]
        ext = AsyncFakeExtension(s)
        reader_task = asyncio.create_task(ext.next_payload())

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(reader_task), timeout=0.2)

        await ext.send_payload("ping")
        assert await asyncio.wait_for(reader_task, timeout=1) == "pong"


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
        resp.raw.extension.send_payload("ping")
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
        with (
            pytest.raises(KeyError, match="No recorded WS session"),
            Cassette(path=path, record_mode=RecordMode.NONE),
        ):
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
    def test_sync_recording_writes_cassette(self, cassette_dir: Path, echo_ws_server: str) -> None:
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
        self,
        cassette_dir: Path,
        echo_ws_server: str,
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
        self,
        cassette_dir: Path,
        echo_ws_server: str,
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
        resp.raw.extension.send_payload("record-me")
        assert resp.raw.extension.next_payload() == "record-me"

    async def test_async_recording_writes_cassette(
        self,
        cassette_dir: Path,
        echo_ws_server: str,
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


# ── Id-aware replay gating (order-tolerant, opt-in) ───────────────────────────


def _msg(id_: str, **extra: Any) -> str:
    return json.dumps({"id": id_, **extra})


class TestIdAwareGating:
    def _out_of_order_session(self) -> WebSocketSession:
        # Recorded order: send 1, send 2, recv for 2, recv for 1 — but a live
        # client may send 2 before 1 (e.g. concurrent in-flight requests).
        s = WebSocketSession(uri="ws://x", handshake_recorded_at="", protocol=None)
        s.frames = [
            Frame(direction="send", type="text", payload=_msg("1")),
            Frame(direction="send", type="text", payload=_msg("2")),
            Frame(direction="recv", type="text", payload=_msg("2", result="B")),
            Frame(direction="recv", type="text", payload=_msg("1", result="A")),
        ]
        return s

    def test_id_gate_tolerates_out_of_order_sends(self) -> None:
        s = self._out_of_order_session()
        s.id_extractor = dotted_json_id_extractor("id")
        ext = FakeExtension(s)

        released: list[str] = []
        first_released = threading.Event()

        def reader() -> None:
            while (raw := ext.next_payload()) is not None:
                released.append(json.loads(raw)["result"])
                first_released.set()

        thread = threading.Thread(target=reader)
        thread.start()

        # Live client sends id=2 first — reversed from recorded order.
        ext.send_payload(_msg("2"))
        assert first_released.wait(timeout=1)
        assert released == ["B"], "recv for id=2 should release once id=2 is sent, in any order"

        ext.send_payload(_msg("1"))
        thread.join(timeout=1)
        assert released == ["B", "A"]

    async def test_async_id_gate_tolerates_out_of_order_sends(self) -> None:
        s = self._out_of_order_session()
        s.id_extractor = dotted_json_id_extractor("id")
        ext = AsyncFakeExtension(s)

        reader_task = asyncio.create_task(ext.next_payload())
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(reader_task), timeout=0.2)

        # Live client sends id=2 first — reversed from recorded order — and
        # the recv for id=2 (queued first in the log) should release immediately.
        await ext.send_payload(_msg("2"))
        assert json.loads(await asyncio.wait_for(reader_task, timeout=1))["result"] == "B"

        await ext.send_payload(_msg("1"))
        assert await ext.next_payload() == _msg("1", result="A")

    def test_unresolvable_id_falls_back_to_position_gate(self) -> None:
        # Valid JSON, but no "id" key — a legitimate miss (e.g. a message type
        # that doesn't carry a correlation id), not a malformed payload.
        s = WebSocketSession(uri="ws://x", handshake_recorded_at="", protocol=None)
        s.id_extractor = dotted_json_id_extractor("id")
        s.frames = [
            Frame(direction="send", type="text", payload=json.dumps({"op": "no id here"})),
            Frame(direction="recv", type="text", payload=json.dumps({"note": "no id here"})),
        ]
        ext = FakeExtension(s)
        released = threading.Event()

        def reader() -> None:
            ext.next_payload()
            released.set()

        thread = threading.Thread(target=reader)
        thread.start()
        try:
            assert not released.wait(timeout=0.2)
            ext.send_payload(json.dumps({"op": "no id here"}))
            assert released.wait(timeout=1)
        finally:
            thread.join(timeout=1)

    def test_cassette_threads_string_path_extractor_to_replay(self, cassette_dir: Path) -> None:
        frames = [
            {"direction": "send", "type": "text", "payload": _msg("1")},
            {"direction": "send", "type": "text", "payload": _msg("2")},
            {"direction": "recv", "type": "text", "payload": _msg("2", result="B")},
            {"direction": "recv", "type": "text", "payload": _msg("1", result="A")},
        ]
        path = _ws_cassette(
            cassette_dir,
            "ws_id_aware",
            [_ws_session_dict("ws://example.com/chat", frames)],
        )
        with Cassette(path=path, record_mode=RecordMode.NONE, ws_id_extractor="id"):
            resp = niquests.Session().get("ws://example.com/chat")
        ext = resp.raw.extension
        ext.send_payload(_msg("2"))
        assert json.loads(ext.next_payload())["result"] == "B"
        ext.send_payload(_msg("1"))
        assert json.loads(ext.next_payload())["result"] == "A"

    def test_cassette_threads_callable_extractor_to_replay(self, cassette_dir: Path) -> None:
        frames = [
            {"direction": "send", "type": "text", "payload": _msg("x")},
            {"direction": "recv", "type": "text", "payload": _msg("x", result="ok")},
        ]
        path = _ws_cassette(
            cassette_dir,
            "ws_id_aware_callable",
            [_ws_session_dict("ws://example.com/chat", frames)],
        )
        extractor = dotted_json_id_extractor("id")
        with Cassette(path=path, record_mode=RecordMode.NONE, ws_id_extractor=extractor):
            resp = niquests.Session().get("ws://example.com/chat")
        ext = resp.raw.extension
        ext.send_payload(_msg("x"))
        assert json.loads(ext.next_payload())["result"] == "ok"
