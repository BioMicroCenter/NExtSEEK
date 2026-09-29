"""The CC agent image can draw a chart (D6 of the 2026-09-25 dev run).

Without a plotting library every chart turn of that run hand-encoded a PNG with
numpy and zlib or wrote an SVG by hand, and one turn tried ``pip install
matplotlib`` twice before spending 96 s and 11 model turns on a 3-bar chart. The
image installs the ``container`` extra (``uv sync --locked --extra container`` in
the Dockerfile), so matplotlib belongs there, and ``--locked`` fails the build if
the lock does not carry it.
"""
from __future__ import annotations

import re
import tomllib

from NessieAI import paths

CC_RUNTIME = paths.CC_RUNTIME_DIR


def _container_extra() -> list[str]:
    data = tomllib.loads((CC_RUNTIME / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["optional-dependencies"]["container"]


def test_matplotlib_is_in_the_image_only_container_extra():
    names = [re.split(r"[<>=~!\[; ]", spec, maxsplit=1)[0].lower() for spec in _container_extra()]
    assert "matplotlib" in names


def test_matplotlib_is_not_a_host_dependency():
    """The host bridge venv stays lean: the chart library is for the image only."""
    data = tomllib.loads((CC_RUNTIME / "pyproject.toml").read_text(encoding="utf-8"))
    host = [spec.lower() for spec in data["project"]["dependencies"]]
    assert not any(spec.startswith("matplotlib") for spec in host)


def test_the_lock_carries_matplotlib_and_pillow():
    lock = (CC_RUNTIME / "uv.lock").read_text(encoding="utf-8")
    assert re.search(r'^name = "matplotlib"$', lock, re.M)
    assert re.search(r'^name = "pillow"$', lock, re.M)


def test_the_dockerfile_installs_the_container_extra():
    dockerfile = (CC_RUNTIME / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"uv sync --locked[^\n]*--extra container", dockerfile)
