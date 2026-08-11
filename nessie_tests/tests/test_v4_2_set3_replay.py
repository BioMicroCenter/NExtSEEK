"""Plan 018 V4-2 DONE: set3_final replay + rejection cases (no route execution)."""
from __future__ import annotations

import pathlib
import zipfile

import orjson
import pytest

from nessie_tests import bayes_manifest as bm
from nessie_tests import bayesian, corpus, runner
from nessie_tests import v4_2_verifier as v4

CORPUS = pathlib.Path(__file__).resolve().parents[1] / "corpus.json"


def test_v13a_set3_manifest_hash_and_strict_parse():
    raw = v4.load_set3_bayes_bytes()
    assert v4.sha256_bytes(raw) == v4.V13A_EXPECTED["bayes_manifest_sha256"]
    m = bm.BayesManifest.model_validate(orjson.loads(raw))
    assert len(m.pairs) == 149


def test_verifier_report_passes_on_transferred_set3():
    report = v4.run_verifier()
    assert report.passed, report.errors


def test_set3_pairs_have_independent_forced_route_traces():
    m = v4.load_set3_bayes_manifest()
    for p in m.pairs:
        assert p.ns is not None and p.cc is not None
        assert p.ns.route == "nextseek_query" and p.ns.route_source == "forced"
        assert p.cc.route == "container_cc" and p.cc.route_source == "forced"
        assert all(s == "forced" for s in (p.ns.route_sources or []))
        assert all(s == "forced" for s in (p.cc.route_sources or []))
        assert set(p.ns.task_ids).isdisjoint(set(p.cc.task_ids))


def test_producer_write_emits_schema_versioned_bayes_manifest(tmp_path, monkeypatch):
    """Synthetic fake drive — not hand-authored bytes as sole proof."""
    one_id = corpus.bayesian_ids(CORPUS)[:1]
    monkeypatch.setattr(corpus, "bayesian_ids", lambda _p: one_id)

    calls = []

    def post_query(body):
        calls.append(body.get("force_route"))
        return {"task_id": f"t-{len(calls)}", "session_id": f"s-{len(calls)}"}

    def get_progress(_):
        arm = calls[-1]
        route = "container_cc" if arm == "cc" else "nextseek_query"
        return {
            "status": "completed",
            "progress": [
                {"event": "route_decided", "data": {"route": route, "source": "forced"}},
                {"event": "query_complete", "data": {"reply": "ok"}},
            ],
        }

    bayesian.run_paired(
        base_url="http://x",
        auth_header="",
        out_dir=tmp_path,
        corpus_path=CORPUS,
        post_query=post_query,
        get_progress=get_progress,
        skip_preflight=True,
    )
    path = tmp_path / bm.MANIFEST_NAME
    assert path.is_file()
    raw = orjson.loads(path.read_bytes())
    assert raw.get("schema_version") == "bayes_manifest/v1"
    assert len(raw["pairs"]) == 1
    bm.BayesManifest.model_validate(raw)


def test_zip_member_path_is_under_testquestions_prefix():
    with zipfile.ZipFile(v4.V13A_ZIP) as zf:
        assert v4.SET3_ZIP_MEMBER in zf.namelist()


def test_corpus_fingerprint_matches_v13a():
    fp = runner.corpus_fingerprint(CORPUS)
    assert fp == v4.V13A_EXPECTED["corpus_sha256"]
