# Implementation and release evidence

This is an experimental source-build Home Assistant community app, not a
validated HAOS release. `IMPLEMENTATION_PLAN.md` remains the original design.

## Implemented design

- A persistent HTTP frontend owns port 8555 and exposes process health,
  readiness, and read-only discovery status even when no provider is available.
- Supervisor inventory and bounded Wyoming `Describe` probes determine usable
  providers. Published host ports are not used as internal destination ports.
- Home Assistant app options provide explicit provider selection; a separate
  configuration UI is unnecessary for v1. `none` disables a role.
- The upstream gateway is pinned to commit
  `a4e000b650dbc55cefe4e5c02e6f97a4ec4b0eae` and runs as a supervised child
  listening only on loopback. Endpoint changes require a child restart.
- Packaging uses a Python PID 1 rather than s6, so `init: false` is intentional.
- CI builds source images; it does not publish images or GitHub releases.
  Separate security checks audit the Python lock, scan tracked source for
  secrets, and retain a Python-dependency CycloneDX SBOM (not an OS-image SBOM).

## Evidence and limits

Local verification on Python 3.13 passed 26 tests, including synthetic Wyoming
WAV synthesis and multipart transcription through the real pinned gateway and
wrapper (both roles and each role alone), transcript-log suppression, discovery
and ambiguity, startup deadlines, child recycling, request limits, and SIGTERM.
Pinned Supervisor repository/app/translation schemas, Ruff, and YAML lint passed.
An amd64 image built from the app directory; its no-provider health was HTTP
200, readiness was HTTP 503, and SIGTERM exited 0 in under a second.
These results do not prove real model quality, real Supervisor authorization,
or appliance installation. CI repeats checks and builds both image architectures.

The pinned miniaudio version supplies a CPython 3.13 Linux x86_64 wheel but no
equivalent aarch64 wheel. The build therefore needs a compiler for aarch64;
successful amd64 installation does not establish arm64 compatibility.

## Release gates still requiring real appliances

- [ ] Clean installation through the public repository and My Home Assistant button.
- [ ] HAOS amd64 and aarch64 startup, shutdown, update, and default-role authorization.
- [ ] Real Piper only, Whisper only, both, and neither.
- [ ] Real TTS WAV/media types and multipart STT with known recordings.
- [ ] Provider stop/restart, DNS delays, duplicate and renamed providers.
- [ ] HTTPS/mobile client compatibility and README-only troubleshooting.
- [ ] Publish 0.1.0 as a prerelease only after the intended validation is complete.
- [ ] Publish 1.0.0 only after the full plan's release acceptance criteria pass.

No appliance integration result or release is implied by publishing this source.
