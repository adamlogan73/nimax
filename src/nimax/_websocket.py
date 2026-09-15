"""WebSocket frame model and extension proxy classes."""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


@dataclass
class Frame:
    """A single recorded WebSocket frame."""

    direction: str  # "send" | "recv"
    type: str  # "text" | "binary" | "ping" | "pong" | "close"
    payload: str | None
    offset_ms: int = 0
    close_code: int | None = None
    close_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "direction": self.direction,
            "type": self.type,
            "payload": self.payload,
            "offset_ms": self.offset_ms,
        }
        if self.close_code is not None:
            d["close_code"] = self.close_code
        if self.close_reason is not None:
            d["close_reason"] = self.close_reason
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Frame:
        return cls(
            direction=d["direction"],
            type=d.get("type", "text"),
            payload=d.get("payload"),
            offset_ms=d.get("offset_ms", 0),
            close_code=d.get("close_code"),
            close_reason=d.get("close_reason"),
        )


@dataclass
class WebSocketSession:
    """Recorded WebSocket session: metadata + ordered frame sequence."""

    uri: str
    handshake_recorded_at: str
    protocol: str | None
    frames: list[Frame] = field(default_factory=list)
    _cursor: int = field(default=0, init=False, repr=False)
    # True once a replay consumer has claimed this session so subsequent
    # connections to the same URI get the next unclaimed session.
    _claimed: bool = field(default=False, init=False, repr=False)
    uri_path: str = field(default="", init=False, repr=False)
    # Replay gating state: total number of real send_payload() calls observed
    # so far (_sent_count), versus how many "send" frames the cursor has
    # walked past in the recorded log (_required_sends_seen). A recv frame may
    # only be released once _sent_count catches up to _required_sends_seen as
    # of that frame's position — mirroring a real socket, which can't deliver
    # a response before its triggering request went out.
    _sent_count: int = field(default=0, init=False, repr=False)
    _required_sends_seen: int = field(default=0, init=False, repr=False)
    _sync_cond: threading.Condition = field(
        default_factory=threading.Condition,
        init=False,
        repr=False,
    )
    _async_cond: asyncio.Condition = field(
        default_factory=asyncio.Condition,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        self.uri_path = urlparse(self.uri).path

    def claim(self) -> bool:
        """Mark session as claimed. Returns True if it was unclaimed."""
        if self._claimed:
            return False
        self._claimed = True
        return True

    def next_recv_frame(self) -> Frame | None:
        """Return the next unplayed recv-direction frame, advancing the cursor."""
        with self._sync_cond:
            while self._cursor < len(self.frames):
                frame = self.frames[self._cursor]
                self._cursor += 1
                if frame.direction == "recv":
                    return frame
            return None

    def _next_recv_frame_with_requirement(self) -> tuple[Frame, int] | None:
        """Advance the cursor to the next recv frame, pairing it with the number
        of "send" frames recorded before it (the real-send count it must wait for)."""
        with self._sync_cond:
            while self._cursor < len(self.frames):
                frame = self.frames[self._cursor]
                self._cursor += 1
                if frame.direction == "send":
                    self._required_sends_seen += 1
                elif frame.direction == "recv":
                    return frame, self._required_sends_seen
            return None

    def mark_sent(self) -> None:
        """Record (sync) that a real send_payload() call has gone out."""
        with self._sync_cond:
            self._sent_count += 1
            self._sync_cond.notify_all()

    async def mark_sent_async(self) -> None:
        """Record (async) that a real send_payload() call has gone out."""
        async with self._async_cond:
            self._sent_count += 1
            self._async_cond.notify_all()

    def next_recv_frame_sync(self) -> Frame | None:
        """Like :meth:`next_recv_frame`, but blocks until enough real sends have
        happened, mirroring how a real socket can only deliver a response after
        its triggering request was sent."""
        result = self._next_recv_frame_with_requirement()
        if result is None:
            return None
        frame, required = result
        with self._sync_cond:
            while self._sent_count < required:
                self._sync_cond.wait()
        return frame

    async def next_recv_frame_async(self) -> Frame | None:
        """Async counterpart of :meth:`next_recv_frame_sync`."""
        result = self._next_recv_frame_with_requirement()
        if result is None:
            return None
        frame, required = result
        async with self._async_cond:
            while self._sent_count < required:
                await self._async_cond.wait()
        return frame

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "handshake_recorded_at": self.handshake_recorded_at,
            "protocol": self.protocol,
            "frames": [f.to_dict() for f in self.frames],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WebSocketSession:
        # "url" is the legacy key; "uri" is the current format
        uri = d.get("uri") or d.get("url", "")
        session = cls(
            uri=uri,
            handshake_recorded_at=d.get("handshake_recorded_at", ""),
            protocol=d.get("protocol"),
        )
        session.frames = [Frame.from_dict(f) for f in d.get("frames", [])]
        return session


# region Replay proxies


class FakeExtension:
    """Replays pre-recorded WebSocket recv frames for sync clients."""

    def __init__(self, session: WebSocketSession) -> None:
        self._session = session
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def next_payload(self) -> str | None:
        frame = self._session.next_recv_frame_sync()
        return frame.payload if frame is not None else None

    def send_payload(self, data: str) -> None:  # noqa: ARG002
        self._session.mark_sent()

    def close(self) -> None:
        self._closed = True


class AsyncFakeExtension:
    """Replays pre-recorded WebSocket recv frames for async clients."""

    def __init__(self, session: WebSocketSession) -> None:
        self._session = session
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def next_payload(self) -> str | None:
        frame = await self._session.next_recv_frame_async()
        return frame.payload if frame is not None else None

    async def send_payload(self, data: str) -> None:  # noqa: ARG002
        await self._session.mark_sent_async()

    async def close(self) -> None:
        self._closed = True


# endregion


# region Recording proxies


class RecordingExtension:
    """Wraps a live sync WS extension, recording every frame into *session*."""

    def __init__(self, real: Any, session: WebSocketSession, start: float) -> None:
        self._real = real
        self._session = session
        self._start = start

    @property
    def closed(self) -> bool:
        return self._real.closed

    def next_payload(self) -> str | None:
        raw = self._real.next_payload()
        if raw is not None:
            payload = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
            self._session.frames.append(
                Frame(
                    direction="recv",
                    type="text",
                    payload=payload,
                    offset_ms=_elapsed_ms(self._start),
                ),
            )
        return raw

    def send_payload(self, data: str) -> None:
        self._session.frames.append(
            Frame(
                direction="send",
                type="text",
                payload=data,
                offset_ms=_elapsed_ms(self._start),
            ),
        )
        self._real.send_payload(data)

    def close(self) -> None:
        self._real.close()


class AsyncRecordingExtension:
    """Wraps a live async WS extension, recording every frame into *session*."""

    def __init__(self, real: Any, session: WebSocketSession, start: float) -> None:
        self._real = real
        self._session = session
        self._start = start

    @property
    def closed(self) -> bool:
        return self._real.closed

    async def next_payload(self) -> str | None:
        raw = await self._real.next_payload()
        if raw is not None:
            payload = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
            self._session.frames.append(
                Frame(
                    direction="recv",
                    type="text",
                    payload=payload,
                    offset_ms=_elapsed_ms(self._start),
                ),
            )
        return raw

    async def send_payload(self, data: str) -> None:
        self._session.frames.append(
            Frame(
                direction="send",
                type="text",
                payload=data,
                offset_ms=_elapsed_ms(self._start),
            ),
        )
        await self._real.send_payload(data)

    async def close(self) -> None:
        await self._real.close()


# endregion
