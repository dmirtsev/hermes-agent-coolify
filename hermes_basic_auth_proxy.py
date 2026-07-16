#!/usr/bin/env python3
import base64
import hmac
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


LISTEN_HOST = os.environ.get("HERMES_EDGE_BASIC_AUTH_LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("HERMES_EDGE_BASIC_AUTH_LISTEN_PORT", "9119"))
UPSTREAM_HOST = os.environ.get("HERMES_EDGE_BASIC_AUTH_UPSTREAM_HOST", "127.0.0.1")
UPSTREAM_PORT = int(os.environ.get("HERMES_EDGE_BASIC_AUTH_UPSTREAM_PORT", "19119"))
USERNAME = os.environ["HERMES_EDGE_BASIC_AUTH_USERNAME"]
PASSWORD = os.environ["HERMES_EDGE_BASIC_AUTH_PASSWORD"]

EXPECTED_AUTH = "Basic " + base64.b64encode(
    f"{USERNAME}:{PASSWORD}".encode("utf-8")
).decode("ascii")

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_HEAD(self):
        self._proxy()

    def do_GET(self):
        self._proxy()

    def do_POST(self):
        self._proxy()

    def do_PUT(self):
        self._proxy()

    def do_PATCH(self):
        self._proxy()

    def do_DELETE(self):
        self._proxy()

    def do_OPTIONS(self):
        self._proxy()

    def _authorized(self):
        authorization = self.headers.get("Authorization", "")
        proxy_authorization = self.headers.get("Proxy-Authorization", "")
        return hmac.compare_digest(
            authorization, EXPECTED_AUTH
        ) or hmac.compare_digest(proxy_authorization, EXPECTED_AUTH)

    def _send_unauthorized(self):
        body = b"Unauthorized\n"
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Hermes test"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _proxy(self):
        if not self._authorized():
            self._send_unauthorized()
            return

        content_length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(content_length) if content_length else None
        upstream_url = f"http://{UPSTREAM_HOST}:{UPSTREAM_PORT}{self.path}"

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "host"
        }
        if hmac.compare_digest(headers.get("Authorization", ""), EXPECTED_AUTH):
            headers.pop("Authorization", None)
        headers["Host"] = f"{UPSTREAM_HOST}:{UPSTREAM_PORT}"

        request = urllib.request.Request(
            upstream_url,
            data=body,
            headers=headers,
            method=self.command,
        )

        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                self._copy_response(response.status, response.headers, response.read())
        except urllib.error.HTTPError as exc:
            self._copy_response(exc.code, exc.headers, exc.read())
        except Exception:
            response_body = b"Bad Gateway\n"
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(response_body)

    def _copy_response(self, status, headers, body):
        self.send_response(status)
        sent_length = False
        for key, value in headers.items():
            if key.lower() in HOP_BY_HOP_HEADERS:
                continue
            if key.lower() == "content-length":
                sent_length = True
            self.send_header(key, value)
        if not sent_length:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, format, *args):
        sys.stderr.write("[hermes-edge-auth] %s %s\n" % (self.command, self.path))


if __name__ == "__main__":
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), ProxyHandler)
    print(
        f"[hermes-edge-auth] listening on {LISTEN_HOST}:{LISTEN_PORT}, "
        f"proxying to {UPSTREAM_HOST}:{UPSTREAM_PORT}",
        flush=True,
    )
    server.serve_forever()
