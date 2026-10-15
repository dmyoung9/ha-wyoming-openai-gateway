#!/usr/bin/env python3
"""Discover bounded Home Assistant Wyoming providers and supervise the gateway.

The frontend deliberately stays up when no provider is ready.  It serves
health/status endpoints on 8555 and proxies API traffic to the upstream
Wyoming-OpenAI-Gateway child on loopback port 8556 when that child is usable.
Discovery is conservative: it supports known adapters, safe slug conventions,
and explicitly selected add-ons with verified Wyoming port metadata. Supervisor
inventory is not a universal Wyoming service registry and arbitrary ports are
never scanned.
"""

from __future__ import annotations

import copy
import http.client
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any

LOGGER = logging.getLogger("wyoming_openai_gateway.wrapper")

SUPERVISOR_URL = "http://supervisor"
SUPERVISOR_TIMEOUT_SECONDS = 5.0
SUPERVISOR_RESPONSE_LIMIT = 1024 * 1024
SUPERVISOR_ADDON_LIMIT = 256
SUPERVISOR_CANDIDATE_LIMIT = 4
OPTIONS_FILE = "/data/options.json"
OPTIONS_FILE_LIMIT = 64 * 1024

FRONTEND_HOST = "0.0.0.0"
FRONTEND_PORT = 8555
GATEWAY_HOST = "127.0.0.1"
GATEWAY_PORT = 8556
GATEWAY_PRIVACY_SHIM = """\
import logging

for logger_name in (
    "wyoming_openai_gateway.gateway",
    "wyoming_openai_gateway.app",
    "wyoming_openai_gateway.wyoming_client",
    "uvicorn.access",
):
    logging.getLogger(logger_name).disabled = True

from wyoming_openai_gateway.__main__ import main

main()
"""

WYOMING_TIMEOUT_SECONDS = 2.5
WYOMING_LINE_LIMIT = 64 * 1024
WYOMING_DATA_LIMIT = 512 * 1024
WYOMING_PAYLOAD_LIMIT = 1024

MAX_REQUEST_BYTES = 25 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_CONCURRENT_REQUESTS = 4
HTTP_SOCKET_TIMEOUT_SECONDS = 35.0
UPSTREAM_HTTP_TIMEOUT_SECONDS = 300.0
GATEWAY_HEALTH_TIMEOUT_SECONDS = 1.5
GATEWAY_STARTUP_TIMEOUT_SECONDS = 30.0
GATEWAY_HEALTH_BODY_LIMIT = 64 * 1024
DISCOVERY_INTERVAL_SECONDS = 30.0
RETRY_BASE_SECONDS = 5.0
RETRY_MAX_SECONDS = 60.0
CHILD_STOP_TIMEOUT_SECONDS = 10.0
MANAGER_SHUTDOWN_TIMEOUT_SECONDS = 50.0

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_PROVIDER_OPTION_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SECRET_ENV_PARTS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL")
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "proxy-connection",
}


@dataclass(frozen=True, slots=True)
class ProviderAdapter:
    """A verified internal endpoint convention for a Wyoming add-on."""

    addon_slug: str
    internal_port: int
    require_port_metadata: bool = False


# These internal ports are declared by the official Home Assistant add-on
# manifests.  Supervisor's `ports` values are host-port mappings (often null),
# and are intentionally never used as the in-container destination port.
OFFICIAL_ADAPTERS: Mapping[str, ProviderAdapter] = {
    "core_piper": ProviderAdapter("core_piper", 10200),
    "core_whisper": ProviderAdapter("core_whisper", 10300),
}
DISCOVERY_SCOPE = (
    "Known Piper/Whisper adapters, suffix-based adapters with declared ports, "
    "and explicitly selected add-ons with a verified Wyoming port label; "
    "not a universal Wyoming registry."
)


class DiscoveryError(Exception):
    """A safe, non-sensitive discovery failure category."""

    def __init__(self, category: str, status_code: int | None = None):
        super().__init__(category)
        self.category = category
        self.status_code = status_code


class SupervisorClient:
    """Small bounded client for the Supervisor add-on inventory API."""

    def __init__(
        self,
        token: str,
        base_url: str = SUPERVISOR_URL,
        timeout: float = SUPERVISOR_TIMEOUT_SECONDS,
        response_limit: int = SUPERVISOR_RESPONSE_LIMIT,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._response_limit = response_limit

    def get_json(self, path: str) -> Any:
        if not path.startswith("/") or "\r" in path or "\n" in path:
            raise DiscoveryError("invalid_supervisor_path")

        request = urllib.request.Request(
            f"{self._base_url}{path}",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                if response.status != 200:
                    raise DiscoveryError("supervisor_http_error", response.status)
                raw = response.read(self._response_limit + 1)
        except urllib.error.HTTPError as error:
            # Never include exception text or response bodies: they are outside
            # our trust boundary and could echo authorization material.
            raise DiscoveryError("supervisor_http_error", error.code) from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
            raise DiscoveryError("supervisor_unavailable") from None

        if len(raw) > self._response_limit:
            raise DiscoveryError("supervisor_response_too_large")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DiscoveryError("supervisor_invalid_json") from None

    def list_addons(self) -> list[dict[str, Any]]:
        # The default Supervisor role permits /.../info, but rejects /addons.
        # The v1 Supervisor info response contains the installed-app inventory
        # with running states, without requiring manager/admin privileges.
        return parse_supervisor_addons(self.get_json("/supervisor/info"), include_unknown=True)

    def get_addon_info(self, slug: str) -> dict[str, Any]:
        if not _SLUG_RE.fullmatch(slug):
            raise DiscoveryError("invalid_addon_slug")
        quoted_slug = urllib.parse.quote(slug, safe="")
        payload = self.get_json(f"/addons/{quoted_slug}/info")
        return unwrap_supervisor_data(payload, expected="object")


def unwrap_supervisor_data(payload: Any, expected: str | None = None) -> Any:
    """Unwrap the Supervisor's `{data: ...}` envelope with shape validation."""

    if not isinstance(payload, dict):
        raise DiscoveryError("supervisor_invalid_shape")
    data = payload.get("data", payload)
    if expected == "object" and not isinstance(data, dict):
        raise DiscoveryError("supervisor_invalid_shape")
    if expected == "list" and not isinstance(data, list):
        raise DiscoveryError("supervisor_invalid_shape")
    return data


def parse_supervisor_addons(
    payload: Any,
    explicit_slugs: Iterable[str] = (),
    *,
    include_unknown: bool = False,
) -> list[dict[str, Any]]:
    """Return bounded running known/suffix candidates and requested full slugs."""

    data = unwrap_supervisor_data(payload, expected="object")
    addons = data.get("addons")
    if not isinstance(addons, list) or len(addons) > SUPERVISOR_ADDON_LIMIT:
        raise DiscoveryError("supervisor_invalid_inventory")

    explicit = {
        slug for slug in explicit_slugs if isinstance(slug, str) and _SLUG_RE.fullmatch(slug)
    }
    eligible: list[dict[str, Any]] = []
    for addon in addons:
        if not isinstance(addon, dict):
            continue
        slug = addon.get("slug")
        if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
            continue
        if not include_unknown and not is_plausible_adapter_slug(slug) and slug not in explicit:
            continue
        if addon.get("installed") is False or not is_running_state(addon.get("state")):
            continue
        eligible.append(addon)
    if not include_unknown and len(eligible) > SUPERVISOR_CANDIDATE_LIMIT:
        raise DiscoveryError("provider_candidate_limit_exceeded")
    return eligible


def is_plausible_adapter_slug(slug: str) -> bool:
    """Recognize known official slugs and bounded Piper/Whisper slug suffixes."""

    return slug in OFFICIAL_ADAPTERS or slug.endswith(("_piper", "_whisper"))


def is_running_state(state: Any) -> bool:
    return isinstance(state, str) and state.strip().lower() in {"started", "running"}


def slug_to_dns_name(slug: str) -> str:
    """Normalize a full Supervisor slug to its internal app-network DNS name."""

    if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
        raise ValueError("invalid Supervisor slug")
    return slug.replace("_", "-")


def info_is_running(info: Mapping[str, Any], inventory_state: Any) -> bool:
    """Require an installed, started app while tolerating older info payloads."""

    if info.get("installed") is False:
        return False
    state = info.get("state", inventory_state)
    return is_running_state(state)


def adapter_port_is_declared(info: Mapping[str, Any], adapter: ProviderAdapter) -> bool:
    """Check declared internal port metadata without using a host port value."""

    ports = info.get("ports")
    if ports is None:
        return not adapter.require_port_metadata
    if not isinstance(ports, dict):
        return False
    expected = f"{adapter.internal_port}/tcp"
    return any(str(key).strip().lower() == expected for key in ports)


def metadata_wyoming_port(info: Mapping[str, Any]) -> int | None:
    """Return a unique, declared TCP port explicitly described as Wyoming."""

    ports = info.get("ports")
    descriptions = info.get("ports_description")
    if not isinstance(ports, dict) or not isinstance(descriptions, dict):
        return None

    declared_ports = {str(key).strip().lower() for key in ports}
    candidates: set[int] = set()
    for key, description in descriptions.items():
        if not isinstance(key, str) or not isinstance(description, str):
            continue
        normalized_key = key.strip().lower()
        match = re.fullmatch(r"([1-9][0-9]{0,4})/tcp", normalized_key)
        if not match or "wyoming" not in description.casefold():
            continue
        if normalized_key not in declared_ports:
            continue
        port = int(match.group(1))
        if 1 <= port <= 65535:
            candidates.add(port)
    return next(iter(candidates)) if len(candidates) == 1 else None


def adapter_for_addon(
    slug: str, info: Mapping[str, Any], explicit: bool = False
) -> ProviderAdapter | None:
    """Resolve only known conventions or explicit metadata-backed add-ons."""

    official = OFFICIAL_ADAPTERS.get(slug)
    if official is not None:
        return official
    if slug.endswith("_piper"):
        return ProviderAdapter(slug, 10200, require_port_metadata=True)
    if slug.endswith("_whisper"):
        return ProviderAdapter(slug, 10300, require_port_metadata=True)
    if explicit:
        port = metadata_wyoming_port(info)
        if port is not None:
            return ProviderAdapter(slug, port, require_port_metadata=True)
    return None


@dataclass(frozen=True, slots=True)
class ProviderEndpoint:
    slug: str
    name: str
    host: str
    port: int
    capabilities: frozenset[str]

    def supports(self, role: str) -> bool:
        return role in self.capabilities

    def public_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "capabilities": sorted(self.capabilities),
        }


@dataclass(frozen=True, slots=True)
class ProviderDecision:
    role: str
    status: str
    provider: ProviderEndpoint | None = None
    candidates: tuple[ProviderEndpoint, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "provider": self.provider.public_dict() if self.provider else None,
            "candidates": [item.public_dict() for item in self.candidates],
        }


def select_provider(
    role: str,
    preference: str,
    providers: Iterable[ProviderEndpoint],
) -> ProviderDecision:
    """Select only a unique automatic candidate or a valid explicit slug."""

    if role not in {"tts", "asr"}:
        raise ValueError("unsupported provider role")
    candidates = tuple(
        sorted((item for item in providers if item.supports(role)), key=lambda item: item.slug)
    )
    normalized = preference.strip().lower() if isinstance(preference, str) else "__invalid__"

    if normalized == "none":
        return ProviderDecision(role, "disabled", None, candidates)
    if normalized == "auto":
        if not candidates:
            return ProviderDecision(role, "waiting", None, ())
        if len(candidates) > 1:
            return ProviderDecision(role, "ambiguous", None, candidates)
        return ProviderDecision(role, "selected", candidates[0], candidates)
    if not _PROVIDER_OPTION_RE.fullmatch(normalized):
        return ProviderDecision(role, "invalid_selection", None, candidates)

    selected = [item for item in candidates if item.slug == normalized]
    if len(selected) == 1:
        return ProviderDecision(role, "selected", selected[0], candidates)
    if len(selected) > 1:
        return ProviderDecision(role, "ambiguous", None, candidates)
    return ProviderDecision(role, "unavailable", None, candidates)


def _read_exact(reader: Any, size: int) -> bytes:
    parts: list[bytes] = []
    remaining = size
    while remaining:
        part = reader.read(remaining)
        if not part:
            raise DiscoveryError("wyoming_truncated_response")
        parts.append(part)
        remaining -= len(part)
    return b"".join(parts)


def _parse_wyoming_event(reader: Any) -> dict[str, Any]:
    line = reader.readline(WYOMING_LINE_LIMIT + 1)
    if not line or len(line) > WYOMING_LINE_LIMIT or not line.endswith(b"\n"):
        raise DiscoveryError("wyoming_invalid_header")
    try:
        header = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise DiscoveryError("wyoming_invalid_header") from None
    if not isinstance(header, dict) or not isinstance(header.get("type"), str):
        raise DiscoveryError("wyoming_invalid_header")

    event_data = header.get("data", {})
    if not isinstance(event_data, dict):
        raise DiscoveryError("wyoming_invalid_data")

    data_length = header.get("data_length", 0)
    if isinstance(data_length, bool) or not isinstance(data_length, int) or data_length < 0:
        raise DiscoveryError("wyoming_invalid_data_length")
    if data_length > WYOMING_DATA_LIMIT:
        raise DiscoveryError("wyoming_data_too_large")
    if data_length:
        raw_data = _read_exact(reader, data_length)
        try:
            decoded_data = json.loads(raw_data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DiscoveryError("wyoming_invalid_data") from None
        if not isinstance(decoded_data, dict):
            raise DiscoveryError("wyoming_invalid_data")
        event_data.update(decoded_data)

    payload_length = header.get("payload_length", 0)
    if (
        isinstance(payload_length, bool)
        or not isinstance(payload_length, int)
        or payload_length < 0
    ):
        raise DiscoveryError("wyoming_invalid_payload_length")
    if payload_length > WYOMING_PAYLOAD_LIMIT:
        raise DiscoveryError("wyoming_payload_too_large")
    if payload_length:
        _read_exact(reader, payload_length)

    return {"type": header["type"], "data": event_data}


def capabilities_from_info_event(event: Mapping[str, Any]) -> frozenset[str]:
    """Extract installed TTS/ASR capabilities from a Wyoming `info` event."""

    if event.get("type") != "info":
        raise DiscoveryError("wyoming_describe_not_info")
    data = event.get("data")
    if not isinstance(data, dict):
        raise DiscoveryError("wyoming_invalid_info")

    capabilities: set[str] = set()
    for event_key, role in (("tts", "tts"), ("asr", "asr")):
        services = data.get(event_key, [])
        if not isinstance(services, list) or len(services) > 128:
            raise DiscoveryError("wyoming_invalid_info")
        # The Wyoming Info schema represents each service/program as an object
        # with `installed`; older servers may omit that field, so a well-shaped
        # entry is accepted unless it explicitly says it is unavailable.
        if any(
            isinstance(service, dict) and service.get("installed") is not False
            for service in services
        ):
            capabilities.add(role)
    if not capabilities:
        raise DiscoveryError("wyoming_no_speech_capability")
    return frozenset(capabilities)


def probe_wyoming(host: str, port: int, timeout: float = WYOMING_TIMEOUT_SECONDS) -> frozenset[str]:
    """Send one bounded Wyoming `describe` event and return advertised roles."""

    try:
        with socket.create_connection((host, port), timeout=timeout) as connection:
            connection.settimeout(timeout)
            reader = connection.makefile("rb")
            try:
                connection.sendall(b'{"type":"describe","version":"1.5.0"}\n')
                event = _parse_wyoming_event(reader)
            finally:
                reader.close()
    except DiscoveryError:
        raise
    except (OSError, TimeoutError, ValueError):
        raise DiscoveryError("wyoming_unavailable") from None
    return capabilities_from_info_event(event)


def probe_gateway_health(
    host: str | None = None,
    port: int | None = None,
    timeout: float = GATEWAY_HEALTH_TIMEOUT_SECONDS,
) -> bool:
    """Verify the local upstream accepts HTTP health requests within a bound."""

    host = host or GATEWAY_HOST
    port = GATEWAY_PORT if port is None else port
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request("GET", "/healthz", headers={"Connection": "close"})
        response = connection.getresponse()
        if response.status != 200:
            return False
        body = response.read(GATEWAY_HEALTH_BODY_LIMIT + 1)
        return len(body) <= GATEWAY_HEALTH_BODY_LIMIT
    except (OSError, TimeoutError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def discover_providers(
    supervisor: SupervisorClient,
    probe: Callable[[str, int], frozenset[str]] = probe_wyoming,
    explicit_slugs: Iterable[str] = (),
) -> tuple[list[ProviderEndpoint], list[str]]:
    """Inventory and verify bounded adapters plus explicitly selected metadata."""

    explicit = {
        slug for slug in explicit_slugs if isinstance(slug, str) and _SLUG_RE.fullmatch(slug)
    }
    addons = supervisor.list_addons()
    candidate_addons = [
        addon
        for addon in addons
        if isinstance(addon.get("slug"), str)
        and _SLUG_RE.fullmatch(addon["slug"])
        and (is_plausible_adapter_slug(addon["slug"]) or addon["slug"] in explicit)
    ]
    if len(candidate_addons) > SUPERVISOR_CANDIDATE_LIMIT:
        raise DiscoveryError("provider_candidate_limit_exceeded")
    endpoints: list[ProviderEndpoint] = []
    warnings: list[str] = []
    for addon in candidate_addons:
        slug = addon.get("slug")
        if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
            continue
        try:
            info = supervisor.get_addon_info(slug)
        except DiscoveryError as error:
            warnings.append(f"{slug}:{error.category}")
            continue
        if not info_is_running(info, addon.get("state")):
            continue
        adapter = adapter_for_addon(slug, info, explicit=slug in explicit)
        if adapter is None:
            if slug in explicit:
                warnings.append(f"{slug}:no_verified_wyoming_port")
            continue
        if not adapter_port_is_declared(info, adapter):
            metadata_port = metadata_wyoming_port(info) if slug in explicit else None
            if metadata_port is None:
                warnings.append(f"{slug}:adapter_port_not_declared")
                continue
            adapter = ProviderAdapter(slug, metadata_port, require_port_metadata=True)
        host = slug_to_dns_name(slug)
        try:
            capabilities = probe(host, adapter.internal_port)
        except DiscoveryError as error:
            warnings.append(f"{slug}:{error.category}")
            continue
        except Exception as error:  # Test doubles and unexpected probe implementations.
            warnings.append(f"{slug}:probe_{type(error).__name__}")
            continue

        name = info.get("name", addon.get("name", slug))
        if not isinstance(name, str) or not name:
            name = slug
        endpoints.append(
            ProviderEndpoint(slug, name[:128], host, adapter.internal_port, capabilities)
        )
    return endpoints, warnings


@dataclass(frozen=True, slots=True)
class GatewayOptions:
    discovery: bool = True
    tts_provider: str = "auto"
    stt_provider: str = "auto"
    log_level: str = "INFO"
    errors: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_mapping(cls, value: Any) -> GatewayOptions:
        if not isinstance(value, dict):
            return cls(errors=("options_not_object",))
        errors: list[str] = []

        discovery = value.get("discovery", True)
        if not isinstance(discovery, bool):
            errors.append("discovery_not_boolean")
            discovery = True

        def selection(key: str) -> str:
            raw = value.get(key, "auto")
            if not isinstance(raw, str):
                errors.append(f"{key}_invalid")
                return "__invalid__"
            raw = raw.strip().lower()
            if raw in {"auto", "none"} or _PROVIDER_OPTION_RE.fullmatch(raw):
                return raw
            errors.append(f"{key}_invalid")
            return "__invalid__"

        tts_provider = selection("tts_provider")
        stt_provider = selection("stt_provider")
        log_level = value.get("log_level", "INFO")
        if not isinstance(log_level, str) or log_level.upper() not in {
            "DEBUG",
            "INFO",
            "WARNING",
            "ERROR",
        }:
            errors.append("log_level_invalid")
            log_level = "INFO"
        else:
            log_level = log_level.upper()
        return cls(discovery, tts_provider, stt_provider, log_level, tuple(errors))


def load_options(
    path: str = OPTIONS_FILE, max_bytes: int = OPTIONS_FILE_LIMIT
) -> tuple[GatewayOptions, str | None]:
    """Read app options without exposing file contents in diagnostics."""

    try:
        with open(path, "rb") as options_file:
            raw = options_file.read(max_bytes + 1)
    except FileNotFoundError:
        return GatewayOptions(), None
    except OSError:
        return GatewayOptions(), "options_unavailable"
    if len(raw) > max_bytes:
        return GatewayOptions(), "options_too_large"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return GatewayOptions(), "options_invalid_json"
    options = GatewayOptions.from_mapping(payload)
    return options, "options_invalid_values" if options.errors else None


class StatusStore:
    """Thread-safe JSON-serializable runtime status for the HTTP frontend."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: dict[str, Any] = {
            "status": "starting",
            "ready": False,
            "updated_at": utc_timestamp(),
            "discovery_enabled": True,
            "discovery_scope": DISCOVERY_SCOPE,
            "last_error": None,
            "tts": {"status": "waiting", "provider": None, "candidates": []},
            "stt": {"status": "waiting", "provider": None, "candidates": []},
            "gateway": {
                "running": False,
                "healthy": False,
                "upstream": f"http://{GATEWAY_HOST}:{GATEWAY_PORT}",
            },
        }

    def replace(self, value: Mapping[str, Any]) -> None:
        with self._lock:
            self._value = copy.deepcopy(dict(value))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._value)


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def build_child_environment(
    parent: Mapping[str, str],
    selected: Mapping[str, ProviderEndpoint],
    log_level: str = "INFO",
    gateway_port: int = GATEWAY_PORT,
) -> dict[str, str]:
    """Construct child environment and remove token/secret-bearing variables."""

    environment = {
        key: value
        for key, value in parent.items()
        if not any(secret_part in key.upper() for secret_part in _SECRET_ENV_PARTS)
    }
    for key in ("TTS_HOST", "TTS_PORT", "STT_HOST", "STT_PORT"):
        environment.pop(key, None)
    environment.update(
        {
            "HOST": GATEWAY_HOST,
            "PORT": str(gateway_port),
            "PREFIX": "/v1",
            "DEBUG": "false",
            "LOG_LEVEL": log_level,
        }
    )
    tts = selected.get("tts")
    stt = selected.get("asr")
    if tts:
        environment["TTS_HOST"] = tts.host
        environment["TTS_PORT"] = str(tts.port)
    if stt:
        environment["STT_HOST"] = stt.host
        environment["STT_PORT"] = str(stt.port)
    return environment


def _decision_for_status(decision: ProviderDecision) -> dict[str, Any]:
    value = decision.public_dict()
    # Keep the stable status shape simple for clients while preserving every
    # candidate so an ambiguous choice is actionable.
    return value


class GatewaySupervisor:
    """Periodic discovery, child lifecycle, and immutable frontend status."""

    def __init__(
        self,
        options: GatewayOptions,
        token: str | None,
        status: StatusStore | None = None,
        supervisor_factory: Callable[..., SupervisorClient] = SupervisorClient,
        probe: Callable[[str, int], frozenset[str]] = probe_wyoming,
        gateway_health_probe: Callable[[], bool] = probe_gateway_health,
        popen_factory: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
        gateway_command: str | None = None,
        supervisor_url: str = SUPERVISOR_URL,
        parent_environment: Mapping[str, str] | None = None,
        options_error: str | None = None,
    ) -> None:
        self.options = options
        self.token = token
        self.status = status or StatusStore()
        self._supervisor_factory = supervisor_factory
        self._probe = probe
        self._gateway_health_probe = gateway_health_probe
        self._popen_factory = popen_factory
        self._gateway_command = gateway_command
        self._supervisor_url = supervisor_url
        self._parent_environment = dict(
            parent_environment if parent_environment is not None else os.environ
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[Any] | None = None
        self._gateway_healthy = False
        self._gateway_ever_healthy = False
        self._startup_deadline_at: float | None = None
        self._health_failure_count = 0
        self._recycle_requested = threading.Event()
        self._selected: dict[str, ProviderEndpoint] = {}
        self._desired_key: tuple[tuple[str, str, int], ...] | None = None
        self._restart_count = 0
        self._next_restart_at = 0.0
        self._discovery_failures = 0
        self._last_decisions: dict[str, ProviderDecision] = {
            "tts": ProviderDecision("tts", "waiting"),
            "asr": ProviderDecision("asr", "waiting"),
        }
        self._last_error: str | None = None
        self._supervisor_http_status: int | None = None
        self._options_error = options_error

    @property
    def stop_event(self) -> threading.Event:
        return self._stop_event

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.run, name="provider-discovery", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def request_recycle(self) -> None:
        """Ask the manager thread to recycle the child after a proxy timeout."""

        self._recycle_requested.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def run(self) -> None:
        if self.options.errors:
            LOGGER.warning("Invalid app options were ignored: %s", ",".join(self.options.errors))

        next_discovery_at = 0.0
        while not self._stop_event.is_set():
            now = time.monotonic()
            if now >= next_discovery_at:
                try:
                    self.reconcile_once()
                    self._discovery_failures = 0
                    delay = DISCOVERY_INTERVAL_SECONDS
                except DiscoveryError as error:
                    self._discovery_failures += 1
                    delay = min(
                        RETRY_BASE_SECONDS * (2 ** (self._discovery_failures - 1)),
                        RETRY_MAX_SECONDS,
                    )
                    self._publish_discovery_failure(error.category, error.status_code)
                except Exception as error:
                    # Do not log exception text: it may contain request data or
                    # credentials from a future adapter implementation.
                    self._discovery_failures += 1
                    delay = min(
                        RETRY_BASE_SECONDS * (2 ** (self._discovery_failures - 1)),
                        RETRY_MAX_SECONDS,
                    )
                    LOGGER.error("Discovery cycle failed (%s)", type(error).__name__)
                    self._publish_discovery_failure("internal_discovery_error")
                next_discovery_at = time.monotonic() + delay

            self._monitor_child()
            self._refresh_gateway_status()
            self._stop_event.wait(1.0)

        self._stop_gateway()
        self._publish_shutdown()

    def reconcile_once(self) -> dict[str, ProviderDecision]:
        """Run one discovery/selection cycle; useful to exercise without threads."""

        if not self.options.discovery:
            self._selected = {}
            self._last_decisions = {
                "tts": ProviderDecision("tts", "disabled"),
                "asr": ProviderDecision("asr", "disabled"),
            }
            self._apply_selection()
            self._publish_status("disabled", None)
            return dict(self._last_decisions)

        if not self.token:
            self._selected = {}
            self._last_decisions = {
                "tts": ProviderDecision("tts", "waiting"),
                "asr": ProviderDecision("asr", "waiting"),
            }
            self._last_error = "supervisor_token_missing"
            self._apply_selection()
            self._publish_status("error", "supervisor_token_missing")
            return dict(self._last_decisions)

        supervisor = self._supervisor_factory(token=self.token, base_url=self._supervisor_url)
        explicit_slugs = {
            value
            for value in (self.options.tts_provider, self.options.stt_provider)
            if isinstance(value, str)
            and value not in {"auto", "none", "__invalid__"}
            and _SLUG_RE.fullmatch(value)
        }
        providers, warnings = discover_providers(
            supervisor, self._probe, explicit_slugs=explicit_slugs
        )
        for warning in warnings:
            LOGGER.info("Provider candidate unavailable (%s)", warning)

        self._last_decisions = {
            "tts": select_provider("tts", self.options.tts_provider, providers),
            "asr": select_provider("asr", self.options.stt_provider, providers),
        }
        self._selected = {
            role: decision.provider
            for role, decision in self._last_decisions.items()
            if decision.provider is not None
        }
        self._last_error = None
        self._supervisor_http_status = None
        self._apply_selection()
        self._monitor_child()
        self._refresh_gateway_status()
        return dict(self._last_decisions)

    def _selection_key(self) -> tuple[tuple[str, str, int], ...]:
        return tuple(
            (role, endpoint.host, endpoint.port)
            for role, endpoint in sorted(self._selected.items())
        )

    def _apply_selection(self) -> None:
        desired_key = self._selection_key()
        if desired_key == self._desired_key:
            self._maybe_start_gateway()
            return

        self._stop_gateway()
        self._desired_key = desired_key
        self._restart_count = 0
        self._next_restart_at = 0.0
        if desired_key:
            self._maybe_start_gateway()

    def _maybe_start_gateway(self) -> None:
        if not self._desired_key or self._stop_event.is_set():
            return
        if self._process is not None:
            return
        if time.monotonic() < self._next_restart_at:
            return

        environment = build_child_environment(
            self._parent_environment,
            self._selected,
            self.options.log_level,
            GATEWAY_PORT,
        )
        try:
            command = (
                [self._gateway_command]
                if self._gateway_command is not None
                else [sys.executable, "-c", GATEWAY_PRIVACY_SHIM]
            )
            self._process = self._popen_factory(
                command,
                env=environment,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except (OSError, ValueError) as error:
            LOGGER.error("Gateway child could not start (%s)", type(error).__name__)
            self._schedule_child_retry()
            self._last_error = "gateway_start_failed"
            return
        self._last_error = None
        now = time.monotonic()
        self._gateway_healthy = False
        self._gateway_ever_healthy = False
        self._startup_deadline_at = now + GATEWAY_STARTUP_TIMEOUT_SECONDS
        self._health_failure_count = 0
        LOGGER.info("Started gateway child for selected provider configuration")

    def _schedule_child_retry(self) -> None:
        self._restart_count += 1
        delay = min(RETRY_BASE_SECONDS * (2 ** (self._restart_count - 1)), RETRY_MAX_SECONDS)
        self._next_restart_at = time.monotonic() + delay

    def _monitor_child(self) -> None:
        if self._recycle_requested.is_set():
            self._recycle_requested.clear()
            if self._process is not None:
                self._recycle_child("gateway_proxy_timeout")
                return

        process = self._process
        if process is None:
            self._maybe_start_gateway()
            return
        try:
            return_code = process.poll()
        except Exception as error:
            LOGGER.warning("Gateway child status check failed (%s)", type(error).__name__)
            return
        if return_code is None:
            self._check_gateway_health(time.monotonic())
            return
        # Popen.poll() performs a waitpid on POSIX, so the exited process has
        # already been reaped.  wait() is retained for portable finalization.
        try:
            process.wait(timeout=0)
        except (subprocess.TimeoutExpired, OSError):
            pass
        self._process = None
        self._gateway_healthy = False
        self._gateway_ever_healthy = False
        self._startup_deadline_at = None
        self._health_failure_count = 0
        LOGGER.warning("Gateway child exited (status=%s); retrying with backoff", return_code)
        self._last_error = "gateway_child_exited"
        self._schedule_child_retry()
        self._maybe_start_gateway()

        return

    def _recycle_child(self, reason: str) -> None:
        """Stop and reap the child from the manager thread, then back off."""

        self._last_error = reason
        self._stop_gateway()
        if self._desired_key:
            self._schedule_child_retry()
        LOGGER.warning("Gateway child recycled (%s)", reason)

    def _check_gateway_health(self, now: float) -> None:
        try:
            healthy = bool(self._gateway_health_probe())
        except Exception as error:
            LOGGER.warning("Gateway health probe failed (%s)", type(error).__name__)
            healthy = False

        if healthy:
            self._gateway_healthy = True
            self._gateway_ever_healthy = True
            self._startup_deadline_at = None
            self._health_failure_count = 0
            return

        self._gateway_healthy = False
        if not self._gateway_ever_healthy:
            if self._startup_deadline_at is not None and now >= self._startup_deadline_at:
                self._recycle_child("gateway_startup_timeout")
            return

        self._health_failure_count += 1
        if self._health_failure_count >= 2:
            self._recycle_child("gateway_health_failed")

    def _stop_gateway(self) -> None:
        process = self._process
        self._process = None
        self._gateway_healthy = False
        self._gateway_ever_healthy = False
        self._startup_deadline_at = None
        self._health_failure_count = 0
        if process is None:
            return

        try:
            running = process.poll() is None
        except Exception:
            running = True
        if running:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except (AttributeError, OSError) as error:
                LOGGER.warning("Gateway child signal failed (%s)", type(error).__name__)
            try:
                process.wait(timeout=CHILD_STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except (AttributeError, OSError) as error:
                    LOGGER.warning("Gateway child force-stop failed (%s)", type(error).__name__)
                try:
                    process.wait()
                except OSError as error:
                    LOGGER.warning("Gateway child reap failed (%s)", type(error).__name__)
            except OSError as error:
                LOGGER.warning("Gateway child reap failed (%s)", type(error).__name__)
        else:
            try:
                process.wait(timeout=0)
            except (subprocess.TimeoutExpired, OSError):
                pass
        LOGGER.info("Gateway child stopped")

    def _publish_status(self, overall: str, error_code: str | None) -> None:
        running = self._child_running()
        healthy = running and self._gateway_healthy
        ready = bool(self._selected) and healthy
        if overall == "ready" and not ready:
            overall = "starting" if self._selected else "waiting"
        value = {
            "status": overall,
            "ready": ready,
            "updated_at": utc_timestamp(),
            "discovery_enabled": self.options.discovery,
            "discovery_scope": DISCOVERY_SCOPE,
            "last_error": error_code or self._options_error or self._last_error,
            "supervisor_http_status": self._supervisor_http_status,
            "tts": _decision_for_status(self._last_decisions["tts"]),
            "stt": _decision_for_status(self._last_decisions["asr"]),
            "gateway": {
                "running": running,
                "healthy": healthy,
                "upstream": f"http://{GATEWAY_HOST}:{GATEWAY_PORT}",
            },
        }
        self.status.replace(value)

    def _publish_discovery_failure(self, category: str, http_status: int | None = None) -> None:
        self._last_error = category
        self._supervisor_http_status = http_status
        # Inventory availability says nothing about a remembered provider's
        # liveness. Recheck those endpoints even while Supervisor retries back
        # off, rather than trusting the child's process-only /healthz forever.
        capabilities_by_address: dict[tuple[str, int], frozenset[str]] = {}
        retained: dict[str, ProviderEndpoint] = {}
        for role, endpoint in self._selected.items():
            address = (endpoint.host, endpoint.port)
            if address not in capabilities_by_address:
                try:
                    capabilities_by_address[address] = self._probe(*address)
                except Exception:
                    capabilities_by_address[address] = frozenset()
            if role in capabilities_by_address[address]:
                retained[role] = endpoint
            else:
                previous = self._last_decisions[role]
                self._last_decisions[role] = ProviderDecision(
                    role, "unavailable", candidates=previous.candidates,
                )
        if retained != self._selected:
            self._selected = retained
            self._apply_selection()
        self._last_error = category
        if self._selected:
            # A Supervisor outage alone must not interrupt providers that
            # remain independently verified and healthy.
            overall = "degraded"
        else:
            overall = "error" if category == "supervisor_token_missing" else "waiting"
        self._publish_status(overall, category)

    def _publish_shutdown(self) -> None:
        self.status.replace(
            {
                "status": "stopped",
                "ready": False,
                "updated_at": utc_timestamp(),
                "discovery_enabled": self.options.discovery,
                "discovery_scope": DISCOVERY_SCOPE,
                "last_error": None,
                "tts": _decision_for_status(self._last_decisions["tts"]),
                "stt": _decision_for_status(self._last_decisions["asr"]),
                "gateway": {
                    "running": False,
                    "healthy": False,
                    "upstream": f"http://{GATEWAY_HOST}:{GATEWAY_PORT}",
                },
            }
        )

    def _child_running(self) -> bool:
        if self._process is None:
            return False
        try:
            return self._process.poll() is None
        except Exception:
            return False

    def _refresh_gateway_status(self) -> None:
        decisions = self._last_decisions
        if self._last_error == "supervisor_token_missing":
            overall = "error"
        elif any(decision.status == "ambiguous" for decision in decisions.values()):
            overall = "ambiguous"
        elif self._last_error:
            overall = "degraded" if self._selected else "waiting"
        elif self._selected:
            overall = (
                "ready"
                if self._child_running() and self._gateway_healthy
                else "starting"
            )
        elif all(decision.status == "disabled" for decision in decisions.values()):
            overall = "disabled"
        elif any(decision.status == "invalid_selection" for decision in decisions.values()):
            overall = "error"
        else:
            overall = "waiting"
        self._publish_status(overall, self._last_error)


class RequestTooLarge(Exception):
    pass


class UnsupportedRequest(Exception):
    pass


class BoundedThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Threaded HTTP server with a hard cap on concurrent client handlers."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        status: StatusStore,
    ):
        self.status_store = status
        self._request_slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
        super().__init__(server_address, handler)

    def get_request(self) -> tuple[socket.socket, Any]:
        request, address = super().get_request()
        request.settimeout(HTTP_SOCKET_TIMEOUT_SECONDS)
        return request, address

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        if not self._request_slots.acquire(blocking=False):
            try:
                body = b'{"error":"server_busy"}'
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Connection: close\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
                    + body
                )
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._request_slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()

    def handle_error(self, request: socket.socket, client_address: Any) -> None:
        # The stdlib default prints a traceback.  Keep HTTP parsing details and
        # request values out of app logs.
        LOGGER.warning("HTTP request handler failed")


def create_handler(
    status_store: StatusStore, controller: GatewaySupervisor | None = None
) -> type[BaseHTTPRequestHandler]:
    """Build a handler class bound to runtime status and recycle signaling."""

    class GatewayProxyHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "WyomingOpenAIGateway/1"
        sys_version = ""

        def log_message(self, _format: str, *args: Any) -> None:
            # Suppress default access logging; request paths may contain user
            # data and upstream responses are deliberately not logged.
            return

        def send_error(
            self, code: int, message: str | None = None, explain: str | None = None
        ) -> None:
            # BaseHTTPRequestHandler's HTML error page echoes request-line
            # details.  Keep malformed-request responses generic instead.
            self.close_connection = True
            self._json_response(code, {"error": "bad_http_request"})

        def handle_expect_100(self) -> bool:
            try:
                length = self._content_length()
            except UnsupportedRequest:
                self.close_connection = True
                self._json_response(400, {"error": "invalid_request"})
                return False
            if length > MAX_REQUEST_BYTES:
                self.close_connection = True
                self._json_response(413, {"error": "request_too_large"})
                return False
            if self.headers.get("Transfer-Encoding"):
                self.close_connection = True
                self._json_response(501, {"error": "transfer_encoding_not_supported"})
                return False
            self.send_response_only(100)
            self.end_headers()
            return True

        def do_GET(self) -> None:
            self._dispatch()

        def do_HEAD(self) -> None:
            self._dispatch(head_only=True)

        def do_POST(self) -> None:
            self._dispatch()

        def do_PUT(self) -> None:
            self._dispatch()

        def do_PATCH(self) -> None:
            self._dispatch()

        def do_DELETE(self) -> None:
            self._dispatch()

        def do_OPTIONS(self) -> None:
            self._dispatch()

        def _dispatch(self, head_only: bool = False) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.scheme or parsed.netloc or not self.path.startswith("/"):
                self.close_connection = True
                self._json_response(400, {"error": "invalid_request"}, head_only)
                return
            try:
                body = self._read_request_body()
            except RequestTooLarge:
                self.close_connection = True
                self._json_response(413, {"error": "request_too_large"}, head_only)
                return
            except UnsupportedRequest:
                self.close_connection = True
                self._json_response(400, {"error": "invalid_request"}, head_only)
                return
            except (OSError, TimeoutError):
                self.close_connection = True
                self._json_response(408, {"error": "request_timeout"}, head_only)
                return

            if parsed.path in {"/healthz", "/readyz", "/status"}:
                self._status_route(parsed.path, head_only)
                return

            state = status_store.snapshot()
            if not state.get("gateway", {}).get("healthy", False):
                self._json_response(
                    503,
                    {"error": "gateway_unavailable", "status": state.get("status")},
                    head_only,
                )
                return
            self._proxy_request(body, head_only)

        def _status_route(self, path: str, head_only: bool) -> None:
            value = status_store.snapshot()
            if path == "/healthz":
                # Process-level health is independent of provider availability.
                response = {
                    "status": "ok",
                    "ready": bool(value.get("ready")),
                    "runtime": value.get("status"),
                }
                self._json_response(200, response, head_only)
            elif path == "/readyz":
                ready = bool(value.get("ready"))
                self._json_response(200 if ready else 503, value, head_only)
            else:
                self._json_response(200, value, head_only)

        def _content_length(self) -> int:
            raw_values = self.headers.get_all("Content-Length", failobj=[])
            if not raw_values:
                return 0
            if (
                len(raw_values) != 1
                or len(raw_values[0]) > 16
                or not raw_values[0].isascii()
                or not raw_values[0].isdecimal()
            ):
                raise UnsupportedRequest
            length = int(raw_values[0])
            if length < 0:
                raise UnsupportedRequest
            return length

        def _read_request_body(self) -> bytes:
            if self.headers.get("Transfer-Encoding"):
                raise UnsupportedRequest
            length = self._content_length()
            if length > MAX_REQUEST_BYTES:
                raise RequestTooLarge
            if not length:
                return b""
            body = self.rfile.read(length)
            if len(body) != length:
                raise UnsupportedRequest
            return body

        def _proxy_request(self, body: bytes, head_only: bool) -> None:
            connection = http.client.HTTPConnection(
                GATEWAY_HOST, GATEWAY_PORT, timeout=UPSTREAM_HTTP_TIMEOUT_SECONDS
            )
            response_started = False
            try:
                request_headers = self._forward_request_headers(len(body))
                connection.request(
                    self.command,
                    self.path,
                    body=body if body else None,
                    headers=request_headers,
                )
                response = connection.getresponse()
                self.send_response(response.status)
                response_headers = self._forward_response_headers(response)
                no_body = head_only or self.command == "HEAD" or response.status in {204, 304}
                content_length = response.getheader("Content-Length")
                if content_length is None and not no_body:
                    response_headers.append(("Transfer-Encoding", "chunked"))
                for key, value in response_headers:
                    self.send_header(key, value)
                self.end_headers()
                response_started = True
                if no_body:
                    return

                total = 0
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        LOGGER.warning("Gateway response exceeded configured size limit")
                        self.close_connection = True
                        return
                    if content_length is None:
                        self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                        self.wfile.write(chunk)
                        self.wfile.write(b"\r\n")
                    else:
                        self.wfile.write(chunk)
                if content_length is None:
                    self.wfile.write(b"0\r\n\r\n")
                else:
                    try:
                        expected_length = int(content_length)
                    except ValueError:
                        expected_length = -1
                    if total != expected_length:
                        self.close_connection = True
                self.wfile.flush()
            except (OSError, TimeoutError, http.client.HTTPException) as error:
                LOGGER.warning("Gateway proxy request failed (%s)", type(error).__name__)
                if isinstance(error, TimeoutError) and controller is not None:
                    # The HTTP client cannot cancel a Wyoming operation already
                    # awaiting upstream work. Request a manager-thread recycle;
                    # never touch Popen from this request thread.
                    controller.request_recycle()
                if not self.wfile.closed:
                    try:
                        if not response_started:
                            self._json_response(502, {"error": "gateway_upstream_error"}, head_only)
                        else:
                            self.close_connection = True
                    except OSError:
                        self.close_connection = True
            finally:
                connection.close()

        def _forward_request_headers(self, body_length: int) -> dict[str, str]:
            connection_header = self.headers.get("Connection", "")
            nominated = {
                part.strip().lower()
                for part in connection_header.split(",")
                if part.strip()
            }
            excluded = _HOP_BY_HOP_HEADERS | nominated | {"host", "content-length", "expect"}
            forwarded: dict[str, str] = {}
            for key, value in self.headers.items():
                if key.lower() not in excluded:
                    forwarded[key] = value
            if body_length:
                forwarded["Content-Length"] = str(body_length)
            return forwarded

        def _forward_response_headers(
            self, response: http.client.HTTPResponse
        ) -> list[tuple[str, str]]:
            connection_header = response.getheader("Connection", "")
            nominated = {
                part.strip().lower()
                for part in connection_header.split(",")
                if part.strip()
            }
            excluded = _HOP_BY_HOP_HEADERS | nominated
            result: list[tuple[str, str]] = []
            for key, value in response.getheaders():
                if key.lower() not in excluded:
                    result.append((key, value))
            return result

        def _json_response(
            self,
            status: int,
            value: Mapping[str, Any],
            head_only: bool = False,
        ) -> None:
            body = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head_only:
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

    return GatewayProxyHandler


def _safe_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        LOGGER.warning("Invalid %s value; using default", name)
        return default
    if not minimum <= value <= maximum:
        LOGGER.warning("Out-of-range %s value; using default", name)
        return default
    return value


def main() -> int:
    options, options_error = load_options()
    logging.basicConfig(
        level=getattr(logging, options.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if options_error:
        LOGGER.warning("App options could not be fully loaded (%s)", options_error)

    status = StatusStore()
    manager = GatewaySupervisor(
        options=options,
        token=os.environ.get("SUPERVISOR_TOKEN"),
        status=status,
        options_error=options_error,
    )
    frontend_host = os.environ.get("FRONTEND_HOST", FRONTEND_HOST)
    frontend_port = _safe_int_env("FRONTEND_PORT", FRONTEND_PORT, 1, 65535)
    try:
        server = BoundedThreadingHTTPServer(
            (frontend_host, frontend_port), create_handler(status, manager), status
        )
    except OSError as error:
        LOGGER.error("HTTP frontend could not bind (%s)", type(error).__name__)
        return 1

    server_thread = threading.Thread(target=server.serve_forever, name="gateway-http", daemon=True)
    shutdown_event = threading.Event()

    def request_shutdown(_signum: int, _frame: Any) -> None:
        shutdown_event.set()

    previous_handlers: dict[int, Any] = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.signal(signum, request_shutdown)

    LOGGER.info("HTTP frontend listening on %s:%s", frontend_host, frontend_port)
    manager.start()
    server_thread.start()
    try:
        while not shutdown_event.wait(0.5):
            if not manager._thread or not manager._thread.is_alive():
                LOGGER.error("Discovery supervisor stopped unexpectedly")
                break
    finally:
        manager.stop()
        server.shutdown()
        server_thread.join(timeout=5.0)
        manager.join(timeout=MANAGER_SHUTDOWN_TIMEOUT_SECONDS)
        server.server_close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
