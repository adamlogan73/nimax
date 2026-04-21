# nimax

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

## Placeholders

Scrub sensitive values (tokens, API keys) from cassettes before they are written:

```python
from nimax import Placeholder

recorder = NimaxRecorder(session, placeholders=[
    Placeholder(placeholder="<AUTH_TOKEN>", replace="Bearer secret123"),
])
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
