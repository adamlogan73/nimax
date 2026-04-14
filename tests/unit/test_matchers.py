"""Unit tests for all eight built-in matchers."""

from __future__ import annotations

from nimax._matchers import (
    BUILTIN_MATCHERS,
    BodyMatcher,
    HeadersMatcher,
    HostMatcher,
    MethodMatcher,
    PathMatcher,
    ProtocolMatcher,
    QueryMatcher,
    URIMatcher,
)
from tests._utils import FakeRequest

# ── MethodMatcher ─────────────────────────────────────────────────────────────


class TestMethodMatcher:
    def setup_method(self) -> None:
        self.m = MethodMatcher()

    def test_match(self) -> None:
        assert self.m.match({"method": "GET"}, FakeRequest(method="GET"))

    def test_no_match(self) -> None:
        assert not self.m.match({"method": "POST"}, FakeRequest(method="GET"))

    def test_case_insensitive_recorded(self) -> None:
        assert self.m.match({"method": "get"}, FakeRequest(method="GET"))

    def test_case_insensitive_live(self) -> None:
        assert self.m.match({"method": "GET"}, FakeRequest(method="get"))

    def test_missing_recorded_method(self) -> None:
        # Empty recorded method should not match a real method
        assert not self.m.match({}, FakeRequest(method="GET"))


# ── URIMatcher ────────────────────────────────────────────────────────────────


class TestURIMatcher:
    def setup_method(self) -> None:
        self.m = URIMatcher()

    def test_exact_match(self) -> None:
        uri = "https://example.com/api?q=1"
        assert self.m.match({"uri": uri}, FakeRequest(url=uri))

    def test_no_match_different_query(self) -> None:
        assert not self.m.match(
            {"uri": "https://example.com/api?q=1"},
            FakeRequest(url="https://example.com/api?q=2"),
        )

    def test_no_match_different_path(self) -> None:
        assert not self.m.match(
            {"uri": "https://example.com/a"},
            FakeRequest(url="https://example.com/b"),
        )


# ── HostMatcher ───────────────────────────────────────────────────────────────


class TestHostMatcher:
    def setup_method(self) -> None:
        self.m = HostMatcher()

    def test_same_host(self) -> None:
        assert self.m.match(
            {"uri": "https://example.com/a"},
            FakeRequest(url="https://example.com/b"),
        )

    def test_different_host(self) -> None:
        assert not self.m.match(
            {"uri": "https://example.com/api"},
            FakeRequest(url="https://other.com/api"),
        )

    def test_port_in_host(self) -> None:
        assert self.m.match(
            {"uri": "http://localhost:8080/a"},
            FakeRequest(url="http://localhost:8080/b"),
        )

    def test_different_ports(self) -> None:
        assert not self.m.match(
            {"uri": "http://localhost:8080/a"},
            FakeRequest(url="http://localhost:9090/a"),
        )


# ── PathMatcher ───────────────────────────────────────────────────────────────


class TestPathMatcher:
    def setup_method(self) -> None:
        self.m = PathMatcher()

    def test_same_path_different_host(self) -> None:
        assert self.m.match(
            {"uri": "https://example.com/api/v1"},
            FakeRequest(url="https://other.com/api/v1"),
        )

    def test_same_path_different_query(self) -> None:
        assert self.m.match(
            {"uri": "https://example.com/api?a=1"},
            FakeRequest(url="https://example.com/api?b=2"),
        )

    def test_different_path(self) -> None:
        assert not self.m.match(
            {"uri": "https://example.com/api/v1"},
            FakeRequest(url="https://example.com/api/v2"),
        )


# ── QueryMatcher ──────────────────────────────────────────────────────────────


class TestQueryMatcher:
    def setup_method(self) -> None:
        self.m = QueryMatcher()

    def test_same_params(self) -> None:
        assert self.m.match(
            {"uri": "https://example.com/?a=1&b=2"},
            FakeRequest(url="https://example.com/?b=2&a=1"),
        )

    def test_different_values(self) -> None:
        assert not self.m.match(
            {"uri": "https://example.com/?a=1"},
            FakeRequest(url="https://example.com/?a=2"),
        )

    def test_missing_param(self) -> None:
        assert not self.m.match(
            {"uri": "https://example.com/?a=1&b=2"},
            FakeRequest(url="https://example.com/?a=1"),
        )

    def test_no_query_string(self) -> None:
        assert self.m.match(
            {"uri": "https://example.com/api"},
            FakeRequest(url="https://example.com/api"),
        )


# ── HeadersMatcher ────────────────────────────────────────────────────────────


class TestHeadersMatcher:
    def setup_method(self) -> None:
        self.m = HeadersMatcher()

    def test_all_recorded_headers_present(self) -> None:
        assert self.m.match(
            {"headers": {"Content-Type": ["application/json"]}},
            FakeRequest(headers={"Content-Type": "application/json", "X-Extra": "yes"}),
        )

    def test_missing_header_in_live(self) -> None:
        assert not self.m.match(
            {"headers": {"Authorization": ["Bearer tok"]}},
            FakeRequest(headers={}),
        )

    def test_wrong_header_value(self) -> None:
        assert not self.m.match(
            {"headers": {"Accept": ["application/json"]}},
            FakeRequest(headers={"Accept": "text/html"}),
        )

    def test_empty_recorded_headers_always_match(self) -> None:
        assert self.m.match({"headers": {}}, FakeRequest(headers={}))
        assert self.m.match({"headers": None}, FakeRequest(headers={}))


# ── BodyMatcher ───────────────────────────────────────────────────────────────


class TestBodyMatcher:
    def setup_method(self) -> None:
        self.m = BodyMatcher()

    def test_both_none(self) -> None:
        assert self.m.match({"body": None}, FakeRequest(body=None))

    def test_string_match(self) -> None:
        assert self.m.match({"body": "hello"}, FakeRequest(body="hello"))

    def test_bytes_decoded_for_comparison(self) -> None:
        assert self.m.match({"body": "hello"}, FakeRequest(body=b"hello"))

    def test_mismatch(self) -> None:
        assert not self.m.match({"body": "hello"}, FakeRequest(body="world"))

    def test_none_vs_string(self) -> None:
        assert not self.m.match({"body": None}, FakeRequest(body="data"))


# ── ProtocolMatcher ───────────────────────────────────────────────────────────


class TestProtocolMatcher:
    def test_always_true(self) -> None:
        # Protocol cannot be determined from PreparedRequest; always passes at
        # match-time and is validated post-response.
        m = ProtocolMatcher()
        assert m.match({"protocol": "HTTP/2"}, FakeRequest())
        assert m.match({}, FakeRequest())


# ── Registry ─────────────────────────────────────────────────────────────────


def test_builtin_matchers_registry() -> None:
    expected = {"method", "uri", "host", "path", "query", "headers", "body", "protocol"}
    assert set(BUILTIN_MATCHERS.keys()) == expected


def test_matcher_name_attribute() -> None:
    for name, cls in BUILTIN_MATCHERS.items():
        assert cls.name == name
