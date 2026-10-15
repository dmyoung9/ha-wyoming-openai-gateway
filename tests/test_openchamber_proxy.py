"""Test the JS embedded in the hook without executing hooks or writing /data."""
import contextlib
import io
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from support import TOKEN_SENTINEL, wait_until


@pytest.fixture
def speech_server():
    state = {"paths": [], "speech_auth": [], "body": b"", "map": "network",
             "v2": False, "voices": True, "bad_json": False, "redirect": False,
             "oversized": False, "readiness": None, "post_path": None,
             "content_type": None, "delay": 0}
    audio = io.BytesIO()
    with wave.open(audio, "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 160)
    state["audio"] = audio.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def reply(self, value, status=200, content_type="application/json"):
            body = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)

        def do_GET(self):
            state["paths"].append(self.path)
            if self.path in ("/v2/apps", "/supervisor/info"):
                assert self.headers.get("Authorization") == f"Bearer {TOKEN_SENTINEL}"
                if state["redirect"]:
                    self.send_response(302)
                    self.send_header("Location", "/leak")
                    self.end_headers()
                    return
                if self.path == "/v2/apps" and not state["v2"]:
                    self.reply({"message": "Forbidden"}, 403)
                    return
                if state["bad_json"]:
                    self.reply(("not-json " + TOKEN_SENTINEL).encode())
                    return
                if state["oversized"]:
                    self.reply(b" " * (1024 * 1024 + 1))
                    return
                key = "apps" if self.path == "/v2/apps" else "addons"
                self.reply({"result": "ok", "data": {key: [
                    {"slug": "demo_gateway", "state": "started"},
                    {"slug": "stopped_gateway", "state": "stopped"},
                ]}})
            elif self.path in ("/v2/apps/demo_gateway/info", "/addons/demo_gateway/info"):
                assert self.headers.get("Authorization") == f"Bearer {TOKEN_SENTINEL}"
                port = self.server.server_port
                shapes = {
                    "network": {"network": {f"{port}/tcp": 1}},
                    "ports": {"ports": {f"{port}/tcp": None}},
                    "nested": {"network": {"tcp": {str(port): 1}}},
                    "udp": {"network": {f"{port}/udp": port}},
                }
                self.reply({"data": {"dns": ["127.0.0.1"], **shapes[state["map"]]}})
            elif self.path == "/readyz" and state["readiness"] is not None:
                self.reply(state["readiness"])
            elif self.path == "/v1/voices":
                state["speech_auth"].append(self.headers.get("Authorization"))
                self.reply({"voices": [{"id": "demo"}] if state["voices"] else []})
            else:
                self.reply({"error": "not found"}, 404)

        def do_POST(self):
            state["body"] = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["post_path"] = self.path
            state["content_type"] = self.headers.get("Content-Type")
            state["speech_auth"].append(self.headers.get("Authorization"))
            time.sleep(state["delay"])
            if self.path in ("/v1/audio/transcriptions", "/v1/audio/translations"):
                self.reply({"text": "synthetic transcript"})
            else:
                self.reply(state["audio"], content_type="audio/wav")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextlib.contextmanager
def launch(proxy_program, tmp_path, speech_server, **overrides):
    node, program = proxy_program
    server, _state = speech_server
    environment = dict(os.environ, SUPERVISOR_TOKEN=TOKEN_SENTINEL,
                       OPENCHAMBER_PROXY_SUPERVISOR_URL=f"http://127.0.0.1:{server.server_port}",
                       OPENCHAMBER_PROXY_LISTEN_HOST="127.0.0.1", OPENCHAMBER_PROXY_PORT="0",
                       OPENCHAMBER_PROXY_DNS_FAMILY="4", OPENCHAMBER_PROXY_TARGET_SLUG="",
                       OPENCHAMBER_PROXY_UPSTREAM_TIMEOUT_MS="300000")
    environment.update(overrides)
    log = tmp_path / "proxy.log"
    with log.open("w") as output:
        process = subprocess.Popen([node, str(program)], env=environment, stdout=output,
                                   stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        try:
            yield process, log
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=7)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)


def listening_url(process, log):
    pattern = r"custom speech server at: (http://[^\s]+)"
    assert wait_until(lambda: re.search(pattern, log.read_text()) or process.poll() is not None,
                      timeout=10), log.read_text()
    assert process.poll() is None, log.read_text()
    return re.search(pattern, log.read_text()).group(1)


@pytest.mark.parametrize("shape", ["network", "ports", "nested"])
@pytest.mark.parametrize("v2", [False, True])
def test_proxy_discovers_internal_tcp_port_and_streams_audio(
    proxy_program, tmp_path, speech_server, shape, v2,
):
    _server, state = speech_server
    state.update(map=shape, v2=v2)
    with launch(proxy_program, tmp_path, speech_server) as (process, log):
        base = listening_url(process, log)
        with urllib.request.urlopen(base + "/voices", timeout=3) as response:
            assert json.load(response)["voices"][0]["id"] == "demo"
        body = b'{"input":"private speech must not be logged"}'
        request = urllib.request.Request(base + "/audio/speech", data=body,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=3) as response:
            assert response.headers["Content-Type"] == "audio/wav"
            assert response.read() == state["audio"]
        assert state["body"] == body
        assert all(value != f"Bearer {TOKEN_SENTINEL}" for value in state["speech_auth"])
        assert not any("stopped_gateway" in path for path in state["paths"])
        assert TOKEN_SENTINEL not in log.read_text()
        assert "private speech" not in log.read_text()


def test_busy_preferred_port_falls_back_and_announces_actual_url(
    proxy_program, tmp_path, speech_server,
):
    server, _state = speech_server
    with launch(proxy_program, tmp_path, speech_server,
                OPENCHAMBER_PROXY_PORT=str(server.server_port)) as (process, log):
        base = listening_url(process, log)
        assert f":{server.server_port}/v1" not in base
        assert "taking a free port" in log.read_text()


@pytest.mark.parametrize(
    "mode", ["udp", "empty", "bad_json", "redirect", "wrong_slug", "oversized"],
)
def test_proxy_fails_safely_when_no_verified_target_exists(
    proxy_program, tmp_path, speech_server, mode,
):
    _server, state = speech_server
    if mode == "udp":
        state["map"] = "udp"
    elif mode == "empty":
        state["voices"] = False
    elif mode in ("bad_json", "redirect", "oversized"):
        state[mode] = True
    overrides = (
        {"OPENCHAMBER_PROXY_TARGET_SLUG": "different_gateway"} if mode == "wrong_slug" else {}
    )
    with launch(proxy_program, tmp_path, speech_server, **overrides) as (process, log):
        assert process.wait(timeout=10) == 1
        assert "custom speech server at:" not in log.read_text()
        assert TOKEN_SENTINEL not in log.read_text()
        assert "/leak" not in state["paths"]
        if mode in ("udp", "wrong_slug"):
            assert "/v1/voices" not in state["paths"]
        if mode == "oversized":
            assert "too large" in log.read_text()


def test_proxy_refuses_public_listener_before_any_discovery(proxy_program, tmp_path, speech_server):
    _server, state = speech_server
    with launch(proxy_program, tmp_path, speech_server,
                OPENCHAMBER_PROXY_LISTEN_HOST="0.0.0.0") as (process, log):
        assert process.wait(timeout=5) == 1
        assert "listener must stay on" in log.read_text()
        assert state["paths"] == []


def test_proxy_refuses_remote_supervisor_origin_before_sending_credentials(
    proxy_program, tmp_path, speech_server,
):
    _server, state = speech_server
    with launch(proxy_program, tmp_path, speech_server,
                OPENCHAMBER_PROXY_SUPERVISOR_URL="https://example.invalid") as (process, log):
        assert process.wait(timeout=5) == 1
        assert "Supervisor URL must target" in log.read_text()
        assert state["paths"] == []
        assert TOKEN_SENTINEL not in log.read_text()


def test_request_dns_lookup_has_deadline(proxy_program, tmp_path, speech_server):
    node, program = proxy_program
    wrapper = tmp_path / "stalled-dns.mjs"
    wrapper.write_text(
        'import dns from "node:dns/promises";\n'
        'import { syncBuiltinESMExports } from "node:module";\n'
        'dns.lookup = () => new Promise(() => {});\n'
        'syncBuiltinESMExports();\n'
        f'await import({json.dumps(program.as_uri())});\n'
    )
    with launch((node, wrapper), tmp_path, speech_server) as (process, log):
        base = listening_url(process, log)
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base + "/voices", timeout=8)
        assert error.value.code == 502


def readiness(*roles):
    return {
        "ready": bool(roles), "gateway": {"healthy": bool(roles)},
        **{role: {"status": "selected" if role in roles else "disabled",
                  "provider": {"capabilities": ["asr" if role == "stt" else "tts"]}
                  if role in roles else None} for role in ("tts", "stt")},
    }


@pytest.mark.parametrize("roles", [("tts",), ("stt",), ("tts", "stt")])
def test_ready_gateway_discovery_does_not_depend_on_voice_listing(
    proxy_program, tmp_path, speech_server, roles,
):
    _server, state = speech_server
    state.update(readiness=readiness(*roles), voices=False)
    with launch(proxy_program, tmp_path, speech_server) as (process, log):
        listening_url(process, log)
        assert "/readyz" in state["paths"]
        assert "/v1/voices" not in state["paths"]


@pytest.mark.parametrize("body", [
    {"status": "ok"}, readiness(),
    {**readiness("stt"), "ready": False},
    {**readiness("stt"), "gateway": {"healthy": False}},
    {**readiness(), "ready": True, "gateway": {"healthy": True}},
    {**readiness("stt"), "stt": {"status": "selected", "provider": {}}},
    {**readiness("stt"), "stt": {"status": "ambiguous", "provider": {"capabilities": ["asr"]}}},
])
def test_health_alone_or_invalid_role_metadata_does_not_prove_stt_support(
    proxy_program, tmp_path, speech_server, body,
):
    _server, state = speech_server
    state.update(readiness=body, voices=False)
    with launch(proxy_program, tmp_path, speech_server) as (process, log):
        assert process.wait(timeout=10) == 1
        assert "custom speech server at:" not in log.read_text()


@pytest.mark.parametrize("route", ["transcriptions", "translations"])
def test_whisper_only_proxy_preserves_multipart_body_and_json_response(
    proxy_program, tmp_path, speech_server, route,
):
    _server, state = speech_server
    state.update(readiness=readiness("stt"), voices=False)
    boundary = "speech-test-boundary"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n"
            f"whisper-1\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"sample.wav\"\r\nContent-Type: audio/wav\r\n\r\n").encode()
    body += state["audio"] + f"\r\n--{boundary}--\r\n".encode()
    content_type = f"multipart/form-data; boundary={boundary}"
    with launch(proxy_program, tmp_path, speech_server) as (process, log):
        base = listening_url(process, log)
        request = urllib.request.Request(base + "/audio/" + route, data=body,
                                         headers={"Content-Type": content_type})
        with urllib.request.urlopen(request, timeout=3) as response:
            assert response.headers["Content-Type"].startswith("application/json")
            assert json.load(response) == {"text": "synthetic transcript"}
        assert state["body"] == body
        assert state["content_type"] == content_type
        assert state["post_path"] == f"/v1/audio/{route}"
        assert "synthetic transcript" not in log.read_text()


def test_upstream_timeout_is_configurable_and_still_bounded(proxy_program, tmp_path, speech_server):
    _server, state = speech_server
    state.update(readiness=readiness("stt"), voices=False, delay=1.4)
    with launch(proxy_program, tmp_path, speech_server,
                OPENCHAMBER_PROXY_UPSTREAM_TIMEOUT_MS="1000") as (process, log):
        base = listening_url(process, log)
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(base + "/audio/transcriptions",
                                                         data=b"test"), timeout=4)
        assert error.value.code == 502


@pytest.mark.parametrize("value", ["NaN", "0", "-1", "300001"])
def test_invalid_upstream_timeout_is_rejected_before_discovery(
    proxy_program, tmp_path, speech_server, value,
):
    _server, state = speech_server
    with launch(proxy_program, tmp_path, speech_server,
                OPENCHAMBER_PROXY_UPSTREAM_TIMEOUT_MS=value) as (process, _log):
        assert process.wait(timeout=5) == 1
        assert state["paths"] == []
