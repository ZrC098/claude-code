"""HTTP webhook that receives WhatsApp messages."""

import hashlib
import hmac
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .config import env
from .whatsapp import parse_payload

log = logging.getLogger("mail_chain")
MAX_BODY = 1_000_000


def make_handler(engine):
    path = engine.cfg.webhook_path
    token = env("WEBHOOK_TOKEN", "")
    meta_verify = env("META_VERIFY_TOKEN", "")
    meta_secret = env("META_APP_SECRET", "")

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code, body=b""):
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            log.debug("http: " + fmt, *args)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/health":
                return self._reply(200, b"ok")
            if url.path != path:
                return self._reply(404)
            # Meta subscription check.
            q = parse_qs(url.query)
            if (meta_verify and q.get("hub.mode") == ["subscribe"]
                    and hmac.compare_digest(q.get("hub.verify_token", [""])[0], meta_verify)):
                return self._reply(200, q.get("hub.challenge", [""])[0].encode())
            return self._reply(403)

        def _authorised(self, raw):
            signature = self.headers.get("X-Hub-Signature-256", "")
            if meta_secret and signature:
                expected = "sha256=" + hmac.new(meta_secret.encode(), raw, hashlib.sha256).hexdigest()
                return hmac.compare_digest(signature, expected)
            if not token:
                log.error("WEBHOOK_TOKEN is not set: refusing webhook calls")
                return False
            supplied = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            supplied = supplied or parse_qs(urlparse(self.path).query).get("token", [""])[0]
            return hmac.compare_digest(supplied, token)

        def do_POST(self):
            if urlparse(self.path).path != path:
                return self._reply(404)
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._reply(400)
            raw = self.rfile.read(length)
            if not self._authorised(raw):
                return self._reply(401)
            try:
                payload = json.loads(raw)
            except ValueError:
                return self._reply(400)
            started = []
            for msg in parse_payload(payload):
                try:
                    chain_id = engine.on_whatsapp_message(msg)
                except Exception:
                    log.exception("failed to start a chain for message %s", msg.message_id)
                    return self._reply(500)
                if chain_id:
                    started.append(chain_id)
            return self._reply(200, json.dumps({"chains": started}).encode())

    return Handler


def serve(engine):
    httpd = ThreadingHTTPServer((engine.cfg.listen_host, engine.cfg.listen_port), make_handler(engine))
    log.info("webhook listening on %s:%s%s", engine.cfg.listen_host,
             engine.cfg.listen_port, engine.cfg.webhook_path)
    return httpd
