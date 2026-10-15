"""Regression cases independently derived from the runtime review findings."""
import pytest


def test_renamed_piper_uses_internal_port_and_requires_selection(discovery):
    class Inventory:
        def list_addons(self):
            return [{"slug": slug, "state": "started"}
                    for slug in ("core_piper", "abc123_piper")]

        def get_addon_info(self, slug):
            return {"name": slug, "state": "started", "ports": {"10200/tcp": 30000}}

    calls = []

    def probe(host, port):
        calls.append((host, port))
        return frozenset({"tts"})

    providers, warnings = discovery.discover_providers(Inventory(), probe=probe)
    assert warnings == []
    assert calls == [("core-piper", 10200), ("abc123-piper", 10200)]
    assert discovery.select_provider("tts", "auto", providers).status == "ambiguous"
    assert discovery.select_provider("tts", "abc123_piper", providers).provider.slug == (
        "abc123_piper"
    )


def test_explicit_unknown_slug_needs_unique_wyoming_port_metadata(discovery):
    class Inventory:
        def list_addons(self):
            return [{"slug": "abc_speech", "state": "started"}]

        def get_addon_info(self, _slug):
            return {"state": "started", "ports": {"12345/tcp": 30000},
                    "ports_description": {"12345/tcp": "Wyoming protocol"}}

    calls = []

    def probe(host, port):
        calls.append((host, port))
        return frozenset({"asr"})

    assert discovery.discover_providers(Inventory(), probe=probe)[0] == []
    assert calls == []
    providers, _warnings = discovery.discover_providers(
        Inventory(), probe=probe, explicit_slugs=["abc_speech"],
    )
    assert providers[0].slug == "abc_speech"
    assert calls == [("abc-speech", 12345)]
    assert discovery.metadata_wyoming_port({
        "ports": {"12345/tcp": None, "12346/tcp": None},
        "ports_description": {"12345/tcp": "Wyoming", "12346/tcp": "Wyoming"},
    }) is None


@pytest.mark.parametrize("failure", ["startup", "proxy", "inventory"])
def test_stalled_or_timed_out_child_is_not_left_running(discovery, monkeypatch, failure):
    class Inventory:
        def __init__(self, **_kwargs):
            pass

        def list_addons(self):
            return [{"slug": "core_piper", "state": "started"}]

        def get_addon_info(self, _slug):
            return {"state": "started", "ports": {"10200/tcp": None}}

    class Process:
        pid = 2000000000
        returncode = None
        waited = False

        def poll(self):
            return self.returncode

        def wait(self, **_kwargs):
            self.waited = True
            return self.returncode

    child = Process()
    monkeypatch.setattr(discovery.os, "killpg", lambda *_args: setattr(child, "returncode", -15))
    manager = discovery.GatewaySupervisor(
        discovery.GatewayOptions(), token="synthetic",
        supervisor_factory=Inventory, probe=lambda *_args: frozenset({"tts"}),
        gateway_health_probe=lambda: failure != "startup",
        popen_factory=lambda *_args, **_kwargs: child, parent_environment={},
    )
    manager.reconcile_once()
    assert manager.status.snapshot()["ready"] is (failure != "startup")
    if failure == "startup":
        manager._startup_deadline_at = 0
    elif failure == "proxy":
        manager.request_recycle()
        assert child.poll() is None  # Request thread only signals; manager owns reaping.
    else:
        manager._publish_discovery_failure("supervisor_unavailable")
        assert child.poll() is None  # Healthy speech must survive an inventory outage.
        assert manager.status.snapshot()["ready"] is True

        def unavailable(*_args):
            raise discovery.DiscoveryError("wyoming_unavailable")

        manager._probe = unavailable
        manager._publish_discovery_failure("supervisor_unavailable")
        assert manager.status.snapshot()["ready"] is False
    manager._monitor_child()
    assert child.poll() == -15
    assert child.waited
    assert manager._process is None
