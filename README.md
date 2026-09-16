# nimax

[![CI](https://github.com/adamlogan73/nimax/actions/workflows/ci.yml/badge.svg)](https://github.com/adamlogan73/nimax/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/adamlogan73/nimax/graph/badge.svg)](https://codecov.io/gh/adamlogan73/nimax)
[![PyPI](https://img.shields.io/pypi/v/nimax)](https://pypi.org/project/nimax/)

Record and replay [niquests](https://github.com/jawah/niquests) HTTP and WebSocket interactions in pytest.

nimax is a VCR-style cassette library built natively for niquests — supporting lazy responses, multiplexed connections, `AsyncSession`, and WebSockets. It is to niquests what [betamax](https://github.com/betamax/betamax) is to requests.

## Installation

```bash
pip install nimax
```

## Quick start

### Automatic fixture

nimax registers a `nimax_session` pytest fixture automatically. Use it instead of `niquests.Session()` in your tests:

```python
def test_my_api(nimax_session):
    resp = nimax_session.get("https://api.example.com/users")
    assert resp.status_code == 200
```

On the first run nimax records the real HTTP response to a cassette file under `cassettes/<test_module>/<test_name>.json`. Subsequent runs replay from the cassette — no network required.

### Async sessions

```python
import pytest
import niquests


async def test_async(nimax_session):
    async with niquests.AsyncSession() as session:
        with NimaxRecorder(session).use_cassette("my_cassette.json"):
            resp = await session.get("https://api.example.com/data")
            assert resp.status_code == 200
```

### Programmatic API

```python
import niquests
from nimax import NimaxRecorder, RecordMode


def test_programmatic(tmp_path):
    session = niquests.Session()
    cassette_path = tmp_path / "my_cassette.json"
    with NimaxRecorder(session).use_cassette(cassette_path, record_mode=RecordMode.ONCE):
        resp = session.get("https://api.example.com/users")
        assert resp.status_code == 200
```

## Record modes

| Mode | Behaviour |
|---|---|
| `once` | Record on first run, replay on subsequent runs (default) |
| `none` | Never record — raise an error if no matching interaction exists |
| `new_episodes` | Replay existing interactions; record any unmatched requests |
| `all` | Always record, overwriting the cassette each run |

## WebSocket support

WebSocket connections opened through a cassette-backed session are recorded and replayed automatically, alongside HTTP interactions, in the same cassette file:

```python
def test_ws_echo(nimax_session):
    resp = nimax_session.get("wss://echo.example.com")
    resp.extension.send_payload('{"id": "1", "op": "ping"}')
    reply = resp.extension.next_payload()
```

On replay, a recv frame only releases once the real `send_payload()` call it depends on has actually happened — matching how a real socket can't deliver a response before its triggering request went out. By default this is tracked by position (send count) in the recorded log, which is correct as long as your live send order doesn't diverge from the recorded order.

### Correlating responses by id

If your protocol embeds a correlation id in its messages (e.g. JSON-RPC-style `{"id": ..., ...}`) and you dispatch responses from a background reader task — so concurrent, in-flight requests can legitimately resolve out of order — pass `ws_id_extractor` to correlate replay by that id instead of by position:

```python
with NimaxRecorder(session).use_cassette("my_cassette.json", ws_id_extractor="id"):
    ...
```

`ws_id_extractor` accepts:

- A dotted JSON path string, e.g. `"id"` or `"params.id"` for a nested field.
- A callable `(payload: str | bytes) -> Any | None` for anything else (non-JSON protocols, custom shapes).

A message whose id doesn't resolve (e.g. valid JSON with no id field) falls back to the position-based gate. A payload the extractor can't parse at all (e.g. malformed JSON when JSON was expected) raises — that means the extractor doesn't match the actual protocol, which is worth surfacing rather than silently ignoring.

With the automatic `nimax_session`/`nimax_async_session` fixtures, set a dotted-path string project-wide via `[tool.nimax]` in `pyproject.toml`:

```toml
[tool.nimax]
ws_id_extractor = "id"
```

A callable can't live in static config, so to use one with the automatic fixtures, override the `nimax_ws_id_extractor` fixture in your own `conftest.py`:

```python
import pytest


@pytest.fixture
def nimax_ws_id_extractor():
    return my_custom_extractor
```

## Placeholders

Scrub sensitive values (tokens, API keys) from cassettes before they are written:

```python
from nimax import Placeholder

recorder = NimaxRecorder(
    session,
    placeholders=[
        Placeholder(placeholder="<AUTH_TOKEN>", replace="Bearer secret123"),
    ],
)
```

## Custom matchers and serializers

```python
from nimax import BaseMatcher, NimaxRecorder


class BodyMatcher(BaseMatcher):
    name = "body"

    def match(self, recorded: dict, live: object) -> bool:
        return recorded.get("body") == live.body  # type: ignore[union-attr]


NimaxRecorder.register_matcher(BodyMatcher)
```

YAML cassettes are supported out of the box — use a `.yaml` extension for the cassette path.

## Requirements

- Python ≥ 3.11
- niquests ≥ 3
- pytest ≥ 8
- PyYAML ≥ 6

## License

MIT
