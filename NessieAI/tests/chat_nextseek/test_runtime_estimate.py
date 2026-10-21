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
    monkeypatch.setattr(at, "lookup_run_sizes", lambda accs: (
        seen.update(looked_up=list(accs)), {"SRR1": {"fastq_bytes": 5 * 10 ** 8}})[1])
    monkeypatch.setattr(at, "runtime_estimate",
                        lambda key, n_samples, n_fetch=0, fetch_sizes=None: (
                            seen.update(key=key, n=n_samples, f=n_fetch, sizes=fetch_sizes),
                            {"text": "about 1–2 hours"})[1])

    class _Cfg:
        TOWER_ENV_COMPLETE = False
        LURIA_ENV_COMPLETE = True

    state = {"artifacts": {"samplesheet": str(sheet), "base_dir": str(tmp_path)}, "bundle_key": "GRCh38"}
    out = json.loads(at.tool_configure_run(_Cfg(), state, {"pipeline_key": "rnaseq", "params": {}},
                                           str(tmp_path)))
    assert out["ok"] is True, out
    assert out["runtime_estimate"] == {"text": "about 1–2 hours"}
    # ENA is asked only about the row still to fetch, and its answer reaches the estimate
    assert seen == {"looked_up": ["SRR1"], "key": "rnaseq", "n": 2, "f": 1,
                    "sizes": {"SRR1": {"fastq_bytes": 5 * 10 ** 8}}}


def test_the_prompt_tells_the_agent_to_state_the_estimate():
    from pathlib import Path

    import chat_nextseek

    prompt = (Path(chat_nextseek.__file__).parent / "prompts" / "pipeline_agent.txt").read_text()
    assert "runtime_estimate" in prompt


# --- SRA download size (the fetchngs pre-stage) ---------------------------------------------
# A hlatyping launch of six human RNA-seq runs (SRR21023814-19, 16.5-21.1 GB of FASTQ each,
# about 107 GB, 390-500 M read pairs per run; SLURM 11426306, 2026-10-20) was offered as
# "about 1.5–4.5 hours": hlatyping's curated block plus 17-70 minutes of small-run download.
# fetchngs then spent 4 h and fetched none of them.

_GB = 10 ** 9
_HLA_RUNS = {f"SRR2102381{i}": {"fastq_bytes": int(gb * _GB), "read_count": reads, "paired": True}
             for i, (gb, reads) in enumerate(
                 [(16.5, 390_000_000), (17.0, 400_000_000), (17.5, 410_000_000),
                  (18.0, 430_000_000), (21.1, 500_000_000), (17.0, 405_000_000)], start=4)}
_HLATYPING = {"runtime": _block(setup_minutes=[0, 5], setup_label="", base_minutes=[5, 15],
                                per_sample_minutes=[10, 30])}


def test_the_failed_hlatyping_offer_is_reproduced_without_sizes(monkeypatch):
    monkeypatch.setattr(pp, "_load_pipeline_doc", lambda key: _HLATYPING)
    est = pp.runtime_estimate("hlatyping", n_samples=6, n_fetch=6)
    assert est["text"] == "about 1.5–4.5 hours"
    # with no size from ENA the download is still timed as small runs, and the user is told
    assert est["sra_download"]["timed"] is True
    assert "no size for 6 of the 6" in est["warning"]
    assert "timed as small runs" in est["warning"]


def test_large_sra_runs_are_left_out_of_the_range_and_named_by_size(monkeypatch):
    monkeypatch.setattr(pp, "_load_pipeline_doc", lambda key: _HLATYPING)
    sized = pp.runtime_estimate("hlatyping", n_samples=6, n_fetch=6, fetch_sizes=_HLA_RUNS)
    no_fetch = pp.runtime_estimate("hlatyping", n_samples=6)
    # the small-run download minutes are not claimed; no other figure replaces them
    assert (sized["low_minutes"], sized["high_minutes"]) == \
        (no_fetch["low_minutes"], no_fetch["high_minutes"])
    assert sized["text"] == ("about 1–3.5 hours, plus downloading 107.1 GB from SRA, "
                             "which it does not time")
    assert sized["excludes"].startswith("the SRA download")
    assert sized["sra_download"] == {"runs": 6, "sized": 6, "fastq_bytes": sum(r["fastq_bytes"] for r in _HLA_RUNS.values()),
                                     "timed": False}
    warning = sized["warning"]
    assert "SRR21023818: 21.1 GB, 500 M read pairs" in warning
    assert "6 of the 6 runs hold more than 1.0 GB of FASTQ" in warning
    assert "not measured" in warning
    assert any("107.1 GB of FASTQ; not timed" in item for item in sized["includes"])


def test_small_sized_runs_keep_the_download_in_the_range_without_a_warning(monkeypatch):
    monkeypatch.setattr(pp, "_load_pipeline_doc", lambda key: {"runtime": _block()})
    small = {"SRR1": {"fastq_bytes": 150_000_000, "read_count": 1_000_000, "paired": True},
             "SRR2": {"fastq_bytes": 140_000_000, "read_count": 950_000, "paired": True}}
    plain = pp.runtime_estimate("rnaseq", n_samples=2, n_fetch=2)
    sized = pp.runtime_estimate("rnaseq", n_samples=2, n_fetch=2, fetch_sizes=small)
    assert (sized["low_minutes"], sized["high_minutes"]) == \
        (plain["low_minutes"], plain["high_minutes"])
    assert "warning" not in sized
    assert sized["sra_download"]["timed"] is True
    assert any("0.3 GB of FASTQ" in item for item in sized["includes"])


def test_a_large_run_beside_an_unsized_one_says_at_least(monkeypatch):
    monkeypatch.setattr(pp, "_load_pipeline_doc", lambda key: {"runtime": _block()})
    est = pp.runtime_estimate("rnaseq", n_samples=2, n_fetch=2,
                              fetch_sizes={"SRR1": {"fastq_bytes": 20 * _GB}})
    assert est["text"].endswith("plus downloading at least 20.0 GB from SRA, which it does not time")
    assert "for 1 of them, the rest of unknown size" in est["warning"]
    assert "1 of the 2 runs holds more than" in est["warning"]
    assert "(largest SRR1: 20.0 GB)" in est["warning"]   # no read count from ENA: none is invented


def test_no_fetch_means_no_download_entry(monkeypatch):
    monkeypatch.setattr(pp, "_load_pipeline_doc", lambda key: {"runtime": _block()})
    est = pp.runtime_estimate("rnaseq", n_samples=2, fetch_sizes=_HLA_RUNS)
    assert "sra_download" not in est and "warning" not in est


# --- ENA size lookup --------------------------------------------------------------------------

def _fake_ena(monkeypatch, answers):
    from chat_nextseek.seqera import ena
    calls = []

    def fake_get(url, **kw):
        calls.append((url, kw))
        acc = next(a for a in answers if f"accession={a}&" in url)
        answer = answers[acc]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(ena, "_http_get_json", fake_get)
    monkeypatch.setattr(ena, "_RUN_SIZE_CACHE", {})
    return ena, calls


def test_lookup_sums_each_runs_fastq_files_and_reads(monkeypatch):
    ena, calls = _fake_ena(monkeypatch, {
        "SRR21023818": [{"run_accession": "SRR21023818", "fastq_bytes": "10500000000;10600000000",
                         "read_count": "500000000", "library_layout": "PAIRED"}],
        # an experiment accession covering two single-end runs
        "SRX1": [{"run_accession": "SRR8", "fastq_bytes": "100", "read_count": "10",
                  "library_layout": "SINGLE"},
                 {"run_accession": "SRR9", "fastq_bytes": "200", "read_count": "20",
                  "library_layout": "SINGLE"}],
    })
    sizes = ena.lookup_run_sizes(["SRR21023818", "SRX1", "SRR21023818"])
    assert sizes == {
        "SRR21023818": {"fastq_bytes": 21_100_000_000, "read_count": 500_000_000, "paired": True},
        "SRX1": {"fastq_bytes": 300, "read_count": 30, "paired": False},
    }
    assert len(calls) == 2                                    # deduplicated
    assert all(kw.get("retries") == 0 for _, kw in calls)     # one try each
    assert "fastq_bytes" in calls[0][0]


def test_lookup_leaves_out_what_ena_cannot_size(monkeypatch):
    ena, _ = _fake_ena(monkeypatch, {
        "SRR1": OSError("no egress"),
        "SRR2": [],
        "SRR3": [{"run_accession": "SRR3", "fastq_bytes": "", "read_count": "5"}],
        "SRR4": [{"run_accession": "SRR4", "fastq_bytes": "123", "read_count": ""}],
    })
    assert ena.lookup_run_sizes(["SRR1", "SRR2", "SRR3", "SRR4"]) == {
        "SRR4": {"fastq_bytes": 123, "read_count": None, "paired": False}}


def test_lookup_stops_asking_once_its_budget_is_spent(monkeypatch):
    ena, calls = _fake_ena(monkeypatch, {"SRR1": [], "SRR2": []})
    assert ena.lookup_run_sizes(["SRR1", "SRR2"], budget_seconds=0) == {}
    assert calls == []


def test_lookup_answers_from_its_cache_without_asking_again(monkeypatch):
    ena, calls = _fake_ena(monkeypatch, {
        "SRR4": [{"run_accession": "SRR4", "fastq_bytes": "123", "read_count": "7"}]})
    first = ena.lookup_run_sizes(["SRR4"])
    assert ena.lookup_run_sizes(["SRR4"], budget_seconds=0) == first
    assert len(calls) == 1
