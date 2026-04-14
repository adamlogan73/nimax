"""
Core cassette machinery: record and replay niquests HTTP and WebSocket interactions.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Self
from unittest.mock import patch
from urllib.parse import urlparse

import niquests
from niquests import AsyncSession, Response, Session
from niquests.structures import CaseInsensitiveDict

from ._matchers import BUILTIN_MATCHERS, BaseMatcher
from ._placeholders import Placeholder, apply_placeholders, restore_placeholders
from ._record_mode import RecordMode
from ._serializers import BUILTIN_SERIALIZERS, BaseSerializer, JSONSerializer
from ._websocket import (
    AsyncFakeExtension,
    AsyncRecordingExtension,
    FakeExtension,
    RecordingExtension,
    WebSocketSession,
)

if TYPE_CHECKING:
    from pathlib import Path

NIMAX_VERSION = "0.1.0"

#: Default matchers — path-only matching ignores dynamic query params.
DEFAULT_MATCH_ON: frozenset[str] = frozenset({"method", "path"})


# ── Helpers ───────────────────────────────────────────────────────────────────


_NO_WS_SESSION_MSG = "No recorded WS session for {url!r} — re-run with --record to update cassettes"
_NO_HTTP_RESPONSE_MSG = (
    "No recorded response for {method} {url!r} — re-run with --record to update cassettes"
)


def _is_ws(url: str) -> bool:
    return url.startswith(("ws://", "wss://"))


def _normalize_status(status: Any) -> dict[str, Any]:
    """Coerce status to ``{"code": int, "message": str}`` (handles legacy int form)."""
    if isinstance(status, dict):
        return status
    code = int(status)
    try:
        message = HTTPStatus(code).phrase
    except ValueError:
        message = ""
    return {"code": code, "message": message}


def _normalize_body(body: Any) -> dict[str, str] | None:
    """Coerce body to ``{"string": "..."}`` (handles legacy bare-string form)."""
    if body is None:
        return None
    if isinstance(body, dict):
        return body
    text = body if isinstance(body, str) else body.decode("utf-8", errors="replace")
    return {"string": text} if text else None


def _normalize_headers(headers: dict[str, Any]) -> dict[str, list[str]]:
    """Ensure all header values are lists of strings."""
    return {k: [v] if isinstance(v, str) else v for k, v in headers.items()}


def _migrate_interaction(entry: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a legacy cassette entry to the current format in place."""
    req = dict(entry["request"])
    resp = dict(entry["response"])

    if "url" in req and "uri" not in req:
        req["uri"] = req.pop("url")
    req.setdefault("headers", {})
    req.setdefault("body", None)

    resp["status"] = _normalize_status(resp.get("status", 200))
    resp["body"] = _normalize_body(resp.get("body"))
    resp["headers"] = _normalize_headers(resp.get("headers", {}))
    resp.setdefault("protocol", None)
    resp.setdefault("url", req.get("uri", ""))

    return {
        "request": req,
        "response": resp,
        "recorded_at": entry.get("recorded_at", ""),
    }


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class Interaction:
    request: dict[str, Any]
    response: dict[str, Any]
    recorded_at: str
    used: bool = field(default=False, compare=False)


# ── Fake raw (replay WebSocket) ───────────────────────────────────────────────


class _FakeRaw:
    """Minimal stand-in for urllib3's HTTPResponse exposing just the extension."""

    def __init__(self, extension: Any) -> None:
        self.extension = extension


# ── Cassette ──────────────────────────────────────────────────────────────────


class Cassette:
    """
    Context manager that intercepts ``niquests.Session.send`` and
    ``niquests.AsyncSession.send`` to record or replay HTTP and WebSocket
    interactions.

    :param path:         Path to the cassette file (extension determines serializer
                         when *serializer* is ``None``).
    :param record_mode:  :class:`RecordMode` controlling when recording is allowed.
                         Defaults to ``ONCE``.
    :param match_on:     Set of matcher names used to find stored interactions.
                         Defaults to ``{"method", "path"}``.
    :param serializer:   Explicit serializer instance.  When ``None``, inferred from
                         *path* extension (``.yaml`` → YAML, otherwise JSON).
    :param placeholders: List of :class:`Placeholder` objects for value sanitization.
    :param record:       Deprecated boolean shorthand.  ``True`` → ``RecordMode.ALL``,
                         ``False`` → ``RecordMode.NONE``.
    """

    def __init__(  # noqa: PLR0913
        self,
        path: Path,
        *,
        record_mode: RecordMode = RecordMode.ONCE,
        match_on: frozenset[str] = DEFAULT_MATCH_ON,
        serializer: BaseSerializer | None = None,
        placeholders: list[Placeholder] | None = None,
        record: bool | None = None,
        matcher_registry: dict[str, type[BaseMatcher]] | None = None,
        serializer_registry: dict[str, type[BaseSerializer]] | None = None,
    ) -> None:
        # Backward-compat shim
        if record is not None:
            record_mode = RecordMode.ALL if record else RecordMode.NONE

        _registry = matcher_registry if matcher_registry is not None else BUILTIN_MATCHERS
        _supported = frozenset(_registry.keys())
        unknown = match_on - _supported
        if unknown:
            msg = f"Unknown matcher(s): {unknown!r}. Supported: {_supported!r}"
            raise ValueError(msg)

        self._path = path
        self._record_mode = record_mode
        self._matchers: list[BaseMatcher] = [_registry[name]() for name in match_on]
        self._placeholders: list[Placeholder] = placeholders or []
        self._serializer_registry: dict[str, type[BaseSerializer]] = (
            serializer_registry if serializer_registry is not None else BUILTIN_SERIALIZERS
        )
        self._serializer: BaseSerializer = serializer or self._infer_serializer()

        self._interactions: list[Interaction] = []
        self._ws_sessions: list[WebSocketSession] = []
        self._lock = threading.Lock()
        self._patches: list[Any] = []

        # Determine whether this run is a recording pass
        if record_mode == RecordMode.NONE:
            self._recording_active = False
        elif record_mode == RecordMode.ONCE:
            self._recording_active = not path.exists()
        else:  # ALL, NEW_EPISODES
            self._recording_active = True

        # NEW_EPISODES loads existing interactions to replay them, but stays in
        # recording mode to capture unmatched requests. ALL skips loading entirely.
        if not self._recording_active or record_mode == RecordMode.NEW_EPISODES:
            self._load()

    # ── Serializer inference ──────────────────────────────────────────────────

    def _infer_serializer(self) -> BaseSerializer:
        ext = self._path.suffix.lstrip(".")
        cls = self._serializer_registry.get(ext)
        return cls() if cls is not None else JSONSerializer()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        if self._placeholders:
            raw = restore_placeholders(raw, self._placeholders)
        data: dict[str, Any] = self._serializer.deserialize(raw)

        # Support both legacy "interactions" key and current "http_interactions"
        interactions_raw = data.get("http_interactions") or data.get("interactions", [])
        for entry in interactions_raw:
            migrated = _migrate_interaction(entry)
            self._interactions.append(
                Interaction(
                    request=migrated["request"],
                    response=migrated["response"],
                    recorded_at=migrated["recorded_at"],
                ),
            )

        for ws_entry in data.get("websocket_sessions", []):
            # Migrate legacy frame format (direction + payload only)
            frames_raw = ws_entry.get("frames", [])
            for f in frames_raw:
                f.setdefault("type", "text")
                f.setdefault("offset_ms", 0)
            self._ws_sessions.append(WebSocketSession.from_dict(ws_entry))

    def save(self) -> None:
        if not self._recording_active:
            return

        with self._lock:
            http_interactions = [
                {
                    "request": i.request,
                    "response": i.response,
                    "recorded_at": i.recorded_at,
                }
                for i in self._interactions
            ]
            ws_sessions = [ws.to_dict() for ws in self._ws_sessions]

        data: dict[str, Any] = {
            "nimax_version": NIMAX_VERSION,
            "http_interactions": http_interactions,
            "websocket_sessions": ws_sessions,
        }
        serialized = self._serializer.serialize(data)
        if self._placeholders:
            serialized = apply_placeholders(serialized, self._placeholders)

        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        try:
            tmp.write_text(serialized, encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    # ── Matching & recording ──────────────────────────────────────────────────

    def find_match(self, live_request: Any) -> Interaction | None:
        """Return the first unused stored interaction that satisfies all matchers."""
        with self._lock:
            for interaction in self._interactions:
                if interaction.used:
                    continue
                if all(m.match(interaction.request, live_request) for m in self._matchers):
                    interaction.used = True
                    return interaction
        return None

    def save_interaction(self, method: str, url: str, resp: Response) -> None:
        """Append a recorded HTTP interaction."""
        body_str = resp.text if resp._content else ""  # noqa: SLF001
        try:
            message = HTTPStatus(resp.status_code).phrase
        except ValueError:
            message = ""
        headers = _normalize_headers(dict(resp.headers))
        now = datetime.now(tz=UTC).isoformat()
        interaction = Interaction(
            request={"method": method, "uri": url, "headers": {}, "body": None},
            response={
                "status": {"code": resp.status_code, "message": message},
                "headers": headers,
                "body": {"string": body_str} if body_str else None,
                "protocol": None,
                "url": url,
            },
            recorded_at=now,
        )
        with self._lock:
            self._interactions.append(interaction)

    def record_ws(self, url: str) -> tuple[WebSocketSession, float]:
        """Register a new WS session and return it with the monotonic start time."""
        now = datetime.now(tz=UTC).isoformat()
        session = WebSocketSession(
            uri=url,
            handshake_recorded_at=now,
            protocol=None,
        )
        with self._lock:
            self._ws_sessions.append(session)
        return session, time.monotonic()

    def find_ws_session(self, url: str) -> WebSocketSession | None:
        """Return the first unclaimed WS session matching *url* by path."""
        parsed_path = urlparse(url).path
        with self._lock:
            for ws in self._ws_sessions:
                if ws.uri_path == parsed_path and ws.claim():
                    return ws
        return None

    # ── Response construction ─────────────────────────────────────────────────

    def _build_response(self, interaction: Interaction, request: Any) -> Response:
        resp_data = interaction.response
        resp = Response()
        resp.status_code = resp_data["status"]["code"]
        headers_raw = resp_data.get("headers", {})
        resp.headers = CaseInsensitiveDict(
            {k: (v[0] if isinstance(v, list) else v) for k, v in headers_raw.items()},
        )
        body_data = resp_data.get("body")
        if body_data is None:
            resp._content = b""  # noqa: SLF001
        elif isinstance(body_data, dict):
            resp._content = body_data.get("string", "").encode("utf-8")  # noqa: SLF001
        else:
            resp._content = (  # noqa: SLF001
                body_data.encode("utf-8") if isinstance(body_data, str) else bytes(body_data)
            )
        resp._content_consumed = True  # noqa: SLF001
        resp.encoding = "utf-8"
        resp.url = resp_data.get("url", "")
        resp.request = request
        return resp

    def _ws_response(
        self,
        url: str,
        ws_session: WebSocketSession,
        *,
        is_async: bool,
    ) -> Response:
        ext: Any = AsyncFakeExtension(ws_session) if is_async else FakeExtension(ws_session)
        resp = Response()
        resp.status_code = 101
        resp._content = b""  # noqa: SLF001
        resp._content_consumed = True  # noqa: SLF001
        resp.headers = CaseInsensitiveDict({"Upgrade": "websocket"})
        resp.encoding = "utf-8"
        resp.url = url
        resp.raw = _FakeRaw(ext)
        return resp

    # ── Send interceptors ─────────────────────────────────────────────────────

    def _make_sync_send(self, original: Any) -> Any:
        cassette = self

        def send(session_self: Session, request: Any, **kwargs: Any) -> Response:
            url = request.url or ""
            method = request.method or "GET"

            if _is_ws(url):
                if cassette._recording_active:
                    resp = original(session_self, request, **kwargs)
                    if resp.extension is not None:
                        ws_session, start = cassette.record_ws(url)
                        resp.raw._extension = RecordingExtension(  # noqa: SLF001
                            resp.extension,
                            ws_session,
                            start,
                        )
                    return resp
                ws_session = cassette.find_ws_session(url)
                if ws_session is None:
                    raise KeyError(_NO_WS_SESSION_MSG.format(url=url))
                return cassette._ws_response(url, ws_session, is_async=False)

            match = cassette.find_match(request)
            if match is not None:
                return cassette._build_response(match, request)

            if cassette._recording_active:
                resp = original(session_self, request, **kwargs)
                cassette.save_interaction(method, url, resp)
                return resp

            raise KeyError(_NO_HTTP_RESPONSE_MSG.format(method=method, url=url))

        return send

    def _make_async_send(self, original: Any) -> Any:
        cassette = self

        async def send(
            session_self: AsyncSession,
            request: Any,
            **kwargs: Any,
        ) -> Response:
            url = request.url or ""
            method = request.method or "GET"

            if _is_ws(url):
                if cassette._recording_active:
                    resp = await original(session_self, request, **kwargs)
                    if resp.extension is not None:
                        ws_session, start = cassette.record_ws(url)
                        resp.raw._extension = AsyncRecordingExtension(  # noqa: SLF001
                            resp.extension,
                            ws_session,
                            start,
                        )
                    return resp
                ws_session = cassette.find_ws_session(url)
                if ws_session is None:
                    raise KeyError(_NO_WS_SESSION_MSG.format(url=url))
                return cassette._ws_response(url, ws_session, is_async=True)

            match = cassette.find_match(request)
            if match is not None:
                return cassette._build_response(match, request)

            if cassette._recording_active:
                resp = await original(session_self, request, **kwargs)
                cassette.save_interaction(method, url, resp)
                return resp

            raise KeyError(_NO_HTTP_RESPONSE_MSG.format(method=method, url=url))

        return send

    # ── Context manager ───────────────────────────────────────────────────────

    def _start_patching(self) -> None:
        sync_orig = niquests.Session.send
        async_orig = niquests.AsyncSession.send
        self._patches = [
            patch.object(niquests.Session, "send", self._make_sync_send(sync_orig)),
            patch.object(
                niquests.AsyncSession,
                "send",
                self._make_async_send(async_orig),
            ),
        ]
        for p in self._patches:
            p.start()

    def _stop_patching(self) -> None:
        for p in self._patches:
            p.stop()
        self._patches = []

    def __enter__(self) -> Self:
        self._start_patching()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop_patching()
        self.save()
