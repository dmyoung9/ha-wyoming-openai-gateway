# Implementation and release evidence

Version 0.1.2 is a prerelease for HAOS acceptance testing, not a stable or
HAOS-validated release. `IMPLEMENTATION_PLAN.md` remains the original design.

## Implemented design

- A persistent HTTP frontend owns port 8555 and exposes process health,
  readiness, and read-only discovery status even when no provider is available.
- Supervisor inventory and bounded Wyoming `Describe` probes determine usable
  providers. Published host ports are not used as internal destination ports.
- Home Assistant app options provide explicit provider selection; a separate
  configuration UI is unnecessary for v1. `none` disables a role.
- INFO logs show verified provider endpoints, selected roles, bounded voice
  catalogs, and changes/recovery without repeating unchanged polls or logging
  speech/request content. Voice-only changes do not restart the gateway child.
- The upstream gateway is pinned to commit
  `a4e000b650dbc55cefe4e5c02e6f97a4ec4b0eae` and runs as a supervised child
  listening only on loopback. Endpoint changes require a child restart.
- Packaging uses a Python PID 1 rather than s6, so `init: false` is intentional.
- CI builds source images; it does not publish images or GitHub releases.
  Separate security checks audit the Python lock, scan tracked source for
  secrets, and retain a Python-dependency CycloneDX SBOM (not an OS-image SBOM).

## Evidence and limits

Local verification on Python 3.13 passed 33 tests, including synthetic Wyoming
WAV synthesis and multipart transcription through the real pinned gateway and
wrapper (both roles and each role alone), transcript-log suppression, discovery
and ambiguity, startup deadlines, child recycling, request limits, and SIGTERM.
Pinned Supervisor repository/app/translation schemas, Ruff, and YAML lint passed.
An amd64 image built from the app directory; its no-provider health was HTTP
200, readiness was HTTP 503, and SIGTERM exited 0 in under a second.
These results do not prove real model quality, real Supervisor authorization,
or appliance installation. [CI on the implementation commit](https://github.com/dmyoung9/ha-wyoming-openai-gateway/actions/runs/38773364984)
passed all jobs, including amd64 and arm64 source-image builds and no-provider
container smoke tests. The arm64 job used QEMU emulation, not a real HAOS device.

The pinned miniaudio version supplies a CPython 3.13 Linux x86_64 wheel but no
equivalent aarch64 wheel. The build includes a compiler and locked build
requirements; the aarch64 source build has now succeeded in CI.

## Release gates still requiring real appliances

- [ ] Clean installation through the public repository and My Home Assistant button.
- [ ] HAOS amd64 and aarch64 startup, shutdown, update, and default-role authorization.
- [ ] Real Piper only, Whisper only, both, and neither.
- [ ] Real TTS WAV/media types and multipart STT with known recordings.
- [ ] Provider stop/restart, DNS delays, duplicate and renamed providers.
- [ ] HTTPS/mobile client compatibility and README-only troubleshooting.
- [ ] Publish 1.0.0 only after the full plan's release acceptance criteria pass.

## First appliance report and discovery correction

The first appliance installation reached the gateway frontend, but discovery
reported `supervisor_http_error`. Inspection of Supervisor's actual access
patterns revealed that the `default` role allows `/supervisor/info` and provider
info requests, but not the `/addons` inventory list used by 0.1.0. Version 0.1.1
uses the installed-app inventory in `/supervisor/info`, with a reproducing HTTP
regression test and pinned Supervisor ACL validation. No privileges were added.
The owner subsequently confirmed that 0.1.1 works. This is the first working
appliance report, not completion of the full audio/architecture/recovery matrix.

The 0.1.2 prerelease makes a versioned build available for the appliance testing
above; it does not claim that those acceptance gates have passed.

## Independent speech roles in the optional OpenChamber hook

The gateway already supports either Wyoming role without the other. The optional
hook initially depended on a nonempty voice listing during target discovery;
that client-side dependency is now removed. It uses the gateway's readiness and
selected-role metadata for TTS-only, STT-only, and combined setups, retaining a
voice-list fallback for other TTS servers. Generic health alone is not treated
as proof of transcription support.

Local integration tests run the actual Node proxy through the real wrapper and
pinned upstream gateway to synthetic Wyoming TTS/ASR services with the unused
provider actually absent. They verify valid WAV output, multipart transcription,
and expected unavailable-role responses. These are protocol/transport tests,
not real speech-model or full HAOS lifecycle validation. The proxy's default
upstream idle deadline is now five minutes, configurable within bounded limits.

This update is to the separately installed hook, not a gateway image release.
