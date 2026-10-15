# Wyoming OpenAI Gateway for Home Assistant OS
## Community app implementation plan

**Proposed repository:** `https://github.com/dmyoung9/ha-wyoming-openai-gateway`  
**Owner:** `dmyoung9`  
**Status:** Design and implementation plan; not yet implemented or published  
**Goal:** A one-click-installable Home Assistant OS community app that exposes existing Wyoming Piper TTS and Whisper STT services as OpenAI-compatible HTTP endpoints, with automatic discovery and no manual host/port entry for common Home Assistant installations.

## 1. Scope and user experience

1. User already has the official Piper and/or Whisper apps installed and running on Home Assistant OS.
2. User clicks the **Add to My Home Assistant** button in this repository's README to add the community repository.
3. User installs **Wyoming OpenAI Gateway**, starts it, and enables **Start on boot** and **Watchdog**.
4. On startup, the app discovers running Wyoming providers using the Supervisor API, tests their Wyoming protocol capabilities, and selects an unambiguous Piper TTS and Whisper STT provider automatically.
5. The app listens on port 8555 and shows a working API URL. Users configure OpenChamber or other clients with `http://HOME_ASSISTANT_IP:8555/v1/` (or an HTTPS reverse-proxy URL when required).
6. If no compatible provider is running, the app remains running with an actionable status and retries discovery, rather than failing silently. If multiple candidates exist, it reports the ambiguity and offers explicit selection.

**Non-goals for v1:** installing or starting Piper/Whisper automatically, modifying their settings, accessing the Docker socket, exposing the service publicly, building a full HA integration, or guaranteeing MP3 output from an upstream WAV-only gateway.

## 2. Verified upstream and Home Assistant constraints

- Upstream gateway: https://github.com/Archmonger/Wyoming-OpenAI-Gateway (MIT). Provides `/v1/audio/speech`, `/v1/audio/transcriptions`, `/v1/audio/translations`, `/v1/voices`, `/healthz`, and `/readyz`. Environment settings include `TTS_HOST`, `TTS_PORT`, `STT_HOST`, `STT_PORT`, `HOST`, `PORT`, `PREFIX`, `DEBUG`, `LOG_LEVEL`. The current documented speech response format is WAV only.
- Home Assistant community app repositories require root `repository.yaml`; each app requires its own `config.yaml`.
- Home Assistant Supervisor API supports `GET /addons`, `GET /addons/<slug>/info`, and reports installed/running state. App DNS names are derived from repository ID and slug, replacing underscores with hyphens.
- `hassio_api: true` and `SUPERVISOR_TOKEN` permit Supervisor API calls. Use `hassio_role: default`, which supports info calls. Avoid manager/admin privileges.
- The Supervisor Services API is **not** a generic Wyoming service registry (documented services include MQTT and MySQL). Automatic discovery must be implemented; merely declaring a service does not discover Piper or Whisper.
- Home Assistant OS runs apps in isolated containers. `localhost` in our container does not refer to Piper or Whisper.

References: https://developers.home-assistant.io/docs/apps/repository/ ; https://developers.home-assistant.io/docs/apps/configuration/ ; https://developers.home-assistant.io/docs/apps/communication/ ; https://developers.home-assistant.io/docs/api/supervisor/endpoints/ ; https://github.com/Archmonger/Wyoming-OpenAI-Gateway

## 3. GitHub repository creation under dmyoung9

1. Sign in to GitHub as `dmyoung9`. Create a **public** repository named `ha-wyoming-openai-gateway`, with issues enabled. Do not fork upstream merely to create an app wrapper: preserve a clear separation between upstream gateway and our Home Assistant packaging.
2. With GitHub CLI (after `gh auth login`), initialize a local repo:

   ```bash
   mkdir ha-wyoming-openai-gateway && cd ha-wyoming-openai-gateway
   git init -b main
   gh repo create dmyoung9/ha-wyoming-openai-gateway --public --source=. --remote=origin
   ```

3. Add files from the layout below, commit, then `git push -u origin main`.
4. In **Settings > Actions > General**, allow required GitHub Actions. Enable Dependabot security alerts and secret scanning where available; add branch protection after the first release.
5. Use GitHub Releases and semantic version tags for release notes. If publishing images to GHCR, grant workflow `packages: write` only to the image-build job and set the package public for anonymous HAOS pulls.
6. Do not commit Supervisor tokens, HA credentials, or real LAN addresses. Do not claim that the proposed repository exists until it has actually been created.

## 4. Repository structure

```text
ha-wyoming-openai-gateway/
├── repository.yaml
├── README.md                       # public install/use guide (separate deliverable)
├── LICENSE                         # license for our original wrapper code
├── NOTICE.md                       # upstream credit and MIT notice
├── .gitignore
├── .github/
│   ├── workflows/
│   │   ├── lint.yml
│   │   ├── test.yml
│   │   └── release.yml
│   ├── dependabot.yml
│   └── ISSUE_TEMPLATE/bug_report.yml
├── wyoming_openai_gateway/
│   ├── config.yaml                 # HA app metadata, ports, options, API permissions
│   ├── build.yaml                  # optional architecture-specific base image metadata
│   ├── Dockerfile                  # wrapper image, pin upstream version/digest
│   ├── run.sh                      # launch discovery supervisor and gateway
│   ├── rootfs/usr/local/bin/
│   │   └── discover.py             # Supervisor inventory + Wyoming capability probing
│   ├── translations/en.yaml       # user-facing options and port descriptions
│   ├── DOCS.md                     # HA app documentation tab
│   ├── CHANGELOG.md
│   ├── icon.png
│   └── logo.png
└── tests/
    ├── test_discovery.py
    ├── test_selection.py
    └── fixtures/supervisor_addons.json
```

Use the exact Home Assistant packaging conventions from the current developer docs; validate optional files and translation schemas against the version of Supervisor used for release.

## 5. Required Home Assistant boilerplate

**`repository.yaml`**

```yaml
name: Wyoming OpenAI Gateway Apps
url: https://github.com/dmyoung9/ha-wyoming-openai-gateway
maintainer: dmyoung9
```

**Initial `wyoming_openai_gateway/config.yaml` design (validate schema before shipping):**

```yaml
name: Wyoming OpenAI Gateway
slug: wyoming_openai_gateway
version: "0.1.0"
description: OpenAI-compatible TTS/STT API backed by existing Wyoming apps
url: https://github.com/dmyoung9/ha-wyoming-openai-gateway
arch:
  - amd64
  - aarch64
startup: services
boot: auto
init: false
hassio_api: true
hassio_role: default
ports:
  8555/tcp: 8555
ports_description:
  8555/tcp: OpenAI-compatible speech API
options:
  discovery: true
  tts_provider: auto
  stt_provider: auto
  log_level: INFO
schema:
  discovery: bool
  tts_provider: str
  stt_provider: str
  log_level: list(DEBUG|INFO|WARNING|ERROR)
```

**Important:** This is a starting specification, not a validated working app. Confirm whether `init: false` is appropriate for the chosen container base, and whether additional `startup`, watchdog, image/build, translation, or security settings are needed. A custom `Dockerfile` is needed because a stock gateway image cannot by itself perform Supervisor discovery before startup. Avoid `host_network`, `docker_api`, `full_access`, `privileged`, and mounting Home Assistant configuration.

**Wrapper implementation:**

- Choose either a pinned upstream gateway source release installed in a multi-stage image, or a wrapper image based on a pinned upstream image **only if** that image has a suitable runtime and the extra Python discovery dependencies. Test on both `amd64` and `aarch64`.
- Include a small process supervisor (or an appropriate init solution) that handles signals, restarts the gateway on provider changes, and preserves logs. The upstream gateway currently requires at least one configured TTS or STT endpoint at startup, so the wrapper must handle the no-provider state itself.
- Read `/data/options.json` (or a supported Bashio helper) for user options. Never log `SUPERVISOR_TOKEN`.
- Pass resolved provider endpoints to the gateway through environment variables. Bind `HOST=0.0.0.0`, `PORT=8555`, `PREFIX=/v1`.
- Maintain `/healthz` and `/readyz` semantics; `/readyz` should report unready when no provider is usable. Keep discovery status in logs and optionally a local read-only status endpoint.

## 6. Automatic discovery: core feature

**Do not hard-code `core_piper:10200` and `core_whisper:10300` as the only path.** Those are useful known candidates, but other Wyoming apps and versions may use different slugs and ports.

### Algorithm

1. Request `GET http://supervisor/addons` with `Authorization: Bearer $SUPERVISOR_TOKEN`.
2. Keep only **installed and started** apps. Treat installed-but-stopped apps as unavailable; never start another app without user consent.
3. Prioritize known official app slugs (`core_piper`, `core_whisper`) and known community Wyoming implementations. For each candidate, fetch `/addons/<slug>/info` and inspect declared service/port metadata where available. Do not assume that a published host port is the same as the internal listening port.
4. Construct candidate internal DNS names from the **full** Supervisor slug, replacing `_` with `-`, e.g. `core-piper`. Resolve the correct internal TCP port from known-provider adapters and verified metadata; do not indiscriminately scan arbitrary hosts/ports.
5. Open a bounded TCP connection and send Wyoming `Describe` to verify the service and inspect capabilities. Select providers based on actual TTS/STT capabilities rather than names alone.
6. For TTS: select a single healthy speech-synthesis provider. For STT: select a single healthy speech-recognition provider. If exactly one candidate exists for a role, select it automatically. If multiple candidates exist, prefer an explicitly saved selection; otherwise expose the candidates and require selection rather than guessing.
7. Support Piper-only, Whisper-only, and both. Retry unavailable candidates with bounded backoff. Periodically re-inventory Supervisor (e.g. every 60 seconds) and re-probe endpoints; restart/reconfigure the gateway only when the selected endpoint actually changes.
8. Allow `tts_provider` and `stt_provider` options to specify a full Supervisor slug, or `none` to disable a role. Reserve `auto` for automatic selection. Consider explicit remote `host:port` support as a later advanced feature, with strict validation.
9. Handle upgrades, renamed apps, app restarts, DNS delays, provider outages, malformed Supervisor responses, token failure, and app shutdown gracefully.

### Critical implementation caveat

The Supervisor API provides **inventory**, not a guaranteed generic registry of Wyoming endpoint ports. Auto-discovery of official Piper/Whisper can be delivered using tested provider adapters and Wyoming `Describe`; arbitrary third-party Wyoming services cannot be guaranteed zero-config without a published endpoint convention or discovery metadata. Document this honestly. The app should never claim to discover *every* Wyoming server on a network.

### Selection rules

| Piper | Whisper | Result |
|---|---|---|
| Running | Running | Enable TTS and STT automatically |
| Running | Absent/stopped | Enable TTS only; log how to add STT |
| Absent/stopped | Running | Enable STT only; log how to add TTS |
| Absent/stopped | Absent/stopped | Keep app alive, show waiting status, retry |
| Multiple valid TTS or STT | Any | Require explicit selection unless saved preference remains valid |

## 7. Security and networking

- Default to LAN-only documentation. Port 8555 is unauthenticated unless authentication is explicitly implemented; **do not expose it directly to the internet**.
- If OpenChamber is installed as a native Android app, plain HTTP may be blocked by its network security policy; recommend an HTTPS reverse proxy for remote/mobile clients when necessary.
- If adding API-key authentication later, implement it in the wrapper/proxy or a reverse proxy; do not imply that upstream accepts a key merely because clients send one.
- Limit Supervisor access to `hassio_api: true` with `hassio_role: default`; verify access on supported Supervisor releases.
- Set connection/read timeouts and bound request sizes for STT uploads; avoid logging raw speech or audio content.
- Pin upstream image digest or package version; keep SBOM/dependency checks and update process.
- Prefer no Docker socket and no host networking; preserve HAOS app protection mode.

## 8. Testing and acceptance criteria

**Unit tests:** Supervisor payload parsing, full-slug DNS normalization, official provider adapters, capability checks, ambiguous provider selection, startup with zero providers, retries, timeout behavior, and token handling.

**Integration matrix:** HAOS `amd64` and `aarch64`; official Piper only; Whisper only; both; neither; provider stopped/restarted; two Piper implementations; changed app slug; no host port published; bad DNS; malformed Supervisor response.

**API smoke tests:**

```bash
curl -fsS http://HOME_ASSISTANT_IP:8555/healthz
curl -fsS http://HOME_ASSISTANT_IP:8555/v1/voices
curl -fsS http://HOME_ASSISTANT_IP:8555/readyz
curl -fsS -X POST http://HOME_ASSISTANT_IP:8555/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"wyoming","voice":"VOICE_FROM_VOICES_ENDPOINT","input":"Hello","response_format":"wav"}' \
  -o test.wav
file test.wav
```

Test STT with a known audio sample and multipart upload. Assert correct HTTP status and media types, not merely process startup.

**Release acceptance:** a clean HAOS installation with running official Piper/Whisper needs **zero manual host/port configuration**; app starts and discovers services; TTS and STT work; restarting providers recovers automatically; a README-only user can install and troubleshoot; no elevated privileges; source attribution included.

## 9. CI/CD and publication

1. Add YAML lint, shellcheck, Python ruff/pytest, secret scanning, and validation of Home Assistant app metadata using maintained HA app validation tooling.
2. Build and test both supported architectures. If using `image:` in `config.yaml`, publish public multi-architecture GHCR images and ensure the image tag exactly matches the app version. Alternatively, ship a tested source-build app and document installation time tradeoffs.
3. Pin dependencies and upstream revision. Record provenance and dependency/license notices.
4. Publish `0.1.0` as a pre-release; test installing from the public repository URL on a separate HAOS instance, including the My Home Assistant button.
5. Publish `1.0.0` only after zero-config Piper + Whisper discovery, clean startup/shutdown, updates, and recovery are proven.
6. Create issue templates, release changelog, and contribution/security policy. Keep the gateway's upstream fixes upstream when feasible.

## 10. Attribution and licensing

Credit **Archmonger** and contributors of [Wyoming-OpenAI-Gateway](https://github.com/Archmonger/Wyoming-OpenAI-Gateway) prominently in `README.md`, `NOTICE.md`, and app documentation. The upstream gateway is MIT-licensed. Include a copy of its MIT license and copyright notice when redistributing upstream code or binaries; preserve notices in derived code. License original wrapper code separately (MIT is a reasonable compatible choice). State clearly that this community package is **unofficial** and not affiliated with the upstream project or Home Assistant.

## 11. Implementation milestones

- **M1: Repository + metadata:** GitHub repo, README, repository.yaml, config.yaml, licensing, CI scaffolding.
- **M2: Wrapper startup:** Pinned upstream gateway starts as HAOS app using explicit test endpoints.
- **M3: Official auto-discovery:** Supervisor enumeration, official Piper/Whisper adapters, Wyoming `Describe`, zero-config selection.
- **M4: Resilience:** Retry, restart handling, multiple-provider choices, useful status/logging.
- **M5: Release:** Cross-architecture tests, security review, public image/package, My button verified, screenshots and docs.

## 12. Open questions to resolve before coding

1. Verify current official Piper and Whisper app slugs, actual internal listening ports, and whether their DNS aliases work consistently across supported HAOS/Supervisor releases.
2. Decide how the wrapper remains alive with zero providers when the upstream gateway refuses to start without one.
3. Determine whether a minimal local status UI is needed for multiple-provider selection, or whether Supervisor app options are sufficient.
4. Verify cross-architecture availability of the upstream image and `miniaudio` wheels.
5. Confirm whether the upstream gateway has any runtime API for reconfiguration; otherwise use supervised restarts when discovered providers change.
6. Test the exact client requirements for WAV, authentication, and HTTPS, particularly OpenChamber Android.
