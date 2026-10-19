"""How long a run will take, told to the user before they confirm it.

The numbers come from a curated ``runtime`` block in each pipeline's JSON
(reports/templates/nfcore/<key>.json): fixed setup (building the genome index),
a base for the per-run steps, and a per-sample range. configure_run scales it
by the samplesheet and returns it; the prompt makes the agent say it.
"""
from __future__ import annotations

import json

import pytest

from chat_nextseek.seqera import pipeline_params as pp

ATLAS_LAUNCHABLE = ("rnaseq", "scrnaseq", "smrnaseq", "hlatyping", "rnasplice",
                    "rnavar", "riboseq", "denovotranscript")


def _block(**over):
    block = {"basis": "rough", "setup_minutes": [45, 90], "setup_label": "building the genome index",
             "base_minutes": [20, 40], "per_sample_minutes": [10, 25]}
    block.update(over)
    return block


def test_a_two_sample_run_adds_setup_base_and_each_sample(monkeypatch):
    monkeypatch.setattr(pp, "_load_pipeline_doc", lambda key: {"runtime": _block()})
    est = pp.runtime_estimate("rnaseq", n_samples=2)
    assert (est["low_minutes"], est["high_minutes"]) == (45 + 20 + 2 * 10, 90 + 40 + 2 * 25)
    assert est["text"] == "about 1.5–3 hours"
    assert any("genome index" in item for item in est["includes"])
    assert "queue" in est["excludes"]


def test_a_short_run_is_said_in_minutes(monkeypatch):
    monkeypatch.setattr(pp, "_load_pipeline_doc", lambda key: {"runtime": _block(
        setup_minutes=[0, 5], setup_label="", base_minutes=[10, 20], per_sample_minutes=[5, 10])})
    est = pp.runtime_estimate("smrnaseq", n_samples=2)
    assert est["text"] == "about 20–45 minutes"
    assert not any("index" in item for item in est["includes"])


def test_samples_fetched_from_sra_add_download_time(monkeypatch):
    monkeypatch.setattr(pp, "_load_pipeline_doc", lambda key: {"runtime": _block()})
    plain = pp.runtime_estimate("rnaseq", n_samples=2)
    fetched = pp.runtime_estimate("rnaseq", n_samples=2, n_fetch=2)
    assert fetched["low_minutes"] > plain["low_minutes"]
    assert fetched["high_minutes"] > plain["high_minutes"]
    assert any("SRA" in item for item in fetched["includes"])


def test_a_pipeline_with_no_curated_runtime_has_no_estimate(monkeypatch):
    monkeypatch.setattr(pp, "_load_pipeline_doc", lambda key: {})
    assert pp.runtime_estimate("fetchngs", n_samples=3) is None


@pytest.mark.parametrize("key", ATLAS_LAUNCHABLE)
def test_every_launchable_rna_pipeline_has_a_well_formed_runtime_block(key):
    pp._load_pipeline_doc.cache_clear()
    block = pp._load_pipeline_doc(key).get("runtime")
    assert block, f"{key}: no runtime block"
    assert block["basis"]
    for field in ("setup_minutes", "base_minutes", "per_sample_minutes"):
        low, high = block[field]
        assert 0 <= low <= high, f"{key}.{field}"
    est = pp.runtime_estimate(key, n_samples=1)
    assert est["text"].startswith("about ")


def test_configure_run_returns_the_estimate(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from chat_nextseek.pipeline import agent_tools as at

    sheet = tmp_path / "s.csv"
    sheet.write_text("sample,fastq_1,fastq_2,strandedness,accession\n"
                     "A,,,auto,SRR1\n"
                     "B,/net/x/b_1.fq.gz,/net/x/b_2.fq.gz,auto,\n")
    monkeypatch.setattr(at, "emit_luria_launch_artifacts", lambda out_dir, **kw: SimpleNamespace(
        saved_files={"params": f"{out_dir}/params.yml", "launch": f"{out_dir}/launch.yml"},
        launch_entry={"name": "r1"}, fetchngs_launch_entry=None, excluded_accessions=[]))
    seen = {}
    monkeypatch.setattr(at, "runtime_estimate",
                        lambda key, n_samples, n_fetch=0: (seen.update(key=key, n=n_samples, f=n_fetch),
                                                           {"text": "about 1–2 hours"})[1])

    class _Cfg:
        TOWER_ENV_COMPLETE = False
        LURIA_ENV_COMPLETE = True

    state = {"artifacts": {"samplesheet": str(sheet), "base_dir": str(tmp_path)}, "bundle_key": "GRCh38"}
    out = json.loads(at.tool_configure_run(_Cfg(), state, {"pipeline_key": "rnaseq", "params": {}},
                                           str(tmp_path)))
    assert out["ok"] is True, out
    assert out["runtime_estimate"] == {"text": "about 1–2 hours"}
    assert seen == {"key": "rnaseq", "n": 2, "f": 1}


def test_the_prompt_tells_the_agent_to_state_the_estimate():
    from pathlib import Path

    import chat_nextseek

    prompt = (Path(chat_nextseek.__file__).parent / "prompts" / "pipeline_agent.txt").read_text()
    assert "runtime_estimate" in prompt
