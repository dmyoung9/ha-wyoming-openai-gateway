# Wyoming OpenAI Gateway for Home Assistant

**An unofficial Home Assistant community app that exposes your existing Wyoming Piper and Whisper services through OpenAI-compatible speech APIs.**

> **[0.1.2 prerelease](https://github.com/dmyoung9/ha-wyoming-openai-gateway/releases/tag/v0.1.2)** — available for installation and testing. See [validation and known limitations](#validation-and-known-limitations) for what has been verified.

[![Add repository to My Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fdmyoung9%2Fha-wyoming-openai-gateway)

**Repository:** `https://github.com/dmyoung9/ha-wyoming-openai-gateway`

## What it does

The app runs [Wyoming-OpenAI-Gateway](https://github.com/Archmonger/Wyoming-OpenAI-Gateway) on your Home Assistant OS appliance. It reuses **Piper** for text-to-speech and **Whisper** for speech-to-text instead of downloading or running duplicate speech models.

```text
OpenChamber / other OpenAI-compatible client
                 |
                 | HTTP :8555 (or HTTPS reverse proxy)
                 v
     Wyoming OpenAI Gateway app
                 |
          Wyoming TCP
            /        \
           v          v
      Piper app    Whisper app
         TTS           STT
```

### Features

- **Automatic discovery:** Detects already installed, running official Home Assistant Piper and Whisper apps through Supervisor and verifies Wyoming capabilities. No manual host or port entry for the standard setup.
- **Use either or both:** Piper-only, Whisper-only, and combined setups supported.
- **No duplicate models:** Existing Piper voices and Whisper model installation remain where they are.
- **OpenAI-style endpoints:** `/v1/audio/speech`, `/v1/audio/transcriptions`, `/v1/audio/translations`, and `/v1/voices` (subject to upstream capability and provider availability).
- **Recovery:** Retries discovery after provider restarts or app startup ordering changes.
- **Manual selection when necessary:** Multiple valid providers require a choice rather than silently picking the wrong one.

Discovery uses **supported, running Home Assistant apps**, not a network scan. It queries Supervisor inventory and verifies each candidate's Wyoming capabilities.

## Requirements

- Home Assistant OS with Supervisor and access to the Home Assistant app store.
- An `amd64` or `aarch64` appliance.
- At least one running Wyoming speech provider: Home Assistant **Piper** (TTS) or **Whisper** (STT). Both are recommended for both directions.
- A device/client that can reach the Home Assistant appliance's port 8555, or an appropriately configured HTTPS reverse proxy.

This is a Home Assistant **app**, not a HACS integration. You do not need HACS, Unraid, a second server, or Docker CLI access.

## Installation

### Option A: Add to My Home Assistant

1. Click the **Add repository to My Home Assistant** button above.
2. Choose your Home Assistant instance and confirm adding the community app repository.
3. Go to **Settings > Apps > Install app** and locate **Wyoming OpenAI Gateway**.
4. Select **Install**, then **Start**. Enable **Start on boot** and **Watchdog** if offered.
5. Open the app logs to check provider discovery. **Open Web UI** shows the read-only provider and readiness status.

The button adds the **repository**; it does not silently install or start the app.

Installation builds the image on your appliance; allow several minutes for the
first build, particularly on ARM hardware. No Docker CLI commands are needed.

### Option B: Add the repository manually

1. Open Home Assistant **Settings > Apps > Install app**.
2. Open the three-dot menu, select **Repositories**.
3. Add `https://github.com/dmyoung9/ha-wyoming-openai-gateway`.
4. Return to the store, install **Wyoming OpenAI Gateway**, and start it.

### Set up Piper and Whisper

If Piper or Whisper is not installed, install it from Home Assistant's official app store, start it, and finish its normal configuration. The gateway discovers running providers; it does not install or start those apps on your behalf.

## How automatic discovery works

At startup, the gateway queries Supervisor for running apps and checks supported candidates with a Wyoming `Describe` request. It connects over the internal app network, so the standard setup does not require host or port entry.

Inventory comes from `/supervisor/info`, which is accessible with the app's
least-privileged `default` role. Version 0.1.1 fixes a permission error in 0.1.0
that prevented discovery by requesting the restricted `/addons` list instead.

- **One Piper + one Whisper:** Both are selected automatically.
- **Piper only:** TTS works; STT endpoints are unavailable until Whisper is installed/running.
- **Whisper only:** STT works; TTS endpoints are unavailable until Piper is installed/running.
- **Neither running:** The app reports that it is waiting and retries discovery.
- **Multiple compatible providers:** Select the intended provider in the app options; no arbitrary automatic selection.

Official [Piper](https://github.com/home-assistant/addons/blob/master/piper/config.yaml) uses internal TCP port 10200; [Whisper](https://github.com/home-assistant/addons/blob/master/whisper/config.yaml) uses 10300. Neither needs its Wyoming port published to the host for this gateway.

Renamed full slugs ending in `_piper` or `_whisper` are also considered when
metadata declares the conventional internal port. An explicitly selected other
slug requires a unique declared TCP port labeled Wyoming in the returned
metadata. Everything is verified with `Describe`; unsupported metadata requires
a new adapter, not a port scan. Restart the gateway after changing options.

## Connect a client

For a client on the same LAN, configure:

| Setting | Value |
|---|---|
| API base URL | `http://HOME_ASSISTANT_IP:8555/v1/` |
| TTS model | `wyoming` |
| TTS voice | Use an ID returned by `/v1/voices` |
| STT model | `whisper-1` (the gateway's OpenAI-compatible identifier) |
| API key | Not required by the upstream gateway; do not expose the port publicly |

Replace `HOME_ASSISTANT_IP` with the IP address of your Home Assistant OS appliance. Do not use `localhost` from another device. If a native Android app rejects HTTP, use a trusted HTTPS reverse proxy or an appropriate secure connection.

### OpenChamber example

**OpenChamber inside the magnusoverli/opencode Home Assistant add-on:** if it
rejects the gateway's internal URL because remote custom servers are disabled,
see the **[loopback startup-hook workaround](examples/openchamber/README.md)**.
That hook belongs in the OpenCode/OpenChamber container, not this gateway app.
An upstream configuration option is requested in
[magnusoverli/opencode#185](https://github.com/magnusoverli/opencode/issues/185).
The updated hook discovers a ready gateway with **TTS, STT, or both**; it does
not require Piper for a Whisper-only setup. Use its printed loopback base URL
for whichever OpenChamber speech roles you enable.

1. Open OpenChamber's voice/TTS provider settings.
2. Select a **custom/OpenAI-compatible** provider.
3. Set the server/base URL to `http://HOME_ASSISTANT_IP:8555/v1/` (or your HTTPS gateway URL).
4. Set model to `wyoming`.
5. Choose a Piper voice listed by the gateway's `/v1/voices` endpoint.
6. Test playback. If it fails, inspect the gateway app logs and verify the client supports WAV output.

Client-side STT configuration varies; select an OpenAI-compatible transcription endpoint if the client supports it.

## Test the API

After starting the app, run these checks from a device on the same LAN.

List voices:

```bash
curl -fsS http://HOME_ASSISTANT_IP:8555/v1/voices
```

Generate speech (replace the voice ID with one from the response above):

```bash
curl -fS http://HOME_ASSISTANT_IP:8555/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"wyoming","voice":"YOUR_PIPER_VOICE_ID","input":"Hello from Home Assistant","response_format":"wav"}' \
  -o speech.wav
```

Transcribe a local audio file (requires running Whisper):

```bash
curl -fS http://HOME_ASSISTANT_IP:8555/v1/audio/transcriptions \
  -F 'model=whisper-1' \
  -F 'file=@sample.wav'
```

Health checks:

```bash
curl -i http://HOME_ASSISTANT_IP:8555/healthz
curl -i http://HOME_ASSISTANT_IP:8555/readyz
```

Speech output is **WAV**. Clients that require MP3 need a transcoding intermediary.

## Configuration

Configure these in the app's **Configuration** tab:

| Option | Default | Description |
|---|---|---|
| `discovery` | `true` | Detect compatible running Wyoming apps |
| `tts_provider` | `auto` | Auto-select TTS; optionally select a specific discovered app slug |
| `stt_provider` | `auto` | Auto-select STT; optionally select a specific discovered app slug |
| `log_level` | `INFO` | Logging verbosity |

Use a full Supervisor slug to select a specific provider, or `none` to disable
a role. Leave `auto` for automatic selection. Save options and restart the app
after changing them. Setting `discovery: false` disables both providers.

## Provider logging

Leave `log_level` at **INFO** and open the app's **Log** tab. Timestamped entries
show verified Wyoming servers, selected TTS/STT destinations, and advertised
installed voice IDs with their languages. For example:

```text
Verified Wyoming server slug=core_piper host=core-piper port=10200 capabilities=tts
Available Wyoming voice for core_piper: id=en_US-demo-medium languages=en-US
Selected TTS Wyoming endpoint slug=core_piper host=core-piper port=10200
```

Entries appear at startup and when providers, selections, or voice catalogs
change, including outages and recovery—not on every unchanged discovery poll.
Verification describes a successful Wyoming `Describe` connection; speech
connections are made on demand. Catalog output is bounded; `/v1/voices` returns
the gateway's full voice list. Voice summaries also appear in `/status`.

This does **not** enable request-content logging: speech text, audio, Supervisor
tokens, and arbitrary provider metadata remain excluded.

## Troubleshooting

**The app says no Piper or Whisper was found.** Verify the speech apps are installed and **started**, not merely installed, and check their logs. Discovery retries automatically. Inspect **Open Web UI** for the current discovery status.

**More than one Piper or Whisper was found.** Open the status page, copy the intended provider's full slug into `tts_provider` or `stt_provider`, save, and restart the gateway.

**`/v1/voices` is empty or unavailable.** Verify Piper is running and that the gateway log reports a healthy TTS connection. Whisper alone cannot provide voices.

**TTS returns an error.** Confirm the `voice` matches an ID returned by `/v1/voices`, and that the client accepts WAV audio. Test with the curl example before troubleshooting client playback.

**Curl works but an Android app does not.** The client may block cleartext HTTP or send requests from a remote backend. Use a trusted HTTPS endpoint and verify that the client can reach it.

**Cannot reach port 8555.** Check that the app is running, the port is mapped to the HAOS host, and your client can reach the appliance's LAN IP. Do not use an internal container hostname from a phone or laptop.

**Whisper transcription does not work.** Verify Whisper is running and the app reports a healthy STT provider. Confirm the request uses multipart upload to `/v1/audio/transcriptions`.

**App cannot read Supervisor inventory.** Check the app logs for token/API permission errors. Supervisor access is configured by the package; do not paste a Supervisor token into the app options or an issue report.

If running 0.1.0 with `supervisor_http_error` in `/status`, update to 0.1.1 and
restart. Newer status responses include `supervisor_http_status` to distinguish
HTTP failures without exposing credentials. Keep `8099` as your client port if
you mapped the app's internal `8555/tcp` to host port `8099`.

**The app does not appear in the store.** Verify the repository URL, refresh the
store using its **Check for updates** action, and check Supervisor logs for
repository-loading errors. This is a community app repository, not HACS.

For bug reports, include Home Assistant OS version, Supervisor version, gateway app version, which Piper/Whisper apps are installed, sanitized logs, and a reproduction command. **Never include access tokens, private recordings, or public access credentials.**

## Security

Port 8555 is an **unauthenticated trusted-LAN service**. Do not forward it directly to the internet. For remote access, use a VPN or a secured HTTPS reverse proxy with authentication. The gateway forwards speech to your Wyoming providers, not to OpenAI; separately check the privacy behavior of any client you connect.

## Credits and license

This project is a Home Assistant packaging and discovery wrapper around **[Wyoming-OpenAI-Gateway by Archmonger](https://github.com/Archmonger/Wyoming-OpenAI-Gateway)** and its contributors. The underlying gateway is licensed under the **MIT License**. We are grateful for their work making Wyoming services accessible through OpenAI-compatible endpoints.

The community wrapper is independently maintained by **[dmyoung9](https://github.com/dmyoung9)**. It is **not an official Home Assistant app**, and is not affiliated with Home Assistant, OpenAI, or the upstream gateway maintainers. The source build pins upstream commit [`a4e000b650dbc55cefe4e5c02e6f97a4ec4b0eae`](https://github.com/Archmonger/Wyoming-OpenAI-Gateway/commit/a4e000b650dbc55cefe4e5c02e6f97a4ec4b0eae); the upstream copyright and exact MIT notice are included in `wyoming_openai_gateway/LICENSE.upstream` and described in `NOTICE.md`.

Upstream source: https://github.com/Archmonger/Wyoming-OpenAI-Gateway  
Home Assistant app development: https://developers.home-assistant.io/docs/apps/  
Issue tracker: https://github.com/dmyoung9/ha-wyoming-openai-gateway/issues

## Validation and known limitations

**Verified:** 33 gateway automated tests plus optional proxy-hook tests,
synthetic Wyoming TTS/STT through the real
pinned gateway and wrapper, Supervisor metadata schemas, lint and security
checks, and successful **amd64 and arm64 image builds and no-provider smoke
tests** in [CI](https://github.com/dmyoung9/ha-wyoming-openai-gateway/actions).

The owner has confirmed working discovery after the 0.1.1 permission fix on a
Home Assistant appliance. **Still pending:** the complete real-provider audio,
outage/recovery, and dual-architecture HAOS acceptance matrix.
That is why 0.1.2 is a prerelease: use a test appliance or a recoverable setup
and report results before relying on it for daily use. Discovery is limited to
the adapters and metadata described above; arbitrary LAN servers and MP3 output
are not supported.

Releases are published manually; CI does not publish images or releases.
See [implementation evidence](IMPLEMENTATION_STATUS.md),
[app documentation](wyoming_openai_gateway/DOCS.md),
[contribution guide](CONTRIBUTING.md), and [security policy](SECURITY.md).
