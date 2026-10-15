# Wyoming OpenAI Gateway

**Version 0.1.2 — prerelease.** This community app exposes existing Wyoming
speech providers through OpenAI-compatible APIs. Both architecture images pass
CI build and no-provider smoke tests; end-to-end testing on real HAOS is pending.

## What it does

The app wraps [Wyoming-OpenAI-Gateway](https://github.com/Archmonger/Wyoming-OpenAI-Gateway)
and forwards OpenAI-style speech requests to already-running
Wyoming providers. It does not install, configure, or start Piper or Whisper.

The default endpoint is port `8555`, with the API under `/v1/`.
The pinned upstream commit `a4e000b650dbc55cefe4e5c02e6f97a4ec4b0eae` provides
WAV speech output. MP3 output is not supported.

If OpenChamber inside the magnusoverli/opencode Home Assistant add-on rejects
the internal URL because remote custom servers are disabled, see the
[optional loopback hook](https://github.com/dmyoung9/ha-wyoming-openai-gateway/tree/main/examples/openchamber).
Install that workaround in the **OpenCode/OpenChamber container**, not here.
The upstream environment-option request is
[magnusoverli/opencode#185](https://github.com/magnusoverli/opencode/issues/185).

## Options

Upgrading from 0.1.0 fixes an inventory permission error: discovery now uses
`/supervisor/info` instead of the restricted `/addons` list. Supervisor access
remains at the `default` role. Existing options and host port mappings are kept.

| Option | Default | Meaning |
| --- | --- | --- |
| `discovery` | `true` | Enable Supervisor inventory and Wyoming provider discovery. |
| `tts_provider` | `auto` | Automatic selection, `none`, or a discovered Supervisor slug. |
| `stt_provider` | `auto` | Automatic selection, `none`, or a discovered Supervisor slug. |
| `log_level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |

The Supervisor API exposes app inventory, not a universal Wyoming service
registry. Current official [Piper app metadata](https://github.com/home-assistant/addons/blob/master/piper/config.yaml)
lists the `piper` slug and 10200/tcp; current official [Whisper app metadata](https://github.com/home-assistant/addons/blob/master/whisper/config.yaml)
lists the `whisper` slug and 10300/tcp. Both declare the Wyoming service and
map the service port to no host port by default. These are provider-specific
facts, not a promise that arbitrary third-party apps can be discovered. DNS
reachability, protocol capabilities, lifecycle recovery, and multi-provider
selection still require HAOS integration testing.

Full slugs ending in `_piper` or `_whisper` are accepted only when metadata
declares the conventional internal port. Another explicitly selected slug needs
a unique declared TCP port labeled Wyoming in Supervisor metadata. Arbitrary
ports are not scanned. `/status` lists candidates and selection problems;
**Open Web UI** opens this read-only status. Save full slugs in the app options
and restart the gateway to apply changes.

`/healthz` reports wrapper liveness even without providers. `/readyz` requires
at least one selected provider and a serving gateway child. Watchdog uses
liveness, so an absent speech provider does not cause a restart loop.

## Network and security

At the default **INFO** log level, the **Log** tab shows verified Wyoming server
slugs/hosts/ports, selected TTS/STT endpoints, and installed voice IDs/languages.
Startup, metadata changes, outages, and recovery are logged without repeating
unchanged discovery polls. Catalog output is bounded; use `/v1/voices` for the
full gateway voice list. This logs discovery/selection, not every speech request.

Port 8555 is an unauthenticated HTTP API. Publish it only to a trusted LAN.
Never forward it directly to the public internet. For remote use, prefer a VPN
or a carefully secured HTTPS reverse proxy with authentication and rate limits.
Home Assistant app port mapping does not itself add API authentication.

Supervisor access is enabled with the least-privileged `default` role. The app
must not need the Docker socket, host networking, privileged mode, or access to
the Home Assistant configuration directory. Treat the Supervisor bearer token
as a secret: do not print it, include it in issue reports, or expose it in
diagnostic endpoints. The wrapper suppresses upstream content-bearing loggers,
including transcription text, even with wrapper DEBUG logging enabled.
Requests are limited to 25 MiB; chunked request uploads are not supported.

Speech remains on the local Wyoming network when configured that way. This
wrapper does not imply that every upstream implementation or client is local,
private, authenticated, or suitable for sensitive recordings.

## Local checks and HAOS validation

Packaging follows the current Home Assistant [app configuration](https://developers.home-assistant.io/docs/apps/configuration/)
and [repository metadata](https://developers.home-assistant.io/docs/apps/repository/)
documentation. `scripts/validate_metadata.py` checks project invariants;
`scripts/validate_supervisor_schema.py` additionally checks the actual schemas
from the Supervisor revision pinned in CI. Neither proves a real installation.

CI checks metadata syntax/schema, shell and Python quality, tests, and source
image builds for `linux/amd64` and `linux/arm64`. These checks do **not** emulate
the Supervisor API or establish that the app works on HAOS. Before release,
test Piper-only, Whisper-only, both, neither, stopped/restarted providers,
ambiguous providers, API media types, and clean shutdown on actual HAOS systems
for both architectures. No release or container publication is automated.

Report issues with app version, HAOS and Supervisor versions, provider app
versions, sanitized logs, and a reproducible request. Never attach credentials,
Supervisor tokens, public endpoint credentials, or private recordings.
