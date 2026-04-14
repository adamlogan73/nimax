"""Shared pytest fixtures for all nimax tests."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def cassette_dir(tmp_path: Path) -> Path:
    """A temporary directory pre-created for cassette files."""
    d = tmp_path / "cassettes"
    d.mkdir()
    return d


@pytest.fixture
def echo_ws_server() -> Generator[str, None, None]:
    """Local WebSocket echo server running in a background thread.

    Yields the ``ws://localhost:{port}`` URI.  The server echoes every
    message back to the sender and shuts down cleanly after the test.
    """
    import websockets
    from websockets.asyncio.server import ServerConnection
    from websockets.asyncio.server import serve

    loop = asyncio.new_event_loop()
    started = threading.Event()
    shutdown = threading.Event()
    port_holder: list[int] = []

    async def handler(ws: ServerConnection) -> None:
        async for message in ws:
            await ws.send(message)

    async def run() -> None:
        async with serve(handler, "localhost", 0) as server:
            port = server.sockets[0].getsockname()[1]
            port_holder.append(port)
            started.set()
            while not shutdown.is_set():
                await asyncio.sleep(0.05)

    thread = threading.Thread(
        target=lambda: loop.run_until_complete(run()),
        daemon=True,
    )
    thread.start()
    started.wait(timeout=5)
    yield f"ws://localhost:{port_holder[0]}"
    shutdown.set()
    thread.join(timeout=5)
    if not loop.is_running():
        loop.close()
