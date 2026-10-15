"""Optional real-gateway smoke test with synthetic Wyoming audio providers.

Install the pinned gateway and runtime requirements to enable this test. This
proves protocol translation, not real Piper/Whisper or HAOS integration.
"""
import asyncio
import io
import os
import socket
import sys
import threading
import traceback
import wave
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


async def _exercise(wrapped, roles):
    from aiohttp import ClientSession, FormData
    from wyoming.audio import AudioChunk, AudioStart, AudioStop
    from wyoming.event import Event, async_read_event, async_write_event

    attribution = {"name": "synthetic test", "url": "https://example.com"}
    voice = {"name": "test", "description": "Test", "languages": ["en"],
             "attribution": attribution, "installed": True}

    async def provider(reader, writer):
        try:
            while event := await async_read_event(reader):
                if event.type == "describe":
                    await async_write_event(Event(type="info", data={
                        "tts": [{"name": "mock", "attribution": attribution,
                                 "installed": True, "voices": [voice]}],
                        "asr": [{"name": "mock", "attribution": attribution,
                                 "installed": True, "models": [voice]}],
                    }), writer)
                elif event.type == "synthesize":
                    for response in [AudioStart(rate=16000, width=2, channels=1).event(),
                                     AudioChunk(rate=16000, width=2, channels=1,
                                                audio=b"\0\0" * 1600).event(),
                                     AudioStop().event()]:
                        await async_write_event(response, writer)
                elif event.type == "audio-stop":
                    await async_write_event(
                        Event(type="transcript", data={"text": "Synthetic test"}), writer,
                    )
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(provider, "127.0.0.1", 0)
    provider_port = str(server.sockets[0].getsockname()[1])
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, TTS_HOST="127.0.0.1", TTS_PORT=provider_port,
               STT_HOST="127.0.0.1", STT_PORT=provider_port,
               HOST="127.0.0.1", PORT=str(port), PREFIX="/v1", LOG_LEVEL="WARNING")
    process = None
    manager = frontend = None
    if wrapped:
        from support import import_discovery_module

        discovery = import_discovery_module()
        discovery.GATEWAY_PORT = port
        discovery.OFFICIAL_ADAPTERS = {
            slug: discovery.ProviderAdapter(slug, int(provider_port))
            for slug in ("core_piper", "core_whisper")
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
                        "ports": {f"{provider_port}/tcp": None}}

        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
        manager = discovery.GatewaySupervisor(
            discovery.GatewayOptions(
                tts_provider="none" if roles == "asr" else "core_piper",
                stt_provider="none" if roles == "tts" else "core_whisper", log_level="INFO",
            ), token="private-sentinel",
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
        async with ClientSession() as client:
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
        server.close()
        await server.wait_closed()
