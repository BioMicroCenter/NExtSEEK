"""Thin HTTP front for the laya router sidecar (JevLevROUTING, SPEC s10).

Wire contract (pinned by NessieAI/tests/router/test_laya_integration.py, the real client against this wrapper):
  POST /route  Bearer key; {"state","question_id":"route","prompt","options":{key:text}}
               -> {"revision","probabilities","answer_confidence","state_tokens","truncated"}
  GET  /health -> {"revision"}   (open: it is a liveness probe and leaks nothing else)

Why a wrapper and not `laya-serve`: it hides laya's own API behind the fixed contract, reports the revision
(a local checkpoint folder carries none), and has no code path that logs a request body. stdlib only; torch and
laya load in main(), so the tests run with a fake agent.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("laya_wrapper")
MAX_BODY = 64 * 1024  # a condensed state is under 512 tokens; this only bounds abuse
QID = "route"


def _valid(body) -> bool:
    return (
        isinstance(body, dict)
        and isinstance(body.get("state"), str)
        and body.get("question_id") == QID
        and isinstance(body.get("prompt"), str) and body["prompt"].strip() != ""
        and isinstance(body.get("options"), dict) and body["options"]
        and all(isinstance(k, str) and isinstance(v, str) for k, v in body["options"].items())
    )


def make_server(agent, revision: str, api_key: str, host: str, port: int) -> ThreadingHTTPServer:
    if not api_key or api_key == "SET_IN_LOCAL_ENV":  # the env templates' placeholder is a public key
        raise SystemExit("LAYA_API_KEY is empty or the template placeholder: refusing to start an open sidecar")
    expected = ("Bearer " + api_key).encode()
    lock = threading.Lock()  # one forward pass at a time: CPU torch with 4 threads, one copy for all workers

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # never the default stderr line: it carries the request path and client
            pass

        def _send(self, code: int, obj: dict) -> None:
            data = json.dumps(obj, allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/health":
                self._send(200, {"revision": revision})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/route":
                return self._send(404, {"error": "not found"})
            given = (self.headers.get("Authorization") or "").encode()
            if not hmac.compare_digest(given, expected):
                return self._send(401, {"error": "unauthorized"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if not 0 < n <= MAX_BODY:
                    raise ValueError
                body = json.loads(self.rfile.read(n))
            except ValueError:
                return self._send(400, {"error": "bad request"})
            if not _valid(body):
                return self._send(400, {"error": "bad request"})
            try:
                with lock:
                    out = agent.system_one(
                        body["state"],
                        {QID: {"type": "choice", "instructions": body["prompt"], "criteria": body["options"]}},
                    )
                ans, usage = out["answers"][QID], out["usage"]
                reply = {
                    "revision": revision,
                    "probabilities": ans["probabilities"],
                    "answer_confidence": ans["answer_confidence"],
                    "state_tokens": usage["state_tokens"],
                    "truncated": bool(usage["truncated"]),
                }
                json.dumps(reply, allow_nan=False)  # NaN or inf from the model: a 500, never a non-JSON body
            except Exception as exc:  # noqa: BLE001 -- type name only: the message can quote the state
                log.error("inference failed: %s", type(exc).__name__)
                return self._send(500, {"error": "inference failed"})
            self._send(200, reply)

    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    revision = os.environ.get("LAYA_REVISION", "")
    model_dir = os.environ.get("LAYA_MODEL_DIR", "/models")
    if not revision or revision == "unset" or not os.path.isdir(model_dir):
        raise SystemExit("LAYA_REVISION and a mounted checkpoint at LAYA_MODEL_DIR are required")
    import torch
    from laya import Agent

    torch.set_num_threads(int(os.environ.get("LAYA_THREADS", "4")))
    agent = Agent(model_dir, device="cpu")  # local path; HF_HUB_OFFLINE=1 in the image
    server = make_server(agent, revision, os.environ.get("LAYA_API_KEY", ""), "0.0.0.0", 8080)
    log.info("laya sidecar ready revision=%s", revision)  # the revision only, never request text
    server.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
