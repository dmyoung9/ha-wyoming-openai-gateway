# Security policy

## Reporting a vulnerability

Please do not report exploitable vulnerabilities in a public issue. Use GitHub's
private vulnerability reporting for this repository when available. If it is
not enabled, contact the repository owner privately through the contact method
listed on the owner's GitHub profile. Include affected versions, impact, and a
minimal reproduction. Do not include real credentials or recordings.

## Deployment warnings

The API is unauthenticated. Keep it on a trusted LAN, or protect remote access
with a VPN or a properly configured HTTPS reverse proxy that provides
authentication. Never expose port 8555 directly to the public internet.

Supervisor access must remain limited to the default role. The wrapper must
never log `SUPERVISOR_TOKEN`, raw audio, or transcription payloads. Do not
provide Docker socket, host networking, privileged capabilities, or HA
configuration mounts as a workaround.

## Support status

This package is experimental and unreleased; it has not yet been validated on a real HAOS system.
Security review and end-to-end verification remain release blockers.
