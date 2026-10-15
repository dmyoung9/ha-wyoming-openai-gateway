"""Loopback services and subprocess helpers for discovery/runtime tests."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DISCOVER_SCRIPT = ROOT / "wyoming_openai_gateway/rootfs/usr/local/bin/discover.py"
TOKEN_SENTINEL = "test-supervisor-token-must-never-escape"


def import_discovery_module():
    """Import the deployed discovery script directly, independent of package layout."""
    import importlib.util

    if not DISCOVER_SCRIPT.is_file():
        raise FileNotFoundError(f"discovery implementation is not present: {DISCOVER_SCRIPT}")
    spec = importlib.util.spec_from_file_location("gateway_discover", DISCOVER_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load discovery implementation from {DISCOVER_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def encode_wyoming_event(event_type: str, data: Mapping[str, Any] | None = None) -> bytes:
    """Encode one Wyoming event using its JSON-line/data_length wire format."""
    payload = json.dumps(dict(data or {}), separators=(",", ":")).encode("utf-8")
    event = {"type": event_type, "data_length": len(payload)}
    return json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n" + payload


def decode_wyoming_event(stream: socket.socket, *, max_header: int = 65536) -> dict[str, Any]:
    """Read one Wyoming JSON-line event and any length-delimited JSON data."""
    line = bytearray()
    while not line.endswith(b"\n") and len(line) <= max_header:
        chunk = stream.recv(1)
        if not chunk:
            break
        line.extend(chunk)
    if not line or len(line) > max_header or not line.endswith(b"\n"):
        raise ValueError("invalid Wyoming event header")
    event = json.loads(line)
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        raise ValueError("invalid Wyoming event object")
    data_length = event.pop("data_length", 0)
    if data_length:
        chunks = bytearray()
        while len(chunks) < data_length:
            chunk = stream.recv(data_length - len(chunks))
            if not chunk:
                raise EOFError("connection ended inside Wyoming event data")
            chunks.extend(chunk)
        event["data"] = json.loads(chunks)
    return event


class FakeWyomingServer:
    """A one-connection Wyoming peer with controllable reply, malformed, and stall modes."""

    def __init__(
        self,
        response: bytes | None = None,
        *,
        stall: bool = False,
        accept_timeout: float = 3.0,
    ) -> None:
        self.response = response or encode_wyoming_event(
            "info",
            {
                "asr": [{"name": "fixture-asr", "installed": True}],
                "tts": [{"name": "fixture-tts", "installed": True}],
            },
        )
        self.stall = stall
        self.accept_timeout = accept_timeout
        self.request: dict[str, Any] | None = None
        self.error: BaseException | None = None
        self.received = threading.Event()
        self._stop = threading.Event()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(1)
        self._socket.settimeout(accept_timeout)
        self.host, self.port = self._socket.getsockname()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> FakeWyomingServer:
        self._thread.start()
        return self

    def _serve(self) -> None:
        try:
            connection, _ = self._socket.accept()
            with connection:
                connection.settimeout(self.accept_timeout)
                self.request = decode_wyoming_event(connection)
                self.received.set()
                if not self.stall:
                    connection.sendall(self.response)
        except BaseException as exc:  # captured for assertion in the owning test thread
            self.error = exc
            self.received.set()

    def close(self) -> None:
        self._stop.set()
        self._socket.close()
        if self._thread.is_alive():
            self._thread.join(timeout=self.accept_timeout + 0.2)

    def __enter__(self) -> FakeWyomingServer:
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.close()


class RuntimeProcess:
    """Own a child runtime and its captured output; callers supply its real CLI."""

    def __init__(self, command: Sequence[str], env: Mapping[str, str] | None = None) -> None:
        child_env = os.environ.copy()
        child_env.update(env or {})
        self.process = subprocess.Popen(
            list(command),
            cwd=ROOT,
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )

    @property
    def pid(self) -> int:
        return self.process.pid

    def wait_for_exit(self, timeout: float = 5.0) -> int:
        return self.process.wait(timeout=timeout)

    def stop(self, timeout: float = 5.0) -> int:
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            return self.process.wait(timeout=1.0)

    def output(self) -> str:
        if self.process.poll() is None:
            raise RuntimeError("capture output only after the child exits")
        return self.process.stdout.read() if self.process.stdout else ""

    def __enter__(self) -> RuntimeProcess:
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


def wait_until(
    predicate: Callable[[], bool], *, timeout: float = 5.0, interval: float = 0.025
) -> bool:
    """Bounded readiness wait for asynchronous child startup; never used as a test retry."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())
