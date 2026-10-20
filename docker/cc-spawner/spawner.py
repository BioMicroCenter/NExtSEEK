"""cc-spawner: the only service that holds the Docker socket (W9, SEC-004).

The app asks it to run one Container-CC turn, stop one, or run one of three fixed SEEK scripts. Every request is
validated here and the container's image, network, mounts, limits and privileges are built here from the validated
fields, so a compromised app can start a CC turn for a user id it names and nothing more: no image, bind, network or
privilege choice reaches the daemon.

Standard library plus the ``docker`` SDK; no web framework.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

import docker
from docker.errors import APIError, NotFound

logger = logging.getLogger("cc_spawner")

TOKEN_HEADER = "X-Spawner-Token"
MAX_BODY_BYTES = 4 * 1024 * 1024
SCRIPTS_DIR = Path(__file__).resolve().parent / "seek_scripts"
SEEK_OPS = ("create", "patch", "compensate")

# The same shapes cc_engine.py and cc_provision.py enforce on the direct path.
_USER_ID_RE = re.compile(r"^[A-Za-z0-9._@+-]{1,64}$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._@+-]{1,128}$")
_RUN_ID_RE = re.compile(r"^[0-9a-f-]{1,64}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9._:/\[\]@-]{1,200}$")
_SESSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

# Exactly the keys cc_engine.build_agent_environment can produce (a test holds the two lists together).
ALLOWED_ENV_KEYS = frozenset({
    "CLAUDE_CODE_USE_BEDROCK", "ANTHROPIC_BEDROCK_BASE_URL", "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
    "CLAUDE_CODE_ENABLE_AUTO_MODE", "NEXTSEEK_USERNAME", "API_USER", "NEXTSEEK_TURN_PASS", "AWS_REGION",
    "NEXTSEEK_BASE_URL", "NEXTSEEK_URL", "NEXTSEEK_CC_OPS_ROAD", "NEXTSEEK_SIDECAR_HOST",
    "NEXTSEEK_SIDECAR_PORT", "DMAC_PATH_MAPPINGS", "NEXTSEEK_CHAT_SESSION_ID", "NEXTSEEK_CC_TURN_DEADLINE_EPOCH",
    "CLAUDE_CODE_MAX_RETRIES", "API_TIMEOUT_MS", "ANTHROPIC_DEFAULT_SONNET_MODEL",
})
_MAX_ENV_VALUE = 64 * 1024

_TURN_FIELDS = frozenset({
    "run_id", "user_id", "project_dirname", "cc_state_key", "transcripts_subpath", "previous_turns", "turn_files",
    "model_id", "fallback_model_id", "max_budget_usd", "resume_session_id", "environment", "stdin_envelope",
})

_BASE_CMD = [
    "claude", "--print", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
    "--permission-mode", "auto",
]
_WORKDIR = "/home/user"
_CLAUDE_HOME = _WORKDIR + "/.claude"
_TARGETS = {
    "input": "/data/input", "shared": "/data/shared", "scratch": "/data/scratch",
    "previous_turns": "/data/previous_turns", "turn": "/data/turn",
    "transcripts": _WORKDIR + "/.cc-memory/transcripts",
}
_LABEL = "nextseek.cc"
_CONFIRM_EXIT_WAIT_S = 5.0


class Refusal(Exception):
    """A request the spawner will not carry out; ``status`` is the HTTP status it answers with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "0.0.0.0"})
_NEXTSEEK_SERVICE_HOST = "nextseek_nginx"


def _rewrite_loopback_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.hostname in _LOOPBACK_HOSTS:
        return urlunsplit(parts._replace(netloc=_NEXTSEEK_SERVICE_HOST))
    return url


def automode_trust_lines(ns_url: str | None, neo4j_uri: str | None, gcp: bool) -> list[str]:
    """Copy of cc_engine.automode_trust_lines (this image holds only this folder); a parity test holds them equal."""
    environment: list[str] = ["$defaults"]
    if ns_url:
        environment.append(
            "Trusted internal service: the NExtSEEK metadata REST API at "
            f"{_rewrite_loopback_url(ns_url)} — the MIT BioMicro Center lab's own "
            "sample/project database (reached as the authenticated user)."
        )
    if neo4j_uri:
        environment.append(
            f"Trusted internal graph database: Neo4j at {neo4j_uri} (lab lineage graph)."
        )
    if gcp:
        environment.append(
            "Trusted LLM provider: Google Cloud Gemini API — the assistant's own "
            "model provider."
        )
    return environment


@dataclass(frozen=True)
class Config:
    token: str
    users_volume: str = "dmac-cc-users"
    image: str = "dmac-assistant:poc"
    network: str = "dmac-cc-net"
    seek_container: str = "seek"
    max_turns: str = "50"
    mem_limit: str = "4g"
    nano_cpus: int = 2_000_000_000
    pids_limit: int = 512
    fsize_bytes: int = 10 * 1024 ** 3
    # The auto-mode trust text comes from here, never from a request. Non-secret topology only.
    ns_url: str = ""
    neo4j_uri: str = ""
    automode_gcp: bool = False

    def automode_environment(self) -> list[str]:
        return automode_trust_lines(self.ns_url, self.neo4j_uri, self.automode_gcp)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        e = os.environ if env is None else env
        return cls(
            token=e.get("SPAWNER_TOKEN", ""),
            users_volume=e.get("DMAC_CC_USERS_VOLUME", cls.users_volume),
            image=e.get("NEXTSEEK_CC_IMAGE", cls.image),
            network=e.get("NEXTSEEK_CC_NETWORK", cls.network),
            seek_container=e.get("SEEK_CONTAINER", cls.seek_container),
            max_turns=e.get("NEXTSEEK_CC_MAX_TURNS", cls.max_turns),
            mem_limit=e.get("NEXTSEEK_CC_MEM_LIMIT", cls.mem_limit),
            nano_cpus=int(e.get("NEXTSEEK_CC_NANO_CPUS", cls.nano_cpus)),
            pids_limit=int(e.get("NEXTSEEK_CC_PIDS_LIMIT", cls.pids_limit)),
            fsize_bytes=int(e.get("NEXTSEEK_CC_FSIZE_BYTES", cls.fsize_bytes)),
            ns_url=e.get("NEXTSEEK_INTERNAL_BASE_URL", ""),
            neo4j_uri=e.get("NEO4J_URI", ""),
            automode_gcp=e.get("NEXTSEEK_CC_AUTOMODE_GCP", "0") == "1",
        )


# ---- validation -------------------------------------------------------------------------------------------

def _refuse(message: str) -> Refusal:
    return Refusal(422, message)


def _segment(name: str, value: Any, pattern: re.Pattern = _SEGMENT_RE) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value) or value in (".", ".."):
        raise _refuse(f"invalid {name}")
    return value


def _optional(body: dict, key: str):
    value = body.get(key)
    return None if value in (None, "") else value


def _container_name(run_id: str) -> str:
    return f"dmac-cc-agent-{run_id}"


def _subpaths(project: str, user: str, state_key: str | None, run_id: str) -> dict[str, str | None]:
    """The volume-relative tails ``cc_provision.build_user_dirs`` produces (a test holds the two together)."""
    user_rel = f"{project}/{user}"
    return {
        "input": f"{user_rel}/input",
        "shared": f"{project}/shared",
        "scratch": f"{user_rel}/scratch/{run_id}",
        "cc_state": f"{user_rel}/cc-state/{state_key}" if state_key else None,
        "transcripts": f"{user_rel}/_memory/{state_key}/transcripts" if state_key else None,
        "previous_turns": f"{user_rel}/_memory/{state_key}/previous_turns" if state_key else None,
        "turn": f"{user_rel}/_turn/{run_id}",
    }


def _mount(source: str, target: str, subpath: str, read_only: bool) -> dict:
    return {"Target": target, "Source": source, "Type": "volume", "ReadOnly": read_only,
            "VolumeOptions": {"Subpath": subpath}}


def _read_json(raw: bytes) -> dict:
    try:
        body = json.loads(raw or b"{}")
    except ValueError as exc:
        raise _refuse("body is not JSON") from exc
    if not isinstance(body, dict):
        raise _refuse("body must be a JSON object")
    return body


class Spawner:
    def __init__(self, client: Any, config: Config):
        self.client, self.config = client, config

    # ---- auth ----

    def check_token(self, sent: str | None) -> None:
        if not sent:
            raise Refusal(401, "token required")
        if not self.config.token or not hmac.compare_digest(sent.encode(), self.config.token.encode()):
            raise Refusal(403, "token refused")

    # ---- health ----

    def health(self, *, with_detail: bool) -> dict:
        ok, detail = True, "ok"
        try:
            self.client.ping()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"docker daemon unreachable: {type(exc).__name__}"
        if ok:
            try:
                self.client.images.get(self.config.image)
            except Exception:  # noqa: BLE001
                ok, detail = False, f"CC image '{self.config.image}' not found"
        if ok:
            try:
                self.client.networks.get(self.config.network)
            except Exception:  # noqa: BLE001
                ok, detail = False, f"CC network '{self.config.network}' not found"
        return {"ok": ok, "detail": detail} if with_detail else {"ok": ok}

    # ---- turns ----

    def _validate_turn(self, body: dict) -> dict:
        unknown = set(body) - _TURN_FIELDS
        if unknown:
            raise _refuse(f"unknown field: {sorted(unknown)[0]}")
        run_id = body.get("run_id")
        if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
            raise _refuse("invalid run_id")
        user = _segment("user_id", body.get("user_id"), _USER_ID_RE)
        project = _segment("project_dirname", body.get("project_dirname"))
        state_key = _optional(body, "cc_state_key")
        if state_key is not None:
            state_key = _segment("cc_state_key", state_key, _USER_ID_RE)
        subs = _subpaths(project, user, state_key, run_id)
        transcripts = _optional(body, "transcripts_subpath")
        if transcripts is not None and (state_key is None or transcripts != subs["transcripts"]):
            raise _refuse("invalid transcripts_subpath")
        models = {}
        for key in ("model_id", "fallback_model_id"):
            value = _optional(body, key)
            if value is not None and (not isinstance(value, str) or not _MODEL_RE.fullmatch(value)):
                raise _refuse(f"invalid {key}")
            models[key] = value
        budget = body.get("max_budget_usd", 0)
        if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not 0 <= budget <= 100:
            raise _refuse("invalid max_budget_usd")
        resume = _optional(body, "resume_session_id")
        if resume is not None and (not isinstance(resume, str) or not _SESSION_RE.fullmatch(resume)):
            raise _refuse("invalid resume_session_id")
        env = body.get("environment")
        if not isinstance(env, dict):
            raise _refuse("environment must be an object")
        for key, value in env.items():
            if key not in ALLOWED_ENV_KEYS:
                raise _refuse(f"environment key not allowed: {key}")
            if not isinstance(value, str) or len(value) > _MAX_ENV_VALUE:
                raise _refuse(f"environment value not allowed: {key}")
        envelope = body.get("stdin_envelope")
        if not isinstance(envelope, str) or not envelope or len(envelope) > MAX_BODY_BYTES:
            raise _refuse("invalid stdin_envelope")
        return {"run_id": run_id, "user": user, "project": project, "state_key": state_key, "subs": subs,
                "transcripts": transcripts is not None, "previous_turns": bool(body.get("previous_turns")),
                "turn_files": bool(body.get("turn_files")), "budget": budget, "resume": resume, "env": env,
                "envelope": envelope, **models}

    def _mounts(self, v: dict) -> list[dict]:
        vol, subs = self.config.users_volume, v["subs"]
        mounts = [
            _mount(vol, _TARGETS["input"], subs["input"], True),
            _mount(vol, _TARGETS["shared"], subs["shared"], True),
            _mount(vol, _TARGETS["scratch"], subs["scratch"], False),
        ]
        if v["state_key"]:
            mounts.append(_mount(vol, _CLAUDE_HOME, subs["cc_state"], False))
        if v["transcripts"]:
            mounts.append(_mount(vol, _TARGETS["transcripts"], subs["transcripts"], True))
        if v["previous_turns"] and v["state_key"]:
            mounts.append(_mount(vol, _TARGETS["previous_turns"], subs["previous_turns"], True))
        if v["turn_files"]:
            mounts.append(_mount(vol, _TARGETS["turn"], subs["turn"], True))
        return mounts

    def _command(self, v: dict) -> list[str]:
        cmd = list(_BASE_CMD)
        if v["model_id"]:
            cmd += ["--model", v["model_id"]]
        if v["fallback_model_id"] and v["fallback_model_id"] != v["model_id"]:
            cmd += ["--fallback-model", v["fallback_model_id"]]
        cmd += ["--max-turns", self.config.max_turns]
        if v["budget"] > 0:
            cmd += ["--max-budget-usd", str(v["budget"])]
        settings = {"autoMode": {"environment": self.config.automode_environment()}}
        cmd += ["--settings", json.dumps(settings, separators=(",", ":"))]
        if v["resume"]:
            cmd += ["--resume", v["resume"]]
        return cmd

    def _run_kwargs(self, v: dict) -> dict[str, Any]:
        c = self.config
        return {
            "image": c.image, "command": self._command(v), "environment": dict(v["env"]),
            "mounts": self._mounts(v), "working_dir": _WORKDIR, "network": c.network,
            "name": _container_name(v["run_id"]),
            "labels": {_LABEL: "1", "nextseek.cc.user": v["user"], "nextseek.cc.run": v["run_id"]},
            "platform": "linux/amd64", "detach": True, "stdin_open": True, "tty": False,
            "stdout": True, "stderr": True,
            "mem_limit": c.mem_limit, "memswap_limit": c.mem_limit, "nano_cpus": c.nano_cpus,
            "pids_limit": c.pids_limit,
            "ulimits": [docker.types.Ulimit(name="fsize", soft=c.fsize_bytes, hard=c.fsize_bytes)],
            "auto_remove": True, "cap_drop": ["ALL"], "security_opt": ["no-new-privileges=true"],
        }

    def _run_with_stale_name_retry(self, kwargs: dict) -> Any:
        try:
            return self.client.containers.run(**kwargs)
        except APIError as exc:
            if exc.status_code != 409:
                raise
            logger.warning("stale container %s held the name; removing and retrying once", kwargs["name"])
            try:
                self.client.containers.get(kwargs["name"]).remove(force=True)
            except NotFound:
                pass
            return self.client.containers.run(**kwargs)

    def start_turn(self, body: dict) -> tuple[Any, Iterator[bytes]]:
        v = self._validate_turn(body)
        container = self._run_with_stale_name_retry(self._run_kwargs(v))
        raw = container.attach_socket(params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1})
        stream = container.logs(stream=True, follow=True, stdout=True, stderr=False)
        sock = getattr(raw, "_sock", raw)
        sock.sendall(v["envelope"].encode("utf-8"))
        sock.shutdown(1)  # SHUT_WR: Claude Code reads stdin to its end
        return container, stream

    def stop(self, run_id: str) -> dict:
        if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
            raise _refuse("invalid run_id")
        try:
            container = self.client.containers.get(_container_name(run_id))
        except NotFound:
            return {"gone": True}
        labels = (container.attrs.get("Config") or {}).get("Labels") or {}
        if labels.get(_LABEL) != "1":
            raise Refusal(403, "not a CC container")
        return {"gone": self._stop_and_confirm_exit(container)}

    @staticmethod
    def _stop_and_confirm_exit(container: Any) -> bool:
        """The stop cc_engine._stop_and_confirm_exit does: stop, else force-remove, else wait; never raises."""
        try:
            container.stop(timeout=2)
            return True
        except NotFound:
            return True
        except Exception:  # noqa: BLE001
            pass
        try:
            container.remove(force=True)
        except NotFound:
            return True
        except Exception:  # noqa: BLE001
            pass
        try:
            container.wait(timeout=_CONFIRM_EXIT_WAIT_S)
            return True
        except NotFound:
            return True
        except Exception:  # noqa: BLE001
            return False

    # ---- SEEK ops ----

    def seek(self, op: str, payload: Any) -> dict:
        if op not in SEEK_OPS:
            raise Refusal(404, "unknown SEEK op")
        if payload is not None and not isinstance(payload, dict):
            raise _refuse("payload must be an object")
        script = (SCRIPTS_DIR / f"{op}.rb").read_text()
        try:
            container = self.client.containers.get(self.config.seek_container)
            exit_code, output = container.exec_run(
                ["bin/rails", "runner", script], demux=True, workdir="/seek",
                environment={"NEXTSEEK_PAYLOAD": json.dumps(payload or {})})
        except NotFound as exc:
            raise Refusal(503, f"SEEK container {self.config.seek_container!r} not found") from exc
        except APIError as exc:
            raise Refusal(503, "Docker exec into SEEK container failed") from exc
        stdout, stderr = output if isinstance(output, tuple) else (output, b"")
        stdout_text = (stdout or b"").decode("utf-8", errors="replace").strip()
        stderr_text = (stderr or b"").decode("utf-8", errors="replace").strip()
        last = next((ln.strip() for ln in reversed(stdout_text.splitlines()) if ln.strip()), "")
        return {"exit_code": exit_code, "last_line": last, "stderr": stderr_text}


# ---- HTTP -------------------------------------------------------------------------------------------------

def make_server(svc: Spawner, host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # noqa: A003 - request line only, never headers or bodies
            logger.info("%s %s", self.command, self.path.split("?")[0])

        def _send_json(self, status: int, obj: Any) -> None:
            data = json.dumps(obj).encode()
            if status >= 400:
                self.close_connection = True  # a refused request's body may be unread
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> bytes:
            raw = (self.headers.get("Content-Length") or "0").strip()
            if not re.fullmatch(r"[0-9]+", raw):  # a negative length would read until the client closes
                raise Refusal(400, "invalid Content-Length")
            length = int(raw)
            if length > MAX_BODY_BYTES:
                raise _refuse("body too large")
            return self.rfile.read(length) if length else b""

        def _dispatch(self) -> None:
            path = self.path.split("?")[0]
            try:
                if self.command == "GET" and path == "/health":
                    sent = self.headers.get(TOKEN_HEADER)
                    detailed = False
                    if sent:
                        svc.check_token(sent)
                        detailed = True
                    return self._send_json(200, svc.health(with_detail=detailed))
                svc.check_token(self.headers.get(TOKEN_HEADER))
                if self.command != "POST":
                    raise Refusal(404, "not found")
                if path == "/turns":
                    return self._turn(_read_json(self._body()))
                m = re.fullmatch(r"/turns/([^/]+)/stop", path)
                if m:
                    self._body()
                    return self._send_json(200, svc.stop(m.group(1)))
                m = re.fullmatch(r"/seek/([^/]+)", path)
                if m:
                    if m.group(1) not in SEEK_OPS:
                        raise Refusal(404, "unknown SEEK op")
                    return self._send_json(200, svc.seek(m.group(1), _read_json(self._body())))
                raise Refusal(404, "not found")
            except Refusal as exc:
                self._send_json(exc.status, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                logger.exception("request failed")
                self._send_json(502, {"error": f"{type(exc).__name__}"})

        def _turn(self, body: dict) -> None:
            _container, stream = svc.start_turn(body)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for chunk in stream:
                    if chunk:
                        self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                        self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                # The app went away. The turn is not stopped from here: the app's stop call or the container's own
                # end does that.
                self.close_connection = True

        do_GET = do_POST = _dispatch

    return ThreadingHTTPServer((host, port), Handler)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    config = Config.from_env()
    if not config.token:
        logger.warning("SPAWNER_TOKEN is empty: every request but GET /health will be refused")
    server = make_server(Spawner(docker.from_env(), config), "0.0.0.0", int(os.environ.get("SPAWNER_PORT", "8080")))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
