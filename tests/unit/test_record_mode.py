"""Unit tests for RecordMode enum."""

from __future__ import annotations

import pytest

from nimax import RecordMode


def test_values() -> None:
    assert RecordMode.NONE.value == "none"
    assert RecordMode.ONCE.value == "once"
    assert RecordMode.NEW_EPISODES.value == "new_episodes"
    assert RecordMode.ALL.value == "all"


def test_is_str_subclass() -> None:
    # RecordMode(str, Enum) — members compare equal to their string values
    assert RecordMode.ONCE == "once"
    assert RecordMode.ALL == "all"


def test_from_string() -> None:
    assert RecordMode("once") is RecordMode.ONCE
    assert RecordMode("all") is RecordMode.ALL
    assert RecordMode("new_episodes") is RecordMode.NEW_EPISODES
    assert RecordMode("none") is RecordMode.NONE


def test_invalid_value_raises() -> None:
    with pytest.raises(ValueError, match="record"):
        RecordMode("record")


def test_all_members_covered() -> None:
    assert len(RecordMode) == 4
