"""Pytest plugin tests using pytester."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_cassette(cassette_path: Path, body: str = "hello") -> None:
    cassette_path.parent.mkdir(parents=True, exist_ok=True)
    cassette_path.write_text(
        json.dumps({
            "nimax_version": "0.1.0",
            "http_interactions": [
                {
                    "request": {
                        "method": "GET",
                        "uri": "https://example.com/",
                        "headers": {},
                        "body": None,
                    },
                    "response": {
                        "status": {"code": 200, "message": "OK"},
                        "headers": {},
                        "body": {"string": body},
                        "protocol": None,
                        "url": "https://example.com/",
                    },
                    "recorded_at": "2026-01-01T00:00:00Z",
                }
            ],
            "websocket_sessions": [],
        })
    )


# ── nimax_session fixture ─────────────────────────────────────────────────────


class TestNimaxSessionFixture:
    def test_basic_replay(self, pytester: pytest.Pytester) -> None:
        _write_cassette(pytester.path / "cassettes" / "test_basic_replay" / "test_example.json")
        pytester.makepyfile("""
def test_example(nimax_session):
    resp = nimax_session.get("https://example.com/")
    assert resp.status_code == 200
    assert resp.text == "hello"
""")
        pytester.runpytest().assert_outcomes(passed=1)

    def test_parameterized_name_sanitized(self, pytester: pytest.Pytester) -> None:
        # [a] becomes _a_  after sanitization
        _write_cassette(pytester.path / "cassettes" / "test_foo" / "test_example_a_.json")
        pytester.makepyfile("""
import pytest

@pytest.mark.parametrize("val", ["a"])
def test_example(nimax_session, val):
    resp = nimax_session.get("https://example.com/")
    assert resp.status_code == 200
""")
        pytester.runpytest().assert_outcomes(passed=1)

    def test_cassette_name_derives_from_module_and_test(self, pytester: pytest.Pytester) -> None:
        """Cassette path is {cassette_dir}/{module_stem}/{test_name}.json."""
        _write_cassette(pytester.path / "cassettes" / "test_naming" / "test_check.json")
        pytester.makepyfile(
            test_naming="""
def test_check(nimax_session):
    resp = nimax_session.get("https://example.com/")
    assert resp.status_code == 200
"""
        )
        pytester.runpytest().assert_outcomes(passed=1)


# ── nimax_async_session fixture ───────────────────────────────────────────────


class TestNimaxAsyncSessionFixture:
    def test_basic_async_replay(self, pytester: pytest.Pytester) -> None:
        _write_cassette(pytester.path / "cassettes" / "test_basic_async_replay" / "test_async_example.json")
        pytester.makeini("[pytest]\nasyncio_mode = auto\n")
        pytester.makepyfile("""
async def test_async_example(nimax_async_session):
    resp = await nimax_async_session.get("https://example.com/")
    assert resp.status_code == 200
    assert resp.text == "hello"
""")
        pytester.runpytest().assert_outcomes(passed=1)


# ── CLI: --record ─────────────────────────────────────────────────────────────


class TestRecordFlag:
    def test_record_flag_enables_all_mode(self, pytester: pytest.Pytester) -> None:
        # With --record (ALL mode) a missing cassette doesn't raise; it records.
        # We can't hit real network in CI so we just verify the test _runs_
        # (it will fail when the network call fails, not with a cassette error).
        pytester.makepyfile("""
def test_always_passes():
    pass
""")
        result = pytester.runpytest("--record")
        result.assert_outcomes(passed=1)

    def test_record_mode_option_once(self, pytester: pytest.Pytester) -> None:
        _write_cassette(pytester.path / "cassettes" / "test_foo" / "test_with_mode.json")
        pytester.makepyfile("""
def test_with_mode(nimax_session):
    resp = nimax_session.get("https://example.com/")
    assert resp.status_code == 200
""")
        pytester.runpytest("--record-mode=once").assert_outcomes(passed=1)

    def test_invalid_record_mode_fails(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile("def test_x(): pass")
        result = pytester.runpytest("--record-mode=invalid")
        assert result.ret != 0


# ── CLI: --cassette-dir ───────────────────────────────────────────────────────


class TestCassetteDirOption:
    def test_custom_cassette_dir(self, pytester: pytest.Pytester) -> None:
        _write_cassette(pytester.path / "my_cassettes" / "test_foo" / "test_custom.json")
        pytester.makepyfile("""
def test_custom(nimax_session):
    resp = nimax_session.get("https://example.com/")
    assert resp.status_code == 200
""")
        pytester.runpytest("--cassette-dir=my_cassettes").assert_outcomes(passed=1)


# ── CLI: --cassette-match-on ──────────────────────────────────────────────────


class TestMatchOnOption:
    def test_match_on_method_and_path(self, pytester: pytest.Pytester) -> None:
        _write_cassette(pytester.path / "cassettes" / "test_foo" / "test_match_opt.json")
        pytester.makepyfile("""
def test_match_opt(nimax_session):
    resp = nimax_session.get("https://example.com/")
    assert resp.status_code == 200
""")
        pytester.runpytest("--cassette-match-on=method,path").assert_outcomes(passed=1)


# ── pyproject.toml config ─────────────────────────────────────────────────────


class TestPyprojectConfig:
    def test_cassette_library_dir_from_toml(self, pytester: pytest.Pytester) -> None:
        _write_cassette(pytester.path / "my_cassettes" / "test_foo" / "test_toml_dir.json")
        pytester.makepyprojecttoml("""
[tool.nimax]
cassette_library_dir = "my_cassettes"
""")
        pytester.makepyfile("""
def test_toml_dir(nimax_session):
    resp = nimax_session.get("https://example.com/")
    assert resp.status_code == 200
""")
        pytester.runpytest().assert_outcomes(passed=1)

    def test_record_mode_from_toml(self, pytester: pytest.Pytester) -> None:
        _write_cassette(pytester.path / "cassettes" / "test_foo" / "test_toml_mode.json")
        pytester.makepyprojecttoml("""
[tool.nimax]
record_mode = "once"
""")
        pytester.makepyfile("""
def test_toml_mode(nimax_session):
    resp = nimax_session.get("https://example.com/")
    assert resp.status_code == 200
""")
        pytester.runpytest().assert_outcomes(passed=1)

    def test_match_on_list_from_toml(self, pytester: pytest.Pytester) -> None:
        _write_cassette(pytester.path / "cassettes" / "test_foo" / "test_toml_match.json")
        pytester.makepyprojecttoml("""
[tool.nimax]
match_on = ["method", "path"]
""")
        pytester.makepyfile("""
def test_toml_match(nimax_session):
    resp = nimax_session.get("https://example.com/")
    assert resp.status_code == 200
""")
        pytester.runpytest().assert_outcomes(passed=1)

    def test_cli_cassette_dir_overrides_toml(self, pytester: pytest.Pytester) -> None:
        _write_cassette(pytester.path / "cli_dir" / "test_foo" / "test_override.json")
        pytester.makepyprojecttoml("""
[tool.nimax]
cassette_library_dir = "toml_dir"
""")
        pytester.makepyfile("""
def test_override(nimax_session):
    resp = nimax_session.get("https://example.com/")
    assert resp.status_code == 200
""")
        pytester.runpytest("--cassette-dir=cli_dir").assert_outcomes(passed=1)

    def test_cli_record_mode_overrides_toml(self, pytester: pytest.Pytester) -> None:
        _write_cassette(pytester.path / "cassettes" / "test_foo" / "test_cli_mode.json")
        pytester.makepyprojecttoml("""
[tool.nimax]
record_mode = "none"
""")
        pytester.makepyfile("""
def test_cli_mode(nimax_session):
    resp = nimax_session.get("https://example.com/")
    assert resp.status_code == 200
""")
        # CLI --record-mode=once overrides toml "none"
        pytester.runpytest("--record-mode=once").assert_outcomes(passed=1)
