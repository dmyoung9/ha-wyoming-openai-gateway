# Test coverage

Tests derive from `IMPLEMENTATION_PLAN.md` §§5–8. Discovery tests parse the
Supervisor fixtures, require running installed supported apps, preserve a full
slug in DNS normalization, distinguish provider roles, and cover automatic,
explicit, disabled, unavailable, and ambiguous selection. A loopback Wyoming
peer validates a real JSON-line `Describe` request and `data_length` response,
plus malformed, oversized, and bounded-stall responses. Provider discovery is
checked against declared internal ports rather than mapped host-port values.

Runtime tests launch the wrapper as a subprocess with no Supervisor token and
verify process health remains distinct from readiness, unready API traffic is
rejected, oversized HTTP bodies are bounded, and SIGTERM exits cleanly. Manager
tests check child environment secret filtering, unchanged-selection stability,
child-exit restart, and child shutdown when providers are disabled. Discovery
is imported from its deployed script path, without relying on package install.

Live Supervisor authorization/role, real HA app DNS, image architectures, and
actual Piper/Whisper services still require HAOS integration. The separate
`test_upstream_smoke.py` exercises the pinned upstream gateway when available.
