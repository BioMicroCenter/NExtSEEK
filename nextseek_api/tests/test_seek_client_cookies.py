"""Each SEEK API call authenticates on its own; no cookies are kept between calls, even for the same caller."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory

from nextseek_api.helpers import SeekAPIClient


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"cookie": self.headers.get("Cookie")}).encode()
        self.send_response(200)
        self.send_header("Set-Cookie", "_seek_session=abc; Path=/")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _basic(user):
    import base64
    tok = base64.b64encode(f"{user}:pw".encode()).decode()
    return APIRequestFactory().get("/x/", HTTP_AUTHORIZATION=f"Basic {tok}")


class SeekClientCookieTests(SimpleTestCase):
    def test_seek_client_keeps_no_cookies_between_calls(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with override_settings(SEEK_URL=f"http://127.0.0.1:{srv.server_port}"):
                client = SeekAPIClient()
                client.get_sop(_basic("a"), "1")
                body, code, _, _ = client.get_sop(_basic("a"), "1")
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(code, 200)
        self.assertIsNone(json.loads(body)["cookie"])
        self.assertEqual(len(client.session.cookies), 0)
