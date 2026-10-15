"""Provider observability must not weaken protocol, privacy, or restart behavior."""
import logging

from support import TOKEN_SENTINEL, FakeWyomingServer, encode_wyoming_event


def test_description_lists_installed_voices_without_unrelated_metadata(discovery):
    event = {"type": "info", "data": {"tts": [
        {"installed": True, "voices": [
            {"name": "en_US-demo-medium", "id": "not-the-gateway-voice-ID",
             "languages": ["en-US"], "installed": True,
             "description": "private-content-must-not-be-logged"},
            {"name": "not-installed", "languages": ["en"], "installed": False},
        ], "models": [{"name": "not-a-voice-model"}],
         "programs": [{"name": "not-a-voice-program"}]},
        {"installed": False, "voices": [{"name": "unavailable-program"}]},
    ]}}
    description = discovery.description_from_info_event(event)
    assert description.capabilities == frozenset({"tts"})
    assert description.voices == (
        discovery.VoiceInfo("en_US-demo-medium", ("en-US",)),
    )


def test_malformed_optional_voice_metadata_keeps_provider_usable_and_is_bounded(discovery):
    event = {"type": "info", "data": {"tts": [{"voices": [
        None, 42, {"name": 5}, {"name": ""},
        {"name": "normal", "languages": "not-a-list"},
    ]}]}}
    assert discovery.description_from_info_event(event).capabilities == frozenset({"tts"})
    voices = [{"name": f"voice-{i}-" + "x" * 1000, "languages": ["en" * 100] * 50}
              for i in range(200)]
    description = discovery.description_from_info_event({
        "type": "info", "data": {"tts": [{"voices": voices}, {"voices": voices}]},
    })
    assert 0 < len(description.voices) <= 128
    assert all(len(voice.name) <= 256 and len(voice.languages) <= 16
               for voice in description.voices)
    assert all(len(language) <= 64 for voice in description.voices for language in voice.languages)
    assert all(voice.name.endswith("…") for voice in description.voices)


def test_real_describe_carries_voice_details_in_one_probe(discovery):
    response = encode_wyoming_event("info", {"tts": [{"voices": [
        {"name": "en_GB-demo", "languages": ["en-GB"]},
    ]}]})
    with FakeWyomingServer(response=response) as server:
        description = discovery.describe_wyoming(server.host, server.port, timeout=1)
        assert server.received.wait(1)
    assert description.voices == (discovery.VoiceInfo("en_GB-demo", ("en-GB",)),)


def test_connection_and_voice_logs_are_transitions_not_every_poll(
    discovery, monkeypatch, caplog,
):
    voice_a = discovery.VoiceInfo("en_US-demo", ("en-US",))
    voice_b = discovery.VoiceInfo("en_GB-demo", ("en-GB",))
    catalog = [voice_a, voice_b]
    piper_running = True
    launches = []

    class Inventory:
        def __init__(self, **_kwargs):
            pass

        def list_addons(self):
            return [{"slug": slug, "state": "started"}
                    for slug in ("core_piper", "core_whisper")]

        def get_addon_info(self, slug):
            port = 10200 if slug == "core_piper" else 10300
            running = piper_running or slug != "core_piper"
            return {"name": slug, "state": "started" if running else "stopped",
                    "ports": {f"{port}/tcp": None}}

    class Process:
        def __init__(self, pid):
            self.pid = pid
            self.returncode = None

        def poll(self):
            return self.returncode

        def wait(self, **_kwargs):
            self.returncode = 0 if self.returncode is None else self.returncode
            return self.returncode

    def popen(*_args, **_kwargs):
        child = Process(2000000000 + len(launches))
        launches.append(child)
        return child

    def killpg(pid, _signal):
        next(child for child in launches if child.pid == pid).returncode = -15

    def probe(host, _port):
        return discovery.WyomingDescription(
            frozenset({"tts"}) if host == "core-piper" else frozenset({"asr"}),
            tuple(catalog) if host == "core-piper" else (),
        )

    monkeypatch.setattr(discovery.os, "killpg", killpg)
    manager = discovery.GatewaySupervisor(
        discovery.GatewayOptions(), TOKEN_SENTINEL,
        supervisor_factory=Inventory, probe=probe, popen_factory=popen,
        gateway_health_probe=lambda: True, parent_environment={},
    )
    caplog.set_level(logging.INFO)
    try:
        manager.reconcile_once()
        for expected in ("core_piper", "core-piper", "10200", "core_whisper", "10300",
                         "en_US-demo", "en-US", "en_GB-demo", "en-GB"):
            assert expected in caplog.text
        assert "TTS" in caplog.text.upper() and "STT" in caplog.text.upper()
        assert TOKEN_SENTINEL not in caplog.text
        assert len(launches) == 1

        caplog.clear()
        manager.reconcile_once()
        assert not caplog.records

        catalog.reverse()
        manager.reconcile_once()
        assert not caplog.records  # Catalog order is not a server change.

        catalog[:] = [discovery.VoiceInfo("fr_FR-demo", ("fr-FR",))]
        manager.reconcile_once()
        assert "fr_FR-demo" in caplog.text
        assert len(launches) == 1  # Voice changes do not restart the upstream child.

        caplog.clear()
        piper_running = False
        manager.reconcile_once()
        assert "core_piper" in caplog.text
        assert manager.status.snapshot()["tts"]["status"] == "waiting"
        assert len(launches) == 2

        caplog.clear()
        piper_running = True
        manager.reconcile_once()
        assert "core_piper" in caplog.text and "fr_FR-demo" in caplog.text
        assert len(launches) == 3

        catalog[:] = [discovery.VoiceInfo("de_DE-demo", ("de-DE",))]
        manager._publish_discovery_failure("supervisor_unavailable")
        assert manager.status.snapshot()["ready"] is True
        assert manager.status.snapshot()["tts"]["provider"]["slug"] == "core_piper"
        assert manager.status.snapshot()["tts"]["provider"]["voices"] == [
            {"name": "de_DE-demo", "languages": ["de-DE"]},
        ]
        assert "de_DE-demo" in caplog.text and len(launches) == 3
    finally:
        manager._stop_gateway()


def test_advertised_metadata_cannot_inject_log_lines(discovery, caplog):
    description = discovery.description_from_info_event({"type": "info", "data": {"tts": [
        {"voices": [{"name": "voice\nFORGED\x1b[31m\u202e", "languages": ["en\rUS"]}],
         "description": "private-content-must-not-be-logged", "token": TOKEN_SENTINEL},
    ]}})
    endpoint = discovery.ProviderEndpoint(
        "core_piper", "Piper\nForgedName", "core-piper", 10200,
        description.capabilities, description.voices,
    )
    manager = discovery.GatewaySupervisor(discovery.GatewayOptions(), TOKEN_SENTINEL)
    caplog.set_level(logging.INFO)
    manager._log_discovery_changes([endpoint])
    assert caplog.records
    for record in caplog.records:
        message = record.getMessage()
        assert not any(control in message for control in ("\n", "\r", "\x1b", "\u202e"))
    assert TOKEN_SENTINEL not in caplog.text
    assert "private-content-must-not-be-logged" not in caplog.text
