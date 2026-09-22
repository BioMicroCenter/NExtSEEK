"""The cc-agent image must bake the canonical context files the checkout holds.

The six chat_nextseek context files are baked into both the app image and the
cc-agent image. ``./startup.sh rebuild`` builds only the first, so an edit to one
of them followed by an app rebuild alone leaves the CC agent serving its old
copy, indefinitely, while every checkout-level guard stays green: they compare
files in the tree, never the tree against the image. ``check_cc_agent_context``
reads the six files out of the built image and compares bytes.

Hermetic: docker is never asked. ``image_exists`` and ``copy_from_image`` are
replaced, and the fake copy lays down whatever bytes the test says the image
baked.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from startup.lib import layout
from startup.steps import validate

IMAGE = "dmac-assistant:poc"


def _checkout(tmp_path: Path, files: dict[str, bytes] | None = None) -> Path:
    """A checkout holding the six canonical files, overridden or dropped by `files`."""
    repo = tmp_path / "checkout"
    context = repo / layout.CANONICAL_CONTEXT_DIR
    context.mkdir(parents=True)
    wanted = {name: f"checkout {name}\n".encode() for name in layout.CANONICAL_CONTEXT_FILES}
    wanted.update(files or {})
    for name, data in wanted.items():
        if data is not None:
            (context / name).write_bytes(data)
    return repo


def _image(monkeypatch, baked: dict[str, bytes | None] | None = None, *,
           present: bool = True) -> list[tuple]:
    """An image baking the checkout's default bytes, overridden or dropped by `baked`.

    Returns the copy calls, so a test can see what was read and from where.
    """
    files = {name: f"checkout {name}\n".encode() for name in layout.CANONICAL_CONTEXT_FILES}
    # The plugin tree's own files sit in the same directory and are not compared.
    files["ops.json"] = b"{}\n"
    files.update(baked or {})
    calls: list[tuple] = []

    def fake_copy(image, src, dest):
        calls.append((image, src, dest))
        dest = Path(dest)
        dest.mkdir(parents=True)
        for name, data in files.items():
            if data is not None:
                (dest / name).write_bytes(data)

    monkeypatch.setattr(validate, "image_exists", lambda name: present)
    monkeypatch.setattr(validate, "copy_from_image", fake_copy)
    return calls


def test_passes_when_the_image_bakes_the_checkouts_bytes(monkeypatch, tmp_path):
    _image(monkeypatch)

    result = validate.check_cc_agent_context(_checkout(tmp_path))

    assert result.ok is True and result.warn is False
    assert result.name == "cc-agent context"
    assert IMAGE in result.detail


def test_names_every_file_the_image_bakes_differently(monkeypatch, tmp_path):
    """The failure this exists for: an edited file and a skipped cc-agent rebuild."""
    _image(monkeypatch, {"capabilities.md": b"old capabilities\n",
                         "projects_db.json": b"[]\n"})

    result = validate.check_cc_agent_context(_checkout(tmp_path))

    assert result.ok is False
    assert "capabilities.md" in result.detail
    assert "projects_db.json" in result.detail
    # The four that match are not reported as problems.
    assert "min_assays_db.json" not in result.detail
    assert "2 of 6" in result.detail
    assert "./startup.sh rebuild --component cc-agent" in result.detail


def test_compares_bytes_not_text(monkeypatch, tmp_path):
    """Line endings are bytes the agent reads; a CRLF copy is a different file."""
    _image(monkeypatch, {"min_api_endpoints.json": b"checkout min_api_endpoints.json\r\n"})

    result = validate.check_cc_agent_context(_checkout(tmp_path))

    assert result.ok is False
    assert "min_api_endpoints.json" in result.detail


def test_names_a_file_the_image_does_not_have(monkeypatch, tmp_path):
    _image(monkeypatch, {"min_sampletypes_db.json": None})

    result = validate.check_cc_agent_context(_checkout(tmp_path))

    assert result.ok is False
    assert "min_sampletypes_db.json (absent from the image)" in result.detail


def test_names_a_file_the_checkout_does_not_have(monkeypatch, tmp_path):
    _image(monkeypatch)

    result = validate.check_cc_agent_context(
        _checkout(tmp_path, {"min_api_endpoints_enriched.json": None})
    )

    assert result.ok is False
    assert "min_api_endpoints_enriched.json (absent from the checkout)" in result.detail


def test_reads_the_cc_agent_image_at_the_path_the_dockerfile_fills(monkeypatch, tmp_path):
    asked = []
    calls = _image(monkeypatch)
    monkeypatch.setattr(validate, "image_exists", lambda name: asked.append(name) or True)

    validate.check_cc_agent_context(_checkout(tmp_path))

    assert asked == [IMAGE]
    assert [(image, src) for image, src, _ in calls] == [(IMAGE, layout.CC_AGENT_CONTEXT_DIR)]


def test_skips_with_a_warning_when_the_image_is_absent(monkeypatch, tmp_path):
    """First-party images already fails for an absent image; this says why it
    compared nothing rather than failing a second time for the same cause."""
    calls = _image(monkeypatch, present=False)

    result = validate.check_cc_agent_context(_checkout(tmp_path))

    assert result.ok is True and result.warn is True
    assert result.detail.startswith("skipped:")
    assert IMAGE in result.detail
    assert calls == []


def test_reports_a_daemon_outage(monkeypatch, tmp_path):
    def explode(name):
        raise validate.DockerOpsError("docker image ls failed (exit 1): daemon unreachable")

    calls = _image(monkeypatch)
    monkeypatch.setattr(validate, "image_exists", explode)

    result = validate.check_cc_agent_context(_checkout(tmp_path))

    assert result.ok is False
    assert "daemon unreachable" in result.detail
    assert calls == []


@pytest.mark.parametrize("error", [
    validate.DockerOpsError("docker cp dmac-assistant:poc:/app/... failed (exit 1): no such path"),
    FileNotFoundError(2, "No such file or directory", "docker"),
])
def test_fails_when_the_image_cannot_be_read(monkeypatch, tmp_path, error):
    _image(monkeypatch)

    def broken(image, src, dest):
        raise error

    monkeypatch.setattr(validate, "copy_from_image", broken)

    result = validate.check_cc_agent_context(_checkout(tmp_path))

    assert result.ok is False
    assert layout.CC_AGENT_CONTEXT_DIR in result.detail
