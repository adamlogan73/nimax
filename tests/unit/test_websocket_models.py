"""Unit tests for Frame and WebSocketSession data models."""

from __future__ import annotations

from nimax._websocket import Frame, WebSocketSession

# ── Frame ─────────────────────────────────────────────────────────────────────


class TestFrame:
    def test_to_dict_basic(self) -> None:
        f = Frame(direction="recv", type="text", payload="hello", offset_ms=100)
        assert f.to_dict() == {
            "direction": "recv",
            "type": "text",
            "payload": "hello",
            "offset_ms": 100,
        }

    def test_to_dict_includes_close_fields_when_set(self) -> None:
        f = Frame(
            direction="recv",
            type="close",
            payload=None,
            offset_ms=0,
            close_code=1000,
            close_reason="Normal",
        )
        d = f.to_dict()
        assert d["close_code"] == 1000
        assert d["close_reason"] == "Normal"

    def test_to_dict_omits_none_close_fields(self) -> None:
        f = Frame(direction="send", type="text", payload="hi")
        d = f.to_dict()
        assert "close_code" not in d
        assert "close_reason" not in d

    def test_from_dict_full(self) -> None:
        d = {"direction": "recv", "type": "text", "payload": "hello", "offset_ms": 50}
        f = Frame.from_dict(d)
        assert f.direction == "recv"
        assert f.type == "text"
        assert f.payload == "hello"
        assert f.offset_ms == 50

    def test_from_dict_defaults(self) -> None:
        f = Frame.from_dict({"direction": "recv", "payload": "x"})
        assert f.type == "text"
        assert f.offset_ms == 0
        assert f.close_code is None
        assert f.close_reason is None

    def test_round_trip(self) -> None:
        f = Frame(direction="send", type="binary", payload="abc", offset_ms=10)
        assert Frame.from_dict(f.to_dict()) == f


# ── WebSocketSession ──────────────────────────────────────────────────────────


class TestWebSocketSession:
    def _session(self) -> WebSocketSession:
        s = WebSocketSession(
            uri="ws://example.com/chat",
            handshake_recorded_at="2026-01-01T00:00:00Z",
            protocol=None,
        )
        s.frames = [
            Frame(direction="send", type="text", payload="q1"),
            Frame(direction="recv", type="text", payload="r1"),
            Frame(direction="send", type="text", payload="q2"),
            Frame(direction="recv", type="text", payload="r2"),
        ]
        return s

    def test_claim_first_time_returns_true(self) -> None:
        s = WebSocketSession(uri="ws://x", handshake_recorded_at="", protocol=None)
        assert s.claim() is True

    def test_claim_second_time_returns_false(self) -> None:
        s = WebSocketSession(uri="ws://x", handshake_recorded_at="", protocol=None)
        s.claim()
        assert s.claim() is False

    def test_next_recv_skips_send_frames(self) -> None:
        s = self._session()
        f = s.next_recv_frame()
        assert f is not None
        assert f.payload == "r1"

    def test_next_recv_advances_cursor(self) -> None:
        s = self._session()
        s.next_recv_frame()
        f = s.next_recv_frame()
        assert f is not None
        assert f.payload == "r2"

    def test_next_recv_returns_none_when_exhausted(self) -> None:
        s = self._session()
        s.next_recv_frame()
        s.next_recv_frame()
        assert s.next_recv_frame() is None

    def test_next_recv_empty_session(self) -> None:
        s = WebSocketSession(uri="ws://x", handshake_recorded_at="", protocol=None)
        assert s.next_recv_frame() is None

    def test_to_dict_round_trip(self) -> None:
        s = self._session()
        s2 = WebSocketSession.from_dict(s.to_dict())
        assert s2.uri == s.uri
        assert s2.handshake_recorded_at == s.handshake_recorded_at
        assert len(s2.frames) == len(s.frames)
        assert s2.frames[1].payload == "r1"

    def test_from_dict_legacy_url_key(self) -> None:
        d = {
            "url": "ws://legacy.example.com",
            "handshake_recorded_at": "",
            "protocol": None,
            "frames": [],
        }
        s = WebSocketSession.from_dict(d)
        assert s.uri == "ws://legacy.example.com"

    def test_from_dict_uri_preferred_over_url(self) -> None:
        d = {
            "uri": "ws://current.example.com",
            "url": "ws://old.example.com",
            "handshake_recorded_at": "",
            "protocol": None,
            "frames": [],
        }
        s = WebSocketSession.from_dict(d)
        assert s.uri == "ws://current.example.com"

    def test_frame_defaults_applied_on_load(self) -> None:
        d = {
            "uri": "ws://x",
            "handshake_recorded_at": "",
            "protocol": None,
            "frames": [{"direction": "recv", "payload": "hi"}],
        }
        s = WebSocketSession.from_dict(d)
        assert s.frames[0].type == "text"
        assert s.frames[0].offset_ms == 0
