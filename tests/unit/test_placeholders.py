"""Unit tests for Placeholder, apply_placeholders, and restore_placeholders."""

from __future__ import annotations

import dataclasses

import pytest

from nimax import Placeholder
from nimax._placeholders import apply_placeholders, restore_placeholders


def test_apply_replaces_real_value() -> None:
    p = Placeholder(placeholder="<TOKEN>", replace="supersecret")
    result = apply_placeholders("Authorization: Bearer supersecret", [p])
    assert result == "Authorization: Bearer <TOKEN>"


def test_restore_replaces_placeholder() -> None:
    p = Placeholder(placeholder="<TOKEN>", replace="supersecret")
    result = restore_placeholders("Authorization: Bearer <TOKEN>", [p])
    assert result == "Authorization: Bearer supersecret"


def test_round_trip() -> None:
    text = "key=mysecretkey&other=value"
    p = Placeholder(placeholder="<KEY>", replace="mysecretkey")
    assert restore_placeholders(apply_placeholders(text, [p]), [p]) == text


def test_multiple_placeholders() -> None:
    text = "user=alice&token=abc123"
    placeholders = [
        Placeholder(placeholder="<USER>", replace="alice"),
        Placeholder(placeholder="<TOKEN>", replace="abc123"),
    ]
    assert apply_placeholders(text, placeholders) == "user=<USER>&token=<TOKEN>"


def test_no_occurrence_is_noop() -> None:
    text = "nothing-to-replace"
    p = Placeholder(placeholder="<X>", replace="missing")
    assert apply_placeholders(text, [p]) == text


def test_empty_placeholders_list_is_noop() -> None:
    text = "original"
    assert apply_placeholders(text, []) == text
    assert restore_placeholders(text, []) == text


def test_placeholder_is_frozen() -> None:
    p = Placeholder(placeholder="<X>", replace="y")
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.placeholder = "changed"  # type: ignore[misc]
