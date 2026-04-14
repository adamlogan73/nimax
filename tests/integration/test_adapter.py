"""Integration tests for NimaxRecorder (programmatic API)."""

from __future__ import annotations

import json
from pathlib import Path

import niquests
import pytest

from nimax import NimaxRecorder
from nimax import RecordMode
from nimax._matchers import BaseMatcher
from nimax._serializers import BaseSerializer
from nimax._serializers import JSONSerializer
from tests._utils import minimal_cassette
from tests._utils import write_cassette


def _pre_recorded(cassette_dir: Path, name: str = "x") -> Path:
    """Write a minimal cassette and return its path."""
    path = cassette_dir / f"{name}.json"
    write_cassette(path, minimal_cassette())
    return path


# ── register_matcher ──────────────────────────────────────────────────────────


class TestRegisterMatcher:
    def test_registers_by_name(self) -> None:
        class AlwaysMatcher(BaseMatcher):
            name = "always_true"

            def match(self, recorded: dict, live: object) -> bool:
                return True

        NimaxRecorder.register_matcher(AlwaysMatcher)
        assert "always_true" in NimaxRecorder._matchers
        del NimaxRecorder._matchers["always_true"]

    def test_overrides_existing_matcher(self) -> None:
        original = NimaxRecorder._matchers["method"]

        class PermissiveMethod(BaseMatcher):
            name = "method"

            def match(self, recorded: dict, live: object) -> bool:
                return True

        NimaxRecorder.register_matcher(PermissiveMethod)
        assert NimaxRecorder._matchers["method"] is PermissiveMethod
        NimaxRecorder._matchers["method"] = original  # restore


# ── register_serializer ───────────────────────────────────────────────────────


class TestRegisterSerializer:
    def test_registers_by_extension(self) -> None:
        class TxtSerializer(BaseSerializer):
            extension = "txt"

            def serialize(self, data: dict) -> str:
                return str(data)

            def deserialize(self, raw: str) -> dict:
                return {}

        NimaxRecorder.register_serializer(TxtSerializer)
        assert "txt" in NimaxRecorder._serializers
        del NimaxRecorder._serializers["txt"]


# ── use_cassette context manager ──────────────────────────────────────────────


class TestUseCassette:
    def test_replays_pre_recorded_response(self, cassette_dir: Path) -> None:
        _pre_recorded(cassette_dir)
        session = niquests.Session()
        recorder = NimaxRecorder(session)
        with recorder.use_cassette("x", cassette_dir=cassette_dir, record_mode=RecordMode.ONCE):
            resp = session.get("https://example.com/api")
        assert resp.status_code == 200
        assert resp.text == '{"ok": true}'

    def test_session_send_patched_inside_context(self, cassette_dir: Path) -> None:
        _pre_recorded(cassette_dir)
        original_send = niquests.Session.send
        session = niquests.Session()
        recorder = NimaxRecorder(session)
        with recorder.use_cassette("x", cassette_dir=cassette_dir, record_mode=RecordMode.NONE):
            assert niquests.Session.send is not original_send
        assert niquests.Session.send is original_send

    def test_async_session_send_patched_inside_context(self, cassette_dir: Path) -> None:
        _pre_recorded(cassette_dir)
        original_send = niquests.AsyncSession.send
        session = niquests.AsyncSession()
        recorder = NimaxRecorder(session)
        with recorder.use_cassette("x", cassette_dir=cassette_dir, record_mode=RecordMode.NONE):
            assert niquests.AsyncSession.send is not original_send
        assert niquests.AsyncSession.send is original_send

    def test_default_serializer_is_json(self, cassette_dir: Path) -> None:
        _pre_recorded(cassette_dir)
        session = niquests.Session()
        recorder = NimaxRecorder(session)
        with recorder.use_cassette("x", cassette_dir=cassette_dir, record_mode=RecordMode.NONE) as cassette:
            assert isinstance(cassette._serializer, JSONSerializer)

    def test_cassette_path_derived_from_name_and_dir(self, cassette_dir: Path) -> None:
        _pre_recorded(cassette_dir, name="my_test")
        session = niquests.Session()
        recorder = NimaxRecorder(session)
        with recorder.use_cassette(
            "my_test",
            cassette_dir=cassette_dir,
            record_mode=RecordMode.NONE,
        ) as cassette:
            assert cassette._path == cassette_dir / "my_test.json"

    def test_cassette_dir_as_string(self, cassette_dir: Path) -> None:
        _pre_recorded(cassette_dir)
        session = niquests.Session()
        recorder = NimaxRecorder(session)
        with recorder.use_cassette(
            "x",
            cassette_dir=str(cassette_dir),
            record_mode=RecordMode.NONE,
        ) as cassette:
            assert cassette._path.parent == cassette_dir

    def test_yields_cassette_instance(self, cassette_dir: Path) -> None:
        from nimax._cassette import Cassette

        _pre_recorded(cassette_dir)
        session = niquests.Session()
        recorder = NimaxRecorder(session)
        with recorder.use_cassette("x", cassette_dir=cassette_dir, record_mode=RecordMode.NONE) as cassette:
            assert isinstance(cassette, Cassette)

    def test_session_property(self) -> None:
        session = niquests.Session()
        recorder = NimaxRecorder(session)
        assert recorder.session is session

    def test_async_replay(self, cassette_dir: Path) -> None:
        import asyncio

        _pre_recorded(cassette_dir)
        session = niquests.AsyncSession()
        recorder = NimaxRecorder(session)

        async def _run() -> niquests.Response:
            with recorder.use_cassette("x", cassette_dir=cassette_dir, record_mode=RecordMode.ONCE):
                return await session.get("https://example.com/api")

        resp = asyncio.run(_run())
        assert resp.status_code == 200
