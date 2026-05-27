"""CLI entrypoint for the native evaluator.

Bootstraps the BAML client before dispatching to :func:`runner.main`
so a fresh clone can run without a manual ``baml-cli generate`` step.
Attribute-form call on ``bootstrap`` (not ``from .bootstrap import _bootstrap_baml_client``)
so that a ``runpy``-driven test can monkeypatch the module attribute and have
the change visible to this entrypoint.
"""
from __future__ import annotations

from . import bootstrap
from .runner import main


bootstrap._bootstrap_baml_client()
raise SystemExit(main())
