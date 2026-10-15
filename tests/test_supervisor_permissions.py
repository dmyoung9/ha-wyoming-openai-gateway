"""Exercise inventory discovery against a least-privilege Supervisor mock."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from support import TOKEN_SENTINEL


def test_discovery_uses_default_role_permitted_inventory(discovery, caplog):
    paths = []
    responses = {
        "/supervisor/info": {"addons": [
            {"slug": "core_piper", "name": "Piper", "state": "started"},
            {"slug": "core_whisper", "name": "Whisper", "state": "stopped"},
        ]},
        "/addons/core_piper/info": {
            "slug": "core_piper", "name": "Piper", "state": "started",
            "ports": {"10200/tcp": None},
        },
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            authenticated = self.headers.get("Authorization") == f"Bearer {TOKEN_SENTINEL}"
            allowed = self.path in responses and authenticated
            payload = {"result": "ok", "data": responses[self.path]} if allowed else {
                "result": "error", "message": "Forbidden",
            }
            body = json.dumps(payload).encode()
            self.send_response(200 if allowed else 403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = discovery.SupervisorClient(
            TOKEN_SENTINEL, base_url=f"http://127.0.0.1:{server.server_port}", timeout=1,
        )
        providers, warnings = discovery.discover_providers(
            client, probe=lambda *_args: frozenset({"tts"}),
        )
        assert warnings == []
        assert [provider.slug for provider in providers] == ["core_piper"]
        assert paths == ["/supervisor/info", "/addons/core_piper/info"]
        assert TOKEN_SENTINEL not in caplog.text
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_inventory_http_status_is_visible_without_credentials(discovery):
    manager = discovery.GatewaySupervisor(discovery.GatewayOptions(), token=TOKEN_SENTINEL)
    manager._publish_discovery_failure("supervisor_http_error", 403)
    status = manager.status.snapshot()
    assert status["supervisor_http_status"] == 403
    assert status["last_error"] == "supervisor_http_error"
    assert not status["ready"]
    assert TOKEN_SENTINEL not in json.dumps(status)
