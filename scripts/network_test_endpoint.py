"""Loopback-only fixture endpoint for KAVACHNET's opt-in network collector."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status, body, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, b'{"status":"ok"}')
        elif self.path == "/throughput":
            self._send(200, b"KAVACHNET-SYNTHETIC-THROUGHPUT\n" * 32768, "application/octet-stream")
        else:
            self._send(404, b'{"error":"not_found"}')

    def do_POST(self):
        if self.path != "/ack":
            self._send(404, b'{"error":"not_found"}')
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > 1024:
            self._send(413, b'{"error":"payload_too_large"}')
            return
        try:
            payload = json.loads(self.rfile.read(length))
            message_id = payload.get("message_id")
            if not isinstance(message_id, str) or len(message_id) > 64:
                raise ValueError
        except (json.JSONDecodeError, ValueError, AttributeError):
            self._send(400, b'{"error":"invalid_message"}')
            return
        self._send(200, json.dumps({"message_id": message_id}).encode())

    def log_message(self, *_args):
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8787), Handler)
    print("KAVACHNET synthetic measurement endpoint listening on http://127.0.0.1:8787")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
