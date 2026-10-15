# Contributing

This is an experimental Home Assistant community app. Please open an issue
before substantial changes, especially changes to discovery behavior, network
exposure, Supervisor permissions, or upstream dependency pins.

## Development checks

Use Python 3.13 and install runtime dependencies with the checked-in hash-locked
`wyoming_openai_gateway/requirements.lock` and the hash-locked build tools in
`wyoming_openai_gateway/build-requirements.lock`. The CI workflow additionally runs YAML lint, ShellCheck,
Ruff, the tests, app metadata checks, and source builds for `amd64` and
`aarch64` without pushing images.

```sh
python scripts/validate_metadata.py
yamllint -s repository.yaml wyoming_openai_gateway/config.yaml wyoming_openai_gateway/translations/en.yaml
shellcheck wyoming_openai_gateway/run.sh
ruff check wyoming_openai_gateway/rootfs/usr/local/bin/discover.py scripts/validate_metadata.py tests
pytest -q tests
```

The optional OpenChamber loopback-hook tests also use Node.js (CI uses Node 24).
`tests/test_openchamber_proxy.py` extracts the JS embedded in the single-file
hook and exercises it with loopback HTTP fixtures; it does not execute startup
hooks or write to `/data`. Check the Bash wrapper with:

```sh
bash -n examples/openchamber/10-openchamber-loopback-proxy.sh
shellcheck examples/openchamber/10-openchamber-loopback-proxy.sh
```

Test against actual Home Assistant OS/Supervisor as well; local tests and
container builds do not establish Supervisor compatibility. Do not change the
upstream source commit or dependency lock without reviewing upstream changes,
regenerating the lock, and recording the provenance in `NOTICE.md`.

## Pull requests

- Keep Supervisor privileges and exposed network surface minimal.
- Add tests for discovery, provider selection, malformed API responses, and
  failure/retry behavior when changing runtime behavior.
- Do not add real network addresses, credentials, recordings, or Supervisor
  tokens to code, test fixtures, logs, or documentation.
- Do not publish an image or release from CI. Publication requires an explicit
  manual decision. Prereleases may be published for HAOS acceptance testing;
  stable releases require the acceptance gates in `IMPLEMENTATION_STATUS.md`.
