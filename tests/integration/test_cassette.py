"""Integration tests for Cassette: record modes, persistence, matching, thread safety."""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING, Any

import niquests
import pytest

from nimax import Placeholder, RecordMode
from nimax._cassette import Cassette
from tests._utils import FakeRequest, fake_response, minimal_cassette, write_cassette

if TYPE_CHECKING:
    from pathlib import Path

# ── RecordMode.NONE ───────────────────────────────────────────────────────────


class TestRecordModeNone:
    def test_not_recording(self, cassette_dir: Path) -> None:
        path = cassette_dir / "test.json"
        write_cassette(path, minimal_cassette())
        cassette = Cassette(path=path, record_mode=RecordMode.NONE)
        assert cassette._recording_active is False

    def test_raises_on_unmatched_request(self, cassette_dir: Path) -> None:
        path = cassette_dir / "test.json"
        write_cassette(path, minimal_cassette())
        with (
            pytest.raises(KeyError, match="No recorded response"),
            Cassette(path=path, record_mode=RecordMode.NONE),
        ):
            niquests.Session().post("https://other.com/nope")

    def test_absent_cassette_does_not_create_file(self, tmp_path: Path) -> None:
        path = tmp_path / "absent.json"
        with Cassette(path=path, record_mode=RecordMode.NONE):
            pass
        assert not path.exists()


# ── RecordMode.ONCE ───────────────────────────────────────────────────────────


class TestRecordModeOnce:
    def test_recording_active_when_no_file(self, tmp_path: Path) -> None:
        cassette = Cassette(path=tmp_path / "new.json", record_mode=RecordMode.ONCE)
        assert cassette._recording_active is True

    def test_not_recording_when_file_exists(self, cassette_dir: Path) -> None:
        path = cassette_dir / "existing.json"
        write_cassette(path, minimal_cassette())
        cassette = Cassette(path=path, record_mode=RecordMode.ONCE)
        assert cassette._recording_active is False

    def test_raises_on_miss_with_existing_cassette(self, cassette_dir: Path) -> None:
        path = cassette_dir / "existing.json"
        write_cassette(path, minimal_cassette())
        with pytest.raises(KeyError), Cassette(path=path, record_mode=RecordMode.ONCE):
            niquests.Session().delete("https://example.com/api")


# ── RecordMode.ALL ────────────────────────────────────────────────────────────


class TestRecordModeAll:
    def test_recording_active_without_file(self, tmp_path: Path) -> None:
        cassette = Cassette(path=tmp_path / "x.json", record_mode=RecordMode.ALL)
        assert cassette._recording_active is True

    def test_recording_active_even_when_file_exists(self, cassette_dir: Path) -> None:
        path = cassette_dir / "x.json"
        write_cassette(path, minimal_cassette())
        cassette = Cassette(path=path, record_mode=RecordMode.ALL)
        assert cassette._recording_active is True

    def test_existing_interactions_not_loaded(self, cassette_dir: Path) -> None:
        # ALL mode re-records from scratch; pre-existing interactions are ignored.
        path = cassette_dir / "x.json"
        write_cassette(path, minimal_cassette())
        cassette = Cassette(path=path, record_mode=RecordMode.ALL)
        assert cassette._interactions == []


# ── RecordMode.NEW_EPISODES ───────────────────────────────────────────────────


class TestRecordModeNewEpisodes:
    def test_recording_active_with_existing_file(self, cassette_dir: Path) -> None:
        path = cassette_dir / "test.json"
        write_cassette(path, minimal_cassette())
        cassette = Cassette(path=path, record_mode=RecordMode.NEW_EPISODES)
        assert cassette._recording_active is True

    def test_known_interactions_loaded(self, cassette_dir: Path) -> None:
        path = cassette_dir / "test.json"
        write_cassette(path, minimal_cassette())
        cassette = Cassette(path=path, record_mode=RecordMode.NEW_EPISODES)
        assert len(cassette._interactions) == 1

    def test_replays_known_and_records_unknown(self, cassette_dir: Path) -> None:
        path = cassette_dir / "new_ep.json"
        write_cassette(path, minimal_cassette())  # one GET /api entry
        cassette = Cassette(
            path=path,
            record_mode=RecordMode.NEW_EPISODES,
            match_on=frozenset({"method", "path"}),
        )
        known = FakeRequest(method="GET", url="https://example.com/api")
        unknown = FakeRequest(method="POST", url="https://example.com/other")

        # 1. Known interaction is replayed without a network call
        match = cassette.find_match(known)
        assert match is not None
        assert match.response["status"]["code"] == 200

        # 2. Unknown request has no match; simulate recording it
        assert cassette.find_match(unknown) is None
        cassette.save_interaction(
            "POST",
            "https://example.com/other",
            fake_response(200, "new data"),
        )
        cassette.save()

        # 3. Reload and verify both entries persisted
        saved = json.loads(path.read_text())
        assert len(saved["http_interactions"]) == 2
        uris = [i["request"]["uri"] for i in saved["http_interactions"]]
        assert "https://example.com/other" in uris


# ── Persistence ───────────────────────────────────────────────────────────────


class TestPersistence:
    def test_save_creates_json_file(self, tmp_path: Path) -> None:
        path = tmp_path / "cassettes" / "out.json"
        cassette = Cassette(path=path, record_mode=RecordMode.ALL)
        cassette.save_interaction("GET", "https://example.com", fake_response(200, "hi"))
        cassette.save()
        assert path.exists()
        data = json.loads(path.read_text())
        assert "http_interactions" in data

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        path = tmp_path / "a" / "b" / "c.json"
        cassette = Cassette(path=path, record_mode=RecordMode.ALL)
        cassette.save()
        assert path.exists()

    def test_save_is_noop_in_replay_mode(self, cassette_dir: Path) -> None:
        path = cassette_dir / "test.json"
        write_cassette(path, minimal_cassette())
        mtime_before = path.stat().st_mtime
        cassette = Cassette(path=path, record_mode=RecordMode.ONCE)
        cassette.save()
        assert path.stat().st_mtime == mtime_before

    def test_load_json(self, cassette_dir: Path) -> None:
        path = cassette_dir / "test.json"
        write_cassette(path, minimal_cassette())
        cassette = Cassette(path=path, record_mode=RecordMode.NONE)
        assert len(cassette._interactions) == 1

    def test_load_yaml(self, cassette_dir: Path) -> None:
        path = cassette_dir / "test.yaml"
        write_cassette(path, minimal_cassette(), fmt="yaml")
        cassette = Cassette(path=path, record_mode=RecordMode.NONE)
        assert len(cassette._interactions) == 1

    def test_load_legacy_interactions_key(self, cassette_dir: Path) -> None:
        data: dict[str, Any] = {
            "interactions": [
                {
                    "request": {"url": "https://example.com/api", "method": "GET"},
                    "response": {"status": 200, "body": "hi", "headers": {}},
                },
            ],
        }
        path = cassette_dir / "legacy.json"
        write_cassette(path, data)
        cassette = Cassette(path=path, record_mode=RecordMode.NONE)
        assert len(cassette._interactions) == 1

    def test_nimax_version_written_to_cassette(self, tmp_path: Path) -> None:
        path = tmp_path / "cassettes" / "v.json"
        cassette = Cassette(path=path, record_mode=RecordMode.ALL)
        cassette.save()
        data = json.loads(path.read_text())
        assert "nimax_version" in data

    def test_load_nonexistent_file_is_noop(self, tmp_path: Path) -> None:
        cassette = Cassette(
            path=tmp_path / "missing.json",
            record_mode=RecordMode.NONE,
        )
        assert cassette._interactions == []


# ── Placeholders ──────────────────────────────────────────────────────────────


class TestPlaceholders:
    def test_placeholder_redacted_in_saved_file(self, tmp_path: Path) -> None:
        path = tmp_path / "cassettes" / "secrets.json"
        ph = Placeholder(placeholder="<TOKEN>", replace="supersecret")
        cassette = Cassette(path=path, record_mode=RecordMode.ALL, placeholders=[ph])
        cassette.save_interaction("GET", "https://example.com", fake_response(200, "supersecret"))
        cassette.save()
        assert "supersecret" not in path.read_text()
        assert "<TOKEN>" in path.read_text()

    def test_placeholder_restored_on_load(self, cassette_dir: Path) -> None:
        raw_interactions = [
            {
                "request": {
                    "method": "GET",
                    "uri": "https://example.com/api",
                    "headers": {},
                    "body": None,
                },
                "response": {
                    "status": {"code": 200, "message": "OK"},
                    "headers": {},
                    "body": {"string": "Bearer <TOKEN>"},
                    "protocol": None,
                    "url": "https://example.com/api",
                },
                "recorded_at": "2026-01-01T00:00:00Z",
            },
        ]
        path = cassette_dir / "secrets.json"
        write_cassette(path, minimal_cassette(raw_interactions))
        ph = Placeholder(placeholder="<TOKEN>", replace="supersecret")
        cassette = Cassette(path=path, record_mode=RecordMode.NONE, placeholders=[ph])
        body = cassette._interactions[0].response["body"]["string"]
        assert "supersecret" in body
        assert "<TOKEN>" not in body


# ── Matching ──────────────────────────────────────────────────────────────────


class TestMatching:
    def test_find_match_returns_interaction(self, cassette_dir: Path) -> None:
        path = cassette_dir / "test.json"
        write_cassette(path, minimal_cassette())
        cassette = Cassette(
            path=path,
            record_mode=RecordMode.NONE,
            match_on=frozenset({"method", "path"}),
        )
        live = FakeRequest(method="GET", url="https://example.com/api")
        match = cassette.find_match(live)
        assert match is not None
        assert match.response["status"]["code"] == 200

    def test_find_match_marks_interaction_used(self, cassette_dir: Path) -> None:
        path = cassette_dir / "test.json"
        write_cassette(path, minimal_cassette())
        cassette = Cassette(
            path=path,
            record_mode=RecordMode.NONE,
            match_on=frozenset({"method", "path"}),
        )
        live = FakeRequest(method="GET", url="https://example.com/api")
        cassette.find_match(live)
        assert cassette._interactions[0].used is True

    def test_find_match_fifo_ordering(self, cassette_dir: Path) -> None:
        interactions = [
            _interaction("GET", "https://example.com/api", '{"n":1}'),
            _interaction("GET", "https://example.com/api", '{"n":2}'),
        ]
        path = cassette_dir / "fifo.json"
        write_cassette(path, minimal_cassette(interactions))
        cassette = Cassette(
            path=path,
            record_mode=RecordMode.NONE,
            match_on=frozenset({"method", "path"}),
        )
        live = FakeRequest(method="GET", url="https://example.com/api")
        first = cassette.find_match(live)
        second = cassette.find_match(live)
        assert first is not None
        assert first.response["body"]["string"] == '{"n":1}'
        assert second is not None
        assert second.response["body"]["string"] == '{"n":2}'

    def test_find_match_returns_none_on_miss(self, cassette_dir: Path) -> None:
        path = cassette_dir / "test.json"
        write_cassette(path, minimal_cassette())
        cassette = Cassette(
            path=path,
            record_mode=RecordMode.NONE,
            match_on=frozenset({"method", "path"}),
        )
        assert cassette.find_match(FakeRequest(method="POST", url="https://other.com")) is None

    def test_unknown_matcher_raises_value_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Unknown matcher"):
            Cassette(path=tmp_path / "x.json", match_on=frozenset({"bogus"}))


# ── Response construction ─────────────────────────────────────────────────────


class TestBuildResponse:
    def setup_method(self) -> None:
        pass

    def _loaded_cassette(self, cassette_dir: Path) -> Cassette:
        path = cassette_dir / "test.json"
        write_cassette(path, minimal_cassette())
        return Cassette(
            path=path,
            record_mode=RecordMode.NONE,
            match_on=frozenset({"method", "path"}),
        )

    def test_status_code(self, cassette_dir: Path) -> None:
        cassette = self._loaded_cassette(cassette_dir)
        live = FakeRequest(method="GET", url="https://example.com/api")
        match = cassette.find_match(live)
        assert match is not None
        resp = cassette._build_response(match, live)
        assert resp.status_code == 200

    def test_body_decoded(self, cassette_dir: Path) -> None:
        cassette = self._loaded_cassette(cassette_dir)
        live = FakeRequest(method="GET", url="https://example.com/api")
        match = cassette.find_match(live)
        assert match is not None
        resp = cassette._build_response(match, live)
        assert resp.text == '{"ok": true}'

    def test_headers_present(self, cassette_dir: Path) -> None:
        cassette = self._loaded_cassette(cassette_dir)
        live = FakeRequest(method="GET", url="https://example.com/api")
        match = cassette.find_match(live)
        assert match is not None
        resp = cassette._build_response(match, live)
        assert "Content-Type" in resp.headers

    def test_empty_body_interaction(self, cassette_dir: Path) -> None:
        interactions = [_interaction("GET", "https://example.com/api", "")]
        path = cassette_dir / "empty.json"
        write_cassette(path, minimal_cassette(interactions))
        cassette = Cassette(
            path=path,
            record_mode=RecordMode.NONE,
            match_on=frozenset({"method", "path"}),
        )
        live = FakeRequest(method="GET", url="https://example.com/api")
        match = cassette.find_match(live)
        assert match is not None
        resp = cassette._build_response(match, live)
        assert resp.text == ""


# ── Thread safety ─────────────────────────────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_find_match_no_double_claim(self, cassette_dir: Path) -> None:
        n = 20
        interactions = [
            _interaction("GET", "https://example.com/api", f"resp-{i}") for i in range(n)
        ]
        path = cassette_dir / "concurrent.json"
        write_cassette(path, minimal_cassette(interactions))
        cassette = Cassette(
            path=path,
            record_mode=RecordMode.NONE,
            match_on=frozenset({"method", "path"}),
        )
        live = FakeRequest(method="GET", url="https://example.com/api")
        results: list[Any] = []
        lock = threading.Lock()

        def worker() -> None:
            m = cassette.find_match(live)
            if m is not None:
                with lock:
                    results.append(m)

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Each thread claims a distinct interaction — no double-claiming
        assert len(results) == n
        bodies = {r.response["body"]["string"] for r in results}
        assert len(bodies) == n


# ── Backward-compat ───────────────────────────────────────────────────────────


class TestBackwardCompat:
    def test_record_true_maps_to_all(self, tmp_path: Path) -> None:
        cassette = Cassette(path=tmp_path / "x.json", record=True)
        assert cassette._recording_active is True

    def test_record_false_maps_to_none(self, cassette_dir: Path) -> None:
        path = cassette_dir / "x.json"
        write_cassette(path, minimal_cassette())
        cassette = Cassette(path=path, record=False)
        assert cassette._recording_active is False


# ── Helpers ───────────────────────────────────────────────────────────────────


def _interaction(method: str, uri: str, body: str) -> dict[str, Any]:
    return {
        "request": {"method": method, "uri": uri, "headers": {}, "body": None},
        "response": {
            "status": {"code": 200, "message": "OK"},
            "headers": {},
            "body": {"string": body} if body else None,
            "protocol": None,
            "url": uri,
        },
        "recorded_at": "2026-01-01T00:00:00Z",
    }
