from __future__ import annotations

import json
import os
import socket
import sys
import urllib.error
import urllib.request

from support import DISCOVER_SCRIPT, TOKEN_SENTINEL, RuntimeProcess, wait_until


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def get_json(url):
    with urllib.request.urlopen(url, timeout=2) as response:
        return response.status, json.loads(response.read())


def test_runtime_stays_healthy_but_unready_without_providers_and_stops_on_sigterm():
    port = free_port()
    env = os.environ.copy()
    env.pop("SUPERVISOR_TOKEN", None)
    env.update({"FRONTEND_HOST": "127.0.0.1", "FRONTEND_PORT": str(port)})
    base = f"http://127.0.0.1:{port}"

    with RuntimeProcess([sys.executable, str(DISCOVER_SCRIPT)], env) as runtime:
        assert wait_until(
            lambda: _http_status(base + "/healthz") == 200,
            timeout=5,
        ), "runtime did not open its health endpoint"

        health_status, health = get_json(base + "/healthz")
        ready_status, ready = _http_status_json(base + "/readyz")
        status_code, status = get_json(base + "/status")

        assert health_status == status_code == 200
        assert health["status"] == "ok" and health["ready"] is False
        assert ready_status == 503 and ready["ready"] is False
        assert status["tts"]["status"] == "waiting"
        assert status["stt"]["status"] == "waiting"

        request = urllib.request.Request(base + "/v1/voices", method="GET")
        try:
            urllib.request.urlopen(request, timeout=2)
        except urllib.error.HTTPError as error:
            assert error.code == 503
            assert json.loads(error.read())["error"] == "gateway_unavailable"
        else:
            raise AssertionError("API request unexpectedly succeeded without a provider")

        too_large = urllib.request.Request(
            base + "/v1/audio/transcriptions",
            data=None,
            headers={"Content-Length": str(25 * 1024 * 1024 + 1)},
            method="POST",
        )
        try:
            urllib.request.urlopen(too_large, timeout=2)
        except urllib.error.HTTPError as error:
            assert error.code == 413
            assert json.loads(error.read())["error"] == "request_too_large"
        else:
            raise AssertionError("oversized request was not rejected")

        assert runtime.stop(timeout=5) == 0
        output = runtime.output()
        assert TOKEN_SENTINEL not in output


def _http_status(url):
    try:
        with urllib.request.urlopen(url, timeout=0.25) as response:
            return response.status
    except (OSError, urllib.error.HTTPError) as error:
        return getattr(error, "code", None)


def _http_status_json(url):
    try:
        return get_json(url)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_selected_child_is_not_relaunched_until_configuration_or_liveness_changes(
    discovery, monkeypatch, caplog
):
    piper = {
        "slug": "core_piper",
        "name": "Piper",
        "state": "started",
        "installed": True,
    }
    info = {"name": "Piper", "state": "started", "ports": {"10200/tcp": 45678}}
    launches = []

    class FakeSupervisor:
        def __init__(self, **_kwargs):
            pass

        def list_addons(self):
            return [piper]

        def get_addon_info(self, _slug):
            return info

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid
            self.return_code = None

        def poll(self):
            return self.return_code

        def wait(self, timeout=None):
            if self.return_code is None:
                self.return_code = 0
            return self.return_code

    def fake_popen(command, **kwargs):
        assert command == ["test-gateway"]
        process = FakeProcess(9000 + len(launches))
        launches.append((process, kwargs["env"]))
        return process

    def fake_killpg(pid, _signal):
        for process, _ in launches:
            if process.pid == pid:
                process.return_code = -15
                return
        raise ProcessLookupError(pid)

    monkeypatch.setattr(discovery.os, "killpg", fake_killpg)
    monkeypatch.setattr(discovery, "RETRY_BASE_SECONDS", 0)
    manager = discovery.GatewaySupervisor(
        options=discovery.GatewayOptions(),
        token=TOKEN_SENTINEL,
        supervisor_factory=FakeSupervisor,
        probe=lambda _host, _port: frozenset({"tts"}),
        popen_factory=fake_popen,
        gateway_command="test-gateway",
        parent_environment={"SUPERVISOR_TOKEN": TOKEN_SENTINEL, "SAFE_VALUE": "kept"},
    )

    manager.reconcile_once()
    manager.reconcile_once()
    assert len(launches) == 1
    child_env = launches[0][1]
    assert TOKEN_SENTINEL not in child_env.values()
    assert "SUPERVISOR_TOKEN" not in child_env
    assert child_env["SAFE_VALUE"] == "kept"
    assert TOKEN_SENTINEL not in caplog.text

    launches[0][0].return_code = 1
    manager._monitor_child()
    assert len(launches) == 2

    manager.options = discovery.GatewayOptions(tts_provider="none", stt_provider="none")
    manager.reconcile_once()
    assert launches[1][0].return_code == -15
    assert manager._process is None
