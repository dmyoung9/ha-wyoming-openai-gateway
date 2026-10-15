# Changelog

## 0.1.1 — 2026-10-15 (prerelease)

- Fix discovery failing with `supervisor_http_error`: obtain installed-app
  inventory from `/supervisor/info`, permitted by Supervisor's `default` role,
  instead of `/addons`, which requires broader privileges.
- Preserve least-privileged access; no manager/admin role is needed.
- Include the Supervisor HTTP status code in `/status` for safe diagnostics.
- Add a regression test reproducing the inventory permission denial, plus CI
  checks against the pinned Supervisor's actual default-role access patterns.
- 28 automated tests pass. Existing options and network mappings are unchanged.

## 0.1.0 — 2026-10-15 (prerelease)

- Initial community-app packaging around Wyoming-OpenAI-Gateway upstream commit
  [`a4e000b`](https://github.com/Archmonger/Wyoming-OpenAI-Gateway/commit/a4e000b650dbc55cefe4e5c02e6f97a4ec4b0eae).
- Source-build Docker image for `amd64` and `aarch64`; no image publication is
  configured.
- Supervisor inventory and Wyoming capability probing for official providers,
  metadata-backed renamed providers, and explicit selection when ambiguous.
- OpenAI-compatible speech, transcription, translation, and voice endpoints.
- Persistent health/status frontend, bounded uploads, supervised gateway
  restarts, startup deadlines, and recovery during Supervisor outages.
- Suppression of upstream transcript/content logging and Supervisor-token
  removal from the child environment.
- 26 automated tests; CI builds and smoke-tests both architectures, validates
  Supervisor schemas, and performs lint, dependency, and secret checks.
- Published for initial HAOS testing. Real-appliance installation, model
  behavior, and provider recovery still require acceptance testing.
