"""Emit and validate Dockerfile plugin COPY/PATH and Compose named-context blocks."""
from __future__ import annotations

import re
from pathlib import Path

from build_tools.gen_op_surfaces.constants import (
    CANONICAL_CAPABILITIES_IN_CONTEXT,
    IMAGE_CAPABILITIES_PATH,
    NAMED_CAPABILITIES_CONTEXT,
)
from nextseek_api.cc_assistant.op_registry.install_oracle import (
    PLUGIN_COPY_RE,
    PLUGIN_PATH_RE,
    discover_install,
    manifest_plugin_dirs,
)

_PLUGINS_ROOT_REL = Path("docker/cc-runtime/build_context/plugins")
_COPY_RE = re.compile(
    r"^COPY\s+(?P<flags>(?:--\S+\s+)*)(?P<src>\S+)\s+(?P<dest>\S+)\s*$"
)


class CanonicalCapabilitiesError(ValueError):
    """Raised when the in-image capabilities path is not finally written by canonical source."""


class ComposeContextError(ValueError):
    """Raised when the Compose named build context is missing or unsafe."""


def _plugins_root(repo_root: Path) -> Path:
    return repo_root / _PLUGINS_ROOT_REL


def emit_plugin_copy_block(repo_root: Path) -> str:
    """Render one exact per-plugin COPY line for every manifest-bearing plugin."""
    names = manifest_plugin_dirs(_plugins_root(repo_root))
    if not names:
        return ""
    return "".join(
        f"COPY build_context/plugins/{name}/ /app/plugins/{name}/\n" for name in names
    )


def emit_plugin_path_block(repo_root: Path) -> str:
    """Render one PATH entry per plugin, preserving literal ${PATH}."""
    names = manifest_plugin_dirs(_plugins_root(repo_root))
    if not names:
        return ""
    joined = ":".join(f"/app/plugins/{name}/bin" for name in names)
    return f'ENV PATH="{joined}:${{PATH}}"\n'


def emit_capabilities_copy_block(_repo_root: Path) -> str:
    """Copy canonical capabilities.md from the named chat_nextseek context."""
    return (
        f"COPY --from={NAMED_CAPABILITIES_CONTEXT} "
        f"{CANONICAL_CAPABILITIES_IN_CONTEXT} "
        f"{IMAGE_CAPABILITIES_PATH}\n"
    )


def emit_additional_contexts_block(_repo_root: Path) -> str:
    """Declare the vendored chat_nextseek named additional build context."""
    return (
        "      additional_contexts:\n"
        f"        {NAMED_CAPABILITIES_CONTEXT}: ./{NAMED_CAPABILITIES_CONTEXT}\n"
    )


def parse_plugin_copy_names(text: str) -> set[str]:
    names: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        match = PLUGIN_COPY_RE.match(stripped)
        if match:
            names.add(match.group("plugin"))
    return names


def parse_plugin_path_names(text: str) -> set[str]:
    names: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped.startswith("ENV PATH="):
            continue
        names.update(PLUGIN_PATH_RE.findall(stripped))
    return names


def four_install_sets(
    *,
    plugins_root: Path,
    dockerfile_path: Path,
) -> tuple[set[str], set[str], set[str], set[str]]:
    """Return (dir discovery, COPY names, oracle installed, PATH names)."""
    discovery = discover_install(
        plugins_root=plugins_root,
        dockerfile_path=dockerfile_path,
    )
    dirs = set(manifest_plugin_dirs(plugins_root))
    copies = {dest.plugin_name for dest in discovery.copy_destinations}
    installed = set(discovery.plugins)
    paths = {entry.plugin_name for entry in discovery.path_entries}
    return dirs, copies, installed, paths


def _from_context(flags: str) -> str | None:
    for token in flags.split():
        if token.startswith("--from="):
            return token[len("--from=") :]
    return None


def _writes_capabilities(dest: str) -> bool:
    if dest.rstrip("/") == IMAGE_CAPABILITIES_PATH.rstrip("/"):
        return True
    if dest.endswith("/") and IMAGE_CAPABILITIES_PATH.startswith(dest):
        return True
    return dest.rstrip("/") == "/app/plugins/nextseek"


def _is_canonical_writer(from_name: str | None, src: str, dest: str) -> bool:
    return (
        from_name == NAMED_CAPABILITIES_CONTEXT
        and src == CANONICAL_CAPABILITIES_IN_CONTEXT
        and dest == IMAGE_CAPABILITIES_PATH
    )


def validate_canonical_capabilities_final_writer(dockerfile_text: str) -> None:
    """Require the named-context COPY to be the last writer of in-image capabilities."""
    writers: list[tuple[str | None, str, str, bool]] = []
    for line in dockerfile_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _COPY_RE.match(stripped)
        if not match:
            continue
        src = match.group("src")
        dest = match.group("dest")
        from_name = _from_context(match.group("flags"))
        if _writes_capabilities(dest):
            writers.append(
                (from_name, src, dest, _is_canonical_writer(from_name, src, dest))
            )
    if not any(canonical for *_, canonical in writers):
        raise CanonicalCapabilitiesError(
            "missing named context COPY of canonical capabilities.md from chat_nextseek"
        )
    last_from, last_src, last_dest, last_canonical = writers[-1]
    if not last_canonical:
        raise CanonicalCapabilitiesError(
            "later overwrite of in-image capabilities path after canonical COPY: "
            f"{last_from} {last_src} {last_dest}"
        )


def validate_compose_named_context(
    *,
    repo_root: Path,
    contexts: dict[str, str],
) -> None:
    """Require chat_nextseek to resolve to the vendored tree inside repo_root."""
    if NAMED_CAPABILITIES_CONTEXT not in contexts:
        raise ComposeContextError("missing named context chat_nextseek")
    raw = contexts[NAMED_CAPABILITIES_CONTEXT]
    if not isinstance(raw, str) or not raw.strip():
        raise ComposeContextError("named context chat_nextseek source is empty")
    path = Path(raw)
    if path.is_absolute() or raw.startswith("/") or raw.startswith("~"):
        raise ComposeContextError(
            f"absolute or external named context is forbidden: {raw}"
        )
    if ".." in path.parts:
        raise ComposeContextError(
            f"named context traversal is forbidden: {raw}"
        )
    expected = (repo_root / NAMED_CAPABILITIES_CONTEXT).resolve()
    resolved = (repo_root / raw).resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise ComposeContextError(
            f"named context resolves outside repository: {raw}"
        ) from exc
    if resolved != expected:
        raise ComposeContextError(
            "named context does not resolve to vendored chat_nextseek tree: "
            f"{raw} -> {resolved}"
        )
    if not resolved.is_dir():
        raise ComposeContextError(
            f"named context path is not a directory: {resolved}"
        )
