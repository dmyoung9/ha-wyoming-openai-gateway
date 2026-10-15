"""Optional real-gateway smoke test with synthetic Wyoming audio providers.

Install the pinned gateway and runtime requirements to enable this test. This
proves protocol translation, not real Piper/Whisper or HAOS integration.
"""
import asyncio
import io
import json
import os
import socket
import sys
import threading
import traceback
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("wrapped", "roles"), [(False, "both"), (True, "both"), (True, "tts"), (True, "asr")],
    ids=["upstream", "wrapper-both", "wrapper-tts", "wrapper-asr"],
)
def test_real_gateway_wav_and_multipart_transcription(wrapped, roles, capfd):
    pytest.importorskip("wyoming_openai_gateway.app")
    pytest.importorskip("aiohttp")
    asyncio.run(_exercise(wrapped, roles))
    if wrapped:
        output = capfd.readouterr()
        assert "Synthetic test" not in output.out + output.err


@pytest.mark.parametrize("roles", ["both", "tts", "asr"])
def test_real_gateway_through_loopback_proxy_with_independent_providers(
    roles, proxy_program, tmp_path, capfd,
):
    pytest.importorskip("wyoming_openai_gateway.app")
    pytest.importorskip("aiohttp")
    asyncio.run(_exercise(True, roles, proxy_program=proxy_program, proxy_tmp_path=tmp_path))
    output = capfd.readouterr()
    assert "Synthetic test" not in output.out + output.err


async def _exercise(wrapped, roles, proxy_program=None, proxy_tmp_path=None):
    from aiohttp import ClientSession, ClientTimeout, FormData
    from wyoming.audio import AudioChunk, AudioStart, AudioStop
    from wyoming.event import Event, async_read_event, async_write_event

    attribution = {"name": "synthetic test", "url": "https://example.com"}
    voice = {"name": "test", "description": "Test", "languages": ["en"],
             "attribution": attribution, "installed": True}

    async def provider(reader, writer, provider_role):
        try:
            while event := await async_read_event(reader):
                if event.type == "describe":
                    service = {"name": "mock", "attribution": attribution, "installed": True,
                               "voices" if provider_role == "tts" else "models": [voice]}
                    await async_write_event(Event(type="info", data={
                        provider_role: [service],
                    }), writer)
                elif event.type == "synthesize" and provider_role == "tts":
                    for response in [AudioStart(rate=16000, width=2, channels=1).event(),
                                     AudioChunk(rate=16000, width=2, channels=1,
                                                audio=b"\0\0" * 1600).event(),
                                     AudioStop().event()]:
                        await async_write_event(response, writer)
                elif event.type == "audio-stop" and provider_role == "asr":
                    await async_write_event(
                        Event(type="transcript", data={"text": "Synthetic test"}), writer,
                    )
        finally:
            writer.close()
            await writer.wait_closed()

    servers = []
    provider_ports = {}
    for provider_role in ("tts", "asr") if roles == "both" else (roles,):
        server = await asyncio.start_server(
            lambda reader, writer, role=provider_role: provider(reader, writer, role),
            "127.0.0.1", 0,
        )
        servers.append(server)
        provider_ports[provider_role] = server.sockets[0].getsockname()[1]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, HOST="127.0.0.1", PORT=str(port), PREFIX="/v1", LOG_LEVEL="WARNING")
    for role, prefix in (("tts", "TTS"), ("asr", "STT")):
        env.pop(f"{prefix}_HOST", None)
        env.pop(f"{prefix}_PORT", None)
        if role in provider_ports:
            env[f"{prefix}_HOST"] = "127.0.0.1"
            env[f"{prefix}_PORT"] = str(provider_ports[role])
    process = None
    manager = frontend = None
    proxy_context = proxy_supervisor = None
    if wrapped:
        from support import import_discovery_module

        discovery = import_discovery_module()
        discovery.GATEWAY_PORT = port
        discovery.OFFICIAL_ADAPTERS = {
            slug: discovery.ProviderAdapter(slug, provider_ports[role])
            for role, slug in (("tts", "core_piper"), ("asr", "core_whisper"))
            if role in provider_ports
        }
        discovery.slug_to_dns_name = lambda _slug: "127.0.0.1"

        class Inventory:
            def __init__(self, *_args, **_kwargs):
                pass

            def list_addons(self):
                return [{"slug": slug, "state": "started"}
                        for slug in discovery.OFFICIAL_ADAPTERS]

            def get_addon_info(self, slug):
                return {"name": slug, "state": "started",
                        "ports": {f"{discovery.OFFICIAL_ADAPTERS[slug].internal_port}/tcp": None}}

        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
        manager = discovery.GatewaySupervisor(
            discovery.GatewayOptions(log_level="INFO"), token="private-sentinel",
            supervisor_factory=Inventory, parent_environment=env,
        )
        frontend = discovery.BoundedThreadingHTTPServer(
            ("127.0.0.1", 0), discovery.create_handler(manager.status), manager.status,
        )
        frontend.handle_error = lambda *_args: traceback.print_exc()
        threading.Thread(target=frontend.serve_forever, daemon=True).start()
        manager.start()
        base = f"http://127.0.0.1:{frontend.server_port}"
    else:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "wyoming_openai_gateway", env=env,
        )
        base = f"http://127.0.0.1:{port}"
    try:
        async with ClientSession(timeout=ClientTimeout(total=15)) as client:
            async with asyncio.timeout(15):
                while True:
                    try:
                        async with client.get(base + "/readyz") as response:
                            if response.status == 200:
                                break
                    except OSError:
                        pass
                    if process is not None:
                        assert process.returncode is None, "gateway exited before becoming healthy"
                    await asyncio.sleep(0.05)
            if proxy_program is not None:
                from support import TOKEN_SENTINEL
                from test_openchamber_proxy import launch, listening_url

                class SupervisorHandler(BaseHTTPRequestHandler):
                    def do_GET(self):
                        if self.headers.get("Authorization") != f"Bearer {TOKEN_SENTINEL}":
                            self.send_response(401)
                            self.end_headers()
                            return
                        if self.path == "/supervisor/info":
                            data = {"addons": [{"slug": "demo_gateway", "state": "started"}]}
                        elif self.path in (
                            "/v2/apps/demo_gateway/info", "/addons/demo_gateway/info",
                        ):
                            data = {"dns": ["127.0.0.1"],
                                    "network": {f"{frontend.server_port}/tcp": None}}
                        else:
                            self.send_response(403)
                            self.end_headers()
                            return
                        payload = json.dumps({"result": "ok", "data": data}).encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)

                    def log_message(self, *_args):
                        pass

                proxy_supervisor = ThreadingHTTPServer(("127.0.0.1", 0), SupervisorHandler)
                threading.Thread(target=proxy_supervisor.serve_forever, daemon=True).start()
                context = launch(proxy_program, proxy_tmp_path, (proxy_supervisor, None))
                proxy_process, proxy_log = context.__enter__()
                proxy_context = context
                proxy_base = await asyncio.to_thread(listening_url, proxy_process, proxy_log)
                base = proxy_base.removesuffix("/v1")
                expected_roles = {"both": "TTS, STT", "tts": "TTS", "asr": "STT"}[roles]
                assert f"roles: {expected_roles}" in proxy_log.read_text()
            for path in ["/healthz", "/readyz", "/v1/voices"]:
                async with client.get(base + path) as response:
                    expected = 503 if roles == "asr" and path.endswith("voices") else 200
                    assert response.status == expected, await response.text()
                    assert "application/json" in response.headers["Content-Type"]
                    if path.endswith("voices") and expected == 200:
                        assert (await response.json())["voices"][0]["id"] == "test"
            async with client.post(base + "/v1/audio/speech", json={
                "model": "wyoming", "voice": "test", "input": "Hello", "response_format": "wav",
            }) as response:
                audio = await response.read()
                assert response.status == (503 if roles == "asr" else 200), audio
                if roles != "asr":
                    assert response.headers["Content-Type"].startswith("audio/wav")
                    with wave.open(io.BytesIO(audio)) as wav:
                        assert (wav.getnframes(), wav.getframerate(), wav.getnchannels()) == (
                            1600, 16000, 1,
                        )
                else:
                    buffer = io.BytesIO()
                    with wave.open(buffer, "wb") as wav:
                        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                        wav.writeframes(b"\0\0" * 1600)
                    audio = buffer.getvalue()
            form = FormData()
            form.add_field("model", "whisper-1")
            form.add_field("file", audio, filename="sample.wav", content_type="audio/wav")
            async with client.post(base + "/v1/audio/transcriptions", data=form) as response:
                assert response.status == (503 if roles == "tts" else 200), await response.text()
                if roles != "tts":
                    assert (await response.json())["text"] == "Synthetic test"
    finally:
        if proxy_context is not None:
            await asyncio.to_thread(proxy_context.__exit__, None, None, None)
        if proxy_supervisor is not None:
            await asyncio.to_thread(proxy_supervisor.shutdown)
            proxy_supervisor.server_close()
        if manager is not None:
            manager.stop()
            await asyncio.to_thread(manager.join, 15)
        if frontend is not None:
            await asyncio.to_thread(frontend.shutdown)
            frontend.server_close()
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()
        for server in servers:
            server.close()
            await server.wait_closed()
