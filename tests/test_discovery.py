from __future__ import annotations

import json
from pathlib import Path

import pytest

from support import TOKEN_SENTINEL, FakeWyomingServer

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_supervisor_inventory_keeps_only_installed_running_supported_apps(discovery):
    inventory = discovery.parse_supervisor_addons(fixture_json("supervisor_addons.json"))

    assert [addon["slug"] for addon in inventory] == ["core_piper", "core_whisper"]


def test_supervisor_inventory_parses_nested_shape_and_skips_unknown_or_unavailable(discovery):
    payload = {
        "data": {
            "addons": [
                {"slug": "core_piper", "state": "started"},
                {"slug": "core_whisper", "state": "stopped"},
                {"slug": "community_wyoming_tts", "state": "started"},
                {"slug": "core_piper", "state": "started", "installed": False},
            ]
        }
    }

    assert [item["slug"] for item in discovery.parse_supervisor_addons(payload)] == [
        "core_piper"
    ]


def test_slug_dns_preserves_full_slug_and_normalizes_underscores(discovery):
    assert discovery.slug_to_dns_name("community_wyoming_tts") == "community-wyoming-tts"


def test_invalid_supervisor_inventory_is_rejected(discovery):
    with pytest.raises(discovery.DiscoveryError, match="supervisor_invalid_inventory"):
        discovery.parse_supervisor_addons({"data": {"addons": "not-a-list"}})


def test_role_selection_requires_explicit_choice_when_multiple_candidates(discovery):
    piper = discovery.ProviderEndpoint(
        "core_piper", "Piper", "core-piper", 10200, frozenset({"tts"})
    )
    other = discovery.ProviderEndpoint(
        "community_piper", "Community Piper", "community-piper", 10200, frozenset({"tts"})
    )
    providers = [piper, other]

    ambiguous = discovery.select_provider("tts", "auto", providers)
    explicit = discovery.select_provider("tts", "community_piper", providers)
    disabled = discovery.select_provider("tts", "none", providers)
    unavailable = discovery.select_provider("tts", "missing_piper", providers)

    assert ambiguous.status == "ambiguous" and ambiguous.provider is None
    assert {item.slug for item in ambiguous.candidates} == {"core_piper", "community_piper"}
    assert explicit.status == "selected" and explicit.provider == other
    assert disabled.status == "disabled" and disabled.provider is None
    assert unavailable.status == "unavailable" and unavailable.provider is None


def test_selection_does_not_confuse_tts_and_asr_roles(discovery):
    tts = discovery.ProviderEndpoint(
        "core_piper", "Piper", "core-piper", 10200, frozenset({"tts"})
    )
    asr = discovery.ProviderEndpoint(
        "core_whisper", "Whisper", "core-whisper", 10300, frozenset({"asr"})
    )

    assert discovery.select_provider("tts", "auto", [tts, asr]).provider == tts
    assert discovery.select_provider("asr", "auto", [tts, asr]).provider == asr


class FixtureSupervisor:
    def __init__(self, discovery_module, inventory, info_by_slug):
        self.discovery_module = discovery_module
        self.inventory = discovery_module.parse_supervisor_addons(inventory)
        self.info_by_slug = info_by_slug

    def list_addons(self):
        return self.inventory

    def get_addon_info(self, slug):
        return self.info_by_slug[slug]


def test_discovery_probes_internal_ports_not_published_host_ports(discovery):
    inventory = fixture_json("supervisor_addons.json")
    info = {
        "core_piper": {
            "name": "Piper",
            "state": "started",
            "ports": {"10200/tcp": 45678},
        },
        "core_whisper": {
            "name": "Whisper",
            "state": "started",
            "ports": {"10300/tcp": 45679},
        },
    }
    calls = []

    def probe(host, port):
        calls.append((host, port))
        return frozenset({"tts"}) if host == "core-piper" else frozenset({"asr"})

    providers, warnings = discovery.discover_providers(
        FixtureSupervisor(discovery, inventory, info), probe
    )

    assert warnings == []
    assert calls == [("core-piper", 10200), ("core-whisper", 10300)]
    assert [(provider.host, provider.port) for provider in providers] == [
        ("core-piper", 10200),
        ("core-whisper", 10300),
    ]


def test_declared_port_metadata_must_match_adapter_but_host_mapping_does_not_select_port(discovery):
    adapter = discovery.OFFICIAL_ADAPTERS["core_piper"]
    info = fixture_json("supervisor_addon_info.json")["data"]

    assert discovery.adapter_port_is_declared(info, adapter)
    assert adapter.internal_port == 10200
    assert info["ports"]["10200/tcp"] == 45678
    assert not discovery.adapter_port_is_declared(
        {"ports": {"10201/tcp": 10200}}, adapter
    )


def test_wyoming_describe_uses_real_json_line_frame_and_supports_data_length(discovery):
    with FakeWyomingServer() as server:
        capabilities = discovery.probe_wyoming(server.host, server.port, timeout=1)
        assert server.received.wait(1)

    assert server.request == {"type": "describe", "version": "1.5.0"}
    assert capabilities == frozenset({"tts", "asr"})


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (b"not-json\n", "wyoming_invalid_header"),
        (b'{"type":"audio-start"}\n', "wyoming_describe_not_info"),
        (b'{"type":"info","data_length":true}\n', "wyoming_invalid_data_length"),
        (b'{"type":"info","data_length":524289}\n', "wyoming_data_too_large"),
    ],
)
def test_wyoming_malformed_or_oversized_responses_are_rejected(discovery, response, error):
    with FakeWyomingServer(response=response) as server:
        with pytest.raises(discovery.DiscoveryError, match=error):
            discovery.probe_wyoming(server.host, server.port, timeout=1)


def test_wyoming_probe_has_a_bounded_read_timeout(discovery):
    import time

    with FakeWyomingServer(stall=True, accept_timeout=1) as server:
        started = time.monotonic()
        with pytest.raises(discovery.DiscoveryError):
            discovery.probe_wyoming(server.host, server.port, timeout=0.1)
        assert time.monotonic() - started < 0.75


def test_secret_environment_is_removed_before_child_launch(discovery):
    provider = discovery.ProviderEndpoint(
        "core_piper", "Piper", "core-piper", 10200, frozenset({"tts"})
    )
    child_env = discovery.build_child_environment(
        {
            "SUPERVISOR_TOKEN": TOKEN_SENTINEL,
            "CUSTOM_SECRET_VALUE": TOKEN_SENTINEL,
            "TTS_HOST": "stale-host",
            "TTS_PORT": "9",
            "KEEP_ME": "harmless",
        },
        {"tts": provider},
    )

    assert TOKEN_SENTINEL not in child_env.values()
    assert "SUPERVISOR_TOKEN" not in child_env
    assert "CUSTOM_SECRET_VALUE" not in child_env
    assert child_env["TTS_HOST"] == "core-piper"
    assert child_env["TTS_PORT"] == "10200"
    assert child_env["KEEP_ME"] == "harmless"
