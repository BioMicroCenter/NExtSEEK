"""Automated off-box rollback-baseline push to GHCR (DEPLOYMENT.md §5.2).

After a rebuild of the canonical instance, each freshly built first-party
image is gated for baked secrets, tagged ``baseline-<YYYYMMDD>-<shortsha>``,
and pushed to its private org package — so known-good images survive disk
cleanups on the deploy host (which have destroyed local rollback tags before).

Contract: this step NEVER fails the deploy. ``push_baseline`` returns an
outcome for every failure mode instead of raising; failures are surfaced as a
loud banner at rebuild time, persisted to a state marker
(``startup/.ghcr-push-state.json``, gitignored), and re-surfaced by
``startup doctor`` until a push succeeds again.

Credentials are deliberately per-deploying-user and OUTSIDE the repo/build
context: a classic PAT with ``write:packages`` (its owner must be a
BioMicroCenter org *member*) in ``~/.config/nextseek/ghcr.env`` as
``GHCR_USER=…`` / ``GHCR_TOKEN=…``. The token is passed to ``docker login``
via stdin only — never argv, never logged.
"""
from __future__ import annotations

import datetime
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import orjson

from startup.lib import ui
from startup.lib.rebuild_policy import ImagePolicy, registry_images

REGISTRY_IMAGE = "ghcr.io/biomicrocenter/nextseek"
GHCR_ENV_OVERRIDE_VAR = "NEXTSEEK_GHCR_ENV"
DEFAULT_CREDENTIAL_PATH = "~/.config/nextseek/ghcr.env"
CANONICAL_PROJECT = "nextseek"
STATE_FILENAME = ".ghcr-push-state.json"

# §5.2 gate: files whose presence in the image is always acceptable.
_GATE_ALLOWED_FILES = {"/app/docker/nextseek.env.example"}
# /app/.env is conditionally acceptable: the deployed lineage carries a
# known-benign single-key residue (LURIAKEY = a file path, not a credential —
# user-verified and accepted for the 2026-08-05 baseline push). Any other key
# name in that file fails the gate. Key NAMES may appear in diagnostics;
# values are never read.
_BENIGN_ENV_FILE = "/app/.env"
_BENIGN_ENV_KEYS = {"LURIAKEY"}


@dataclass(frozen=True)
class Credentials:
    user: str
    token: str


@dataclass(frozen=True)
class PushOutcome:
    status: str  # pushed | skipped | gate_failed | no_credentials | push_failed | error
    detail: str = ""
    remediation: str = ""
    tag: str | None = None
    digest: str | None = None
    registry_image: str = REGISTRY_IMAGE


def credential_env_path() -> Path:
    override = os.environ.get(GHCR_ENV_OVERRIDE_VAR)
    if override:
        return Path(override)
    return Path(DEFAULT_CREDENTIAL_PATH).expanduser()


def load_credentials(path: Path) -> Credentials | None:
    """Parse GHCR_USER / GHCR_TOKEN from a shell-style env file.

    Returns None (never raises) when the file is missing, unreadable, or does
    not define both keys — the caller turns that into the nudge outcome.
    """
    try:
        text = path.read_text()
    except OSError:
        return None
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    user, token = values.get("GHCR_USER", ""), values.get("GHCR_TOKEN", "")
    if not user or not token:
        return None
    return Credentials(user=user, token=token)


def compute_baseline_tag(repo_root: Path, today: datetime.date | None = None) -> str:
    """``baseline-<YYYYMMDD>[-<shortsha>[-dirty]]``; date-only if git fails."""
    date_part = (today or datetime.date.today()).strftime("%Y%m%d")
    try:
        head = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
        )
        if head.returncode != 0 or not head.stdout.strip():
            return f"baseline-{date_part}"
        sha = head.stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain"],
            capture_output=True,
            text=True,
        )
        dirty = status.returncode == 0 and bool(status.stdout.strip())
        return f"baseline-{date_part}-{sha}" + ("-dirty" if dirty else "")
    except Exception:
        return f"baseline-{date_part}"


def _image_sh(
    image: str,
    script: str,
    *,
    run_limits: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker", "run", "--rm", "--network", "none", *run_limits,
            "--entrypoint", "sh", image, "-c", script,
        ],
        capture_output=True,
        text=True,
    )


def baked_secret_gate(
    image: str,
    *,
    run_limits: tuple[str, ...] = (),
) -> tuple[bool, str]:
    """DEPLOYMENT.md §5.2 pre-push gate: prove the image carries no baked
    config/secret files. Returns (ok, detail); never raises past the caller's
    catch-all because push_baseline wraps everything."""
    probe = _image_sh(
        image,
        "ls /app/.env /app/*secret*.env /app/docker/*env* "
        "/app/dmac/local_settings.py /home/user/.env /opt/dmac/.env 2>/dev/null; true",
        run_limits=run_limits,
    )
    if probe.returncode != 0:
        return False, f"gate probe could not run (exit {probe.returncode}): {probe.stderr.strip()}"
    found = [line.strip() for line in probe.stdout.splitlines() if line.strip()]
    offending = [f for f in found if f not in _GATE_ALLOWED_FILES and f != _BENIGN_ENV_FILE]
    if _BENIGN_ENV_FILE in found:
        keys_probe = _image_sh(
            image, f"cut -d= -f1 {_BENIGN_ENV_FILE}", run_limits=run_limits
        )
        if keys_probe.returncode != 0:
            offending.append(f"{_BENIGN_ENV_FILE} (key names unreadable)")
        else:
            extra_keys = {
                k.strip() for k in keys_probe.stdout.splitlines() if k.strip()
            } - _BENIGN_ENV_KEYS
            if extra_keys:
                offending.append(
                    f"{_BENIGN_ENV_FILE} (non-benign keys: {', '.join(sorted(extra_keys))})"
                )
    if offending:
        return False, "baked config/secret files found: " + "; ".join(offending)
    return True, "image free of baked config/secret files"


def _state_path(repo_root: Path) -> Path:
    return repo_root / "startup" / STATE_FILENAME


def read_state(repo_root: Path) -> dict | None:
    try:
        return orjson.loads(_state_path(repo_root).read_bytes())
    except (OSError, orjson.JSONDecodeError):
        return None


def _record(repo_root: Path, outcome: PushOutcome) -> None:
    state = read_state(repo_root) or {"last_success": None}
    now = datetime.datetime.now().isoformat(timespec="seconds")
    attempt = {"at": now, "status": outcome.status, "detail": outcome.detail}
    images = state.setdefault("images", {})
    if REGISTRY_IMAGE not in images and state.get("last_attempt"):
        images[REGISTRY_IMAGE] = {
            "last_attempt": state["last_attempt"],
            "last_success": state.get("last_success"),
        }
    image_state = images.setdefault(outcome.registry_image, {"last_success": None})
    image_state["last_attempt"] = attempt
    if outcome.status == "pushed":
        image_state["last_success"] = {
            "at": now,
            "tag": outcome.tag,
            "digest": outcome.digest,
        }
    # Preserve the original app-only fields so old tooling can read the marker
    # while new doctor logic evaluates every first-party image independently.
    if outcome.registry_image == REGISTRY_IMAGE:
        state["last_attempt"] = attempt
        if outcome.status == "pushed":
            state["last_success"] = image_state["last_success"]
    _state_path(repo_root).write_bytes(orjson.dumps(state, option=orjson.OPT_INDENT_2))


def _nudge(cred_path: Path) -> str:
    return (
        "off-box rollback baselines are NOT being saved — after a disk cleanup "
        "this host's images would be unrecoverable (it has happened). Fix: mint "
        "a classic PAT with the write:packages scope on YOUR OWN GitHub account "
        "(the account must be a BioMicroCenter org member), then write it to "
        f"{cred_path} as GHCR_USER=<github-username> / GHCR_TOKEN=<token> "
        "(file mode 600). Details: DEPLOYMENT.md §5.2."
    )


def push_baseline(
    repo_root: Path,
    compose_project_name: str,
    today: datetime.date | None = None,
    local_image: str | None = None,
    registry_image: str = REGISTRY_IMAGE,
    git_root: Path | None = None,
    gate_run_limits: tuple[str, ...] = (),
) -> PushOutcome:
    """Gate → tag → login → push → logout. Returns an outcome; NEVER raises."""
    if compose_project_name != CANONICAL_PROJECT:
        # Secondary instances (port-offset test stacks) must not overwrite the
        # org baseline nor the canonical instance's doctor state.
        return PushOutcome(
            status="skipped",
            detail=f"instance '{compose_project_name}' is not the canonical deploy instance",
            registry_image=registry_image,
        )
    cred_path = credential_env_path()
    try:
        local_image = local_image or f"{compose_project_name}-nextseek:latest"
        gate_ok, gate_detail = baked_secret_gate(
            local_image, run_limits=gate_run_limits
        )
        if not gate_ok:
            outcome = PushOutcome(
                status="gate_failed",
                detail=gate_detail,
                remediation=(
                    "do NOT push this image off-box. Clean the build context "
                    "(.dockerignore excludes env files by pattern — see "
                    "test_build_context_env_guard.py), rebuild, and the next "
                    "rebuild will push automatically. DEPLOYMENT.md §5.2."
                ),
                registry_image=registry_image,
            )
            _record(repo_root, outcome)
            return outcome

        creds = load_credentials(cred_path)
        if creds is None:
            outcome = PushOutcome(
                status="no_credentials",
                detail=f"no usable GHCR credential at {cred_path}",
                remediation=_nudge(cred_path),
                registry_image=registry_image,
            )
            _record(repo_root, outcome)
            return outcome

        tag = f"{registry_image}:{compute_baseline_tag(git_root or repo_root, today=today)}"
        tag_result = subprocess.run(
            ["docker", "tag", local_image, tag], capture_output=True, text=True
        )
        if tag_result.returncode != 0:
            outcome = PushOutcome(
                status="push_failed",
                detail=f"docker tag failed: {tag_result.stderr.strip()}",
                remediation=_nudge(cred_path),
                tag=tag,
                registry_image=registry_image,
            )
            _record(repo_root, outcome)
            return outcome

        try:
            login = subprocess.run(
                ["docker", "login", "ghcr.io", "-u", creds.user, "--password-stdin"],
                input=creds.token,
                capture_output=True,
                text=True,
            )
            if login.returncode != 0:
                outcome = PushOutcome(
                    status="push_failed",
                    detail=f"docker login failed: {login.stderr.strip()}",
                    remediation=(
                        "the stored token was rejected — it has likely expired or "
                        "lost org access. " + _nudge(cred_path)
                    ),
                    tag=tag,
                    registry_image=registry_image,
                )
            else:
                push = subprocess.run(
                    ["docker", "push", tag], capture_output=True, text=True
                )
                if push.returncode != 0:
                    outcome = PushOutcome(
                        status="push_failed",
                        detail=f"docker push failed: {push.stderr.strip() or push.stdout.strip()}",
                        remediation=(
                            "if the error mentions permission_denied, the token's "
                            "owner may have lost BioMicroCenter membership or "
                            "package access. " + _nudge(cred_path)
                        ),
                        tag=tag,
                        registry_image=registry_image,
                    )
                else:
                    digest = ""
                    for line in push.stdout.splitlines():
                        if " digest: " in line:
                            digest = line.split(" digest: ")[1].split()[0]
                    outcome = PushOutcome(
                        status="pushed",
                        detail=f"pushed {tag}",
                        tag=tag,
                        digest=digest or None,
                        registry_image=registry_image,
                    )
        finally:
            # Shared box: never leave the credential in ~/.docker/config.json.
            subprocess.run(["docker", "logout", "ghcr.io"], capture_output=True, text=True)

        _record(repo_root, outcome)
        return outcome
    except Exception as exc:  # the deploy is never hostage to this step
        outcome = PushOutcome(
            status="error",
            detail=f"unexpected failure in baseline push step: {exc}",
            remediation=_nudge(cred_path),
            registry_image=registry_image,
        )
        try:
            _record(repo_root, outcome)
        except Exception:
            pass
        return outcome


def push_baselines(
    repo_root: Path,
    compose_project_name: str,
    images: tuple[ImagePolicy, ...],
    git_root: Path | None = None,
) -> tuple[PushOutcome, ...]:
    """Push each rebuilt first-party image without letting registry failures escape."""
    outcomes = []
    for image in images:
        kwargs = {
            "compose_project_name": compose_project_name,
            "local_image": image.local_image,
            "registry_image": image.registry_image,
        }
        if git_root is not None:
            kwargs["git_root"] = git_root
        outcomes.append(push_baseline(repo_root, **kwargs))
    return tuple(outcomes)


def render_outcome(outcome: PushOutcome) -> None:
    """Loud, non-fatal reporting. Success is one quiet ✓; every failure is an
    unmissable banner with a concrete fix."""
    if outcome.status == "pushed":
        ui.ok(f"off-box rollback baseline pushed: {outcome.tag}")
        return
    if outcome.status == "skipped":
        ui.info(f"off-box baseline push skipped ({outcome.detail})")
        return
    ui.banner("⚠ OFF-BOX ROLLBACK BASELINE NOT PUSHED — ACTION NEEDED ⚠")
    ui.fail(outcome.detail)
    if outcome.remediation:
        ui.remediation(outcome.remediation)
    ui.warn("the deploy itself is unaffected; 'startup doctor' will keep flagging this")


def check_registry_baseline(
    repo_root: Path,
    *,
    required_registry_images: set[str] | None = None,
) -> tuple[str, bool, str]:
    """Doctor check for the requested deployment cohort's protected images.

    A full-stack doctor keeps the historical all-first-party requirement.  An
    explicitly app-scoped doctor may request only the app package; this is used
    by the hardware-bounded disposable deploy rehearsal and never weakens the
    default operator check.
    """
    state = read_state(repo_root)
    if state is None:
        return (
            "off-box baseline",
            False,
            "never pushed from this host — images are unrecoverable after a "
            "disk cleanup until a rebuild pushes to GHCR (DEPLOYMENT.md §5.2)",
        )
    image_states = state.get("images")
    if image_states:
        required = required_registry_images or {
            image.registry_image for image in registry_images()
        }
        missing = sorted(required - set(image_states))
        failed = []
        for image_name in sorted(required & set(image_states)):
            image_state = image_states[image_name]
            attempt = image_state.get("last_attempt") or {}
            if attempt.get("status") != "pushed":
                failed.append(
                    f"{image_name}: {attempt.get('status', '?')} ({attempt.get('detail', '')})"
                )
        if missing or failed:
            parts = []
            if missing:
                parts.append("never pushed: " + ", ".join(missing))
            if failed:
                parts.append("failed: " + "; ".join(failed))
            return "off-box baseline", False, " | ".join(parts)
        successes = [
            image_states[image_name].get("last_success", {}).get("at", "?")
            for image_name in required
        ]
        return (
            "off-box baseline",
            True,
            f"all {len(required)} first-party images protected; latest push {max(successes)}",
        )

    # Backward-compatible read of the original app-only marker. The first
    # rebuild under the new CLI writes `images` and activates full coverage.
    attempt = state.get("last_attempt") or {}
    if attempt.get("status") != "pushed":
        return (
            "off-box baseline",
            False,
            f"last attempt {attempt.get('at', '?')} -> {attempt.get('status', '?')}: "
            f"{attempt.get('detail', '')}",
        )
    success = state.get("last_success") or {}
    return (
        "off-box baseline",
        True,
        f"last push {success.get('tag', '?')} at {success.get('at', '?')}",
    )
