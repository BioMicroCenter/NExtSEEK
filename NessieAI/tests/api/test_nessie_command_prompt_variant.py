"""`manage.py nessie --prompt-variant` and `--arms auto`, with run_suite and run_arms patched: no turn is sent."""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from chat_nextseek.prompt_variants import VARIANT_NAMES
from NessieAI.tests.nessie_tests import runner
from NessieAI.tests.nessie_tests.manifest import NessieManifest
from nextseek_api.management.commands.nessie import Command


def _manifest(**kw):
    return NessieManifest(started_at="a", ended_at="b", tier=kw.get("tier", "full"),
                          scope=kw.get("scope", "s"), entries=[])


@pytest.fixture
def suite(monkeypatch):
    captured = {}

    def fake(**kw):
        captured.update(kw)
        return _manifest(tier=kw["tier"], scope=kw["scope"])
    monkeypatch.setattr(runner, "run_suite", fake)
    return captured


@pytest.fixture
def arms(monkeypatch):
    captured = {}

    def fake(**kw):
        captured.update(kw)
        return {"run_meta": {"prompt_variant": kw.get("prompt_variant")}, "questions": [],
                "progress": {"state": "complete", "turns_driven": 0},
                "manifests": {a: _manifest(scope=f"arm:{a}") for a in kw["arms"]},
                "arms_file": str(Path(kw["out_dir"]) / "arms.json")}
    monkeypatch.setattr(runner, "run_arms", fake)
    return captured


def _call(*args):
    out = io.StringIO()
    call_command("nessie", *args, stdout=out, stderr=io.StringIO())
    return out.getvalue()


def _arms_args(tmp_path, *extra):
    return ["--tier", "full", "--cases", str(tmp_path / "cases.json"), "--force-route", "ns",
            "--out", str(tmp_path / "run"), *extra]


def test_the_choices_are_the_engines_and_the_harness_variants():
    parser = Command().create_parser("manage.py", "nessie")
    action = next(a for a in parser._actions if a.dest == "prompt_variant")
    assert tuple(action.choices) == VARIANT_NAMES == runner.PROMPT_VARIANTS


def test_no_variant_by_default(suite, tmp_path):
    _call("--tier", "route", "--out", str(tmp_path))
    assert suite["prompt_variant"] is None


def test_a_variant_reaches_run_suite_without_a_parser_force(suite, tmp_path):
    _call("--tier", "full", "--force-route", "ns", "--prompt-variant", "v2_apoc", "--out", str(tmp_path))
    assert suite["prompt_variant"] == "v2_apoc" and suite["force_parser_mode"] is None


@pytest.mark.parametrize("extra", [[], ["--force-route", "cc"]])
def test_a_variant_needs_the_ns_route(extra, suite, tmp_path):
    with pytest.raises(CommandError, match="--prompt-variant needs --force-route ns"):
        _call("--tier", "full", "--prompt-variant", "v2_apoc", *extra, "--out", str(tmp_path))
    assert suite == {}


def test_an_unknown_variant_is_refused(suite, tmp_path):
    with pytest.raises(CommandError):
        _call("--tier", "full", "--force-route", "ns", "--prompt-variant", "rewrite", "--out", str(tmp_path))
    assert suite == {}


def test_the_unforced_arm_runs_with_a_variant(arms, tmp_path):
    out = _call(*_arms_args(tmp_path, "--arms", "auto", "--prompt-variant", "v2_apoc"))
    assert arms["arms"] == ["auto"] and arms["prompt_variant"] == "v2_apoc"
    assert "prompt variant: v2_apoc" in out


def test_the_unforced_arm_runs_the_default_prompts(arms, tmp_path):
    out = _call(*_arms_args(tmp_path, "--arms", "auto"))
    assert arms["arms"] == ["auto"] and arms["prompt_variant"] is None
    assert "prompt variant: none (the default prompts)" in out


def test_the_forced_arms_take_a_variant(arms, tmp_path):
    _call(*_arms_args(tmp_path, "--arms", "graph,api", "--prompt-variant", "v2_apoc"))
    assert arms["arms"] == ["graph", "api"] and arms["prompt_variant"] == "v2_apoc"
