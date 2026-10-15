# OpenChamber-in-Home-Assistant loopback workaround

Use this **opt-in startup hook** when OpenChamber inside
[magnusoverli/opencode](https://github.com/magnusoverli/opencode) rejects the
gateway's internal URL with:

> Remote custom server URLs are disabled. Set
> OPENCHAMBER_ALLOW_REMOTE_OPENAI_COMPAT_URLS=true to allow this host.

The requested upstream configuration option is tracked in
**[magnusoverli/opencode#185](https://github.com/magnusoverli/opencode/issues/185)**.
This hook is a temporary alternative, not a required part of the gateway app.
Standalone clients that already accept the gateway URL do not need it.

**Missing microphone button?** That can be a browser secure-context or permission
problem, independent of this proxy and Whisper. See
[the microphone troubleshooting steps](../../README.md#missing-microphone-button-in-openchamber)
for the voice-input setting, HTTPS, and the temporary Chrome-only HTTP exception.
The Chrome flag does not affect the HA Companion app's embedded WebView.

## Why the hook runs in the other container

In add-on 3.2.3, [OpenChamber starts with `env -i` and an explicit variable list](https://github.com/magnusoverli/opencode/blob/3a9e1a45d1408fe098e08f20a54d79e2875882de/ha_opencode/rootfs/etc/s6-overlay/s6-rc.d/ha-openchamber/run#L15-L26).
The remote-URL opt-in is not included, and OpenChamber does not source the
add-on's `/data/.env_vars`. The existing environment option is not a general
OpenChamber environment pass-through. Exporting the flag from a separate hook
cannot change the separately launched OpenChamber service's environment.

The hook creates this route instead:

```text
OpenCode/OpenChamber add-on container                  Gateway app container
  OpenChamber -> 127.0.0.1:8555 -> loopback proxy ----> internal app DNS:8555
```

**Install the hook in the OpenCode/OpenChamber add-on, not this gateway app.**
A listener on loopback in the gateway container would not be reachable through
OpenChamber's loopback interface.

## Install

1. Start Wyoming OpenAI Gateway and **Piper, Whisper, or both**. Confirm the
   gateway's `/readyz` returns HTTP 200 with at least one selected provider.
   Piper is not required for Whisper-only operation; Whisper is not required
   for Piper-only operation.
2. In the **OpenCode add-on's Configuration** tab, enable **Startup hooks
   (beta)** (`user_hooks_enabled: true`) and restart it. This creates
   `<Home Assistant config>/opencode/startup.d/` for the stable add-on.
3. Review and save
   **[10-openchamber-loopback-proxy.sh](10-openchamber-loopback-proxy.sh)** in
   that folder using File Editor, Samba, or Studio Code Server. Save the entire
   Bash file, not just its embedded JavaScript. The beta add-on has a separate
   hook folder; use the location documented by your installed add-on.
   If you already run a custom loopback proxy, replace or disable its old hook
   rather than running both. A proxy under a different `/data` path is not
   covered by this hook's duplicate-process check.
4. Restart the OpenCode add-on, or run this from its own terminal:

   ```sh
   ha-hooks run 10-openchamber-loopback-proxy.sh
   ha-hooks log 10-openchamber-loopback-proxy.sh
   tail -n 30 /data/openchamber-loopback-proxy/proxy.log
   ```

5. Wait for a **new** `Point the OpenChamber custom speech server at:` line
   after the latest timestamped `starting proxy discovery` message. Use that URL
   in OpenChamber's custom speech server setting, normally:

   ```text
   http://127.0.0.1:8555/v1
   ```

Use that same loopback base URL for OpenChamber's **STT**, **TTS**, or both
custom server settings, according to the providers you run. Transcriptions and
translations use multipart uploads; the proxy preserves the body and headers.

That URL is used by the OpenChamber **backend inside the container**, not by
your phone/browser directly. No new host port mapping is needed. Your gateway's
existing HA host-port mapping is unrelated to this internal route.

The hook writes `/data/openchamber-loopback-proxy/proxy.mjs` and appends to
`/data/openchamber-loopback-proxy/proxy.log` in the **OpenCode container**.
Edit the persistent `.sh` hook: `proxy.mjs` is regenerated on each start.

**Updating an older copy:** replace the persistent hook file and restart the
**OpenCode/OpenChamber add-on**. Rerunning the hook while the old proxy is alive
does not replace it. This is an update to the optional hook, not the gateway
image; refreshing/updating the gateway alone will not update the other
container's hook.

The host image must provide Bash, Node.js 20+, `setsid`, `pgrep`, and `flock`.
No npm packages or extra Python dependencies are required. See the upstream
[startup-hook contract](https://github.com/magnusoverli/opencode/blob/3a9e1a45d1408fe098e08f20a54d79e2875882de/ha_opencode/DOCS.md#startup-hooks-beta).

## Discovery, selection, and lifecycle

- Reads running app inventory through `/v2/apps`, falling back to
  `/supervisor/info` when necessary, including permission failures.
- Reads each app's info through v2 or legacy endpoints. Uses its DNS name and
  declared **internal TCP port keys**, not published host-port values.
- Probes at most 64 apps and eight ports each, with 16 concurrent HTTP probes.
- Probes the gateway's `/readyz` for healthy, selected TTS and/or STT
  capabilities. **Piper-only, Whisper-only, and combined gateways all qualify.**
  A generic HTTP 200 or health response alone does not prove STT support.
- For other TTS servers without gateway readiness metadata, a nonempty
  `/v1/voices` response remains the compatibility fallback. Arbitrary STT-only
  APIs without capability metadata are not automatically detected.
- The first verified speech API wins; the chosen TTS/STT roles are logged.
- **Multiple speech gateways:** first-response selection is not deterministic.
  Pin a full Supervisor slug using the optional setting below rather than
  assuming which server will be chosen.
- Re-resolves the chosen DNS name on each proxied request, so a replacement
  container IP is picked up. DNS, metadata, and upstream idle waits are bounded;
  audio streams through without whole-response buffering.
- Upstream idle timeout defaults to **five minutes**, matching the gateway
  instead of imposing the old 30-second proxy limit on Whisper. Other client
  and gateway deadlines still apply.
- Discovery occurs **at startup only**. If the gateway or its provider(s) are
  not ready yet, the proxy exits; start them and rerun the hook. It does not automatically
  retry initial discovery or discover a renamed target/new port later.
- This is a detached hook process, **not a supervised add-on service**. If it
  crashes, rerun the hook. The hook's three-second check confirms only that the
  process exists, not that discovery has finished. Check the listening URL.
- Port 8555 is preferred; if occupied, a free port is used instead. Read the
  actual printed URL and update OpenChamber if the port changed after restart.
  Concurrent hook starts are serialized with a startup lock.

This HTTP discovery is separate from the gateway's Wyoming provider discovery.
It does not scan arbitrary LAN hosts, discover every speech service, handle
HTTPS-only targets, or add MP3 transcoding.

### Optional settings

Export these near the top of the **hook file**, before it launches Node:

```sh
# Replace this example with the full installed Supervisor slug, if needed.
export OPENCHAMBER_PROXY_TARGET_SLUG="your_repo_wyoming_openai_gateway"
# Choose a stable unused port if 8555 is already taken.
export OPENCHAMBER_PROXY_PORT=8556
# Optional upstream idle timeout, in milliseconds (1000 through 300000).
export OPENCHAMBER_PROXY_UPSTREAM_TIMEOUT_MS=300000
```

These are proxy settings, not OpenChamber settings. The default target is
automatic and the default preferred port is 8555. The listener is restricted to
`127.0.0.1`, `localhost`, or `::1`; do not expose it on the LAN. The Supervisor
URL defaults to `http://supervisor`; only that hostname and trusted loopback
origins are accepted. Do not redirect Supervisor credentials to another server.

Restart the OpenCode add-on after editing an already-running hook: rerunning
the hook deliberately leaves an existing proxy process alone.

## Security and removal

Startup hooks run as **root with the add-on's environment**, including the
Supervisor token and potentially configured credentials. Review the script
before enabling it. Never use `set -x`, put a real token in the file, or paste
unreviewed logs into an issue. The proxy does not log speech bodies or headers;
its Supervisor token is used only for trusted inventory/info requests, not
forwarded to the speech target. Client-supplied headers are forwarded: leave
the gateway API key empty or use a dummy value if the client requires one,
not a real third-party API key.

This deliberately makes a remote speech service reachable through an allowed
loopback address. Use it only with trusted apps. It is a fixed-target reverse
proxy, not a general URL-forwarding endpoint, and has no independent API-key
authentication. Metadata responses are bounded, redirects are rejected, and
request-DNS waits and shutdown are time-bounded. A fresh DNS lookup does not
mean ongoing target rediscovery or a guarantee about the target's behavior.

To remove it, rename the hook to a filename not ending in `.sh` (or remove that
one file), then restart the **OpenCode add-on**. This stops the detached
process. Do not delete the rest of your OpenCode configuration or `/data`.
When an upstream option becomes available, configure that opt-in, switch
OpenChamber to the gateway's direct internal URL, and remove this workaround.

## Provenance and verification

Adapted from the repository owner's working startup hook. The published copy
adds loopback-only binding, trusted Supervisor origins, redirect/metadata
bounds, TCP-only discovery, optional target selection, serialized startup,
and request cleanup. It is not a byte-for-byte copy of the original hook.

Tests execute the embedded JS against loopback HTTP fixtures for both inventory
APIs, port-map shapes, audio streaming, port collisions, invalid/oversized
metadata, rejected redirects/origins, credential non-forwarding, and a stalled
DNS lookup. Role-matrix tests additionally run the actual Node proxy through the
real gateway wrapper and pinned upstream gateway to synthetic Wyoming providers:
TTS only, STT only, and both. These verify WAV output, multipart transcription,
and unavailable-role responses; they are not tests of real speech model quality.
The Bash wrapper is syntax-checked and ShellCheck-checked, not run
against `/data` by the tests. The guarded copy still needs its own HAOS lifecycle
validation; the owner tested the original approach on an appliance.

No gateway app update is required just to use this example. It is a separately
installed, opt-in workaround under the repository's MIT license.
