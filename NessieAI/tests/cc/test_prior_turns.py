"""The previous turns a Container-CC turn is handed (``NessieAI/cc/prior_turns.py``).

2026-09-23 ruling: every follow-up goes to CC, "and ENSURE that container_cc has access
to the previous run's artifacts": the Search details and the downloaded files. These
pin what is staged, where it is mounted, and that nothing outside this chat's own
records and this user's own trees can be staged.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from NessieAI.cc import prior_turns
from NessieAI.cc.cc_config import CCPaths
from NessieAI.cc.cc_provision import build_user_dirs

CYPHER = ("MATCH (n:T_NHP) WHERE EXISTS { MATCH (f:T_D_FLOW)-[:DERIVED_FROM*1..12]->(n) } "
          "AND EXISTS { MATCH (s:T_D_SEQ)-[:DERIVED_FROM*1..12]->(n) } "
          "RETURN n.id AS id, n.uuid AS uuid, n.Species AS Species ORDER BY id LIMIT 5000")
ROWS = [{"id": 1, "uuid": "NHP-1", "Species": "Macaca mulatta"},
        {"id": 2, "uuid": "NHP-2", "Species": "Macaca fascicularis"},
        {"id": 3, "uuid": "NHP-3", "Species": "Macaca mulatta"}]
ENTITY = {"sampletypes": [{"code": "NHP", "name": "Non Human Primate"},
                          {"code": "D.FLOW", "name": "Flow Cytometry Data"}],
          "assays": [{"code": "Flow Cytometry"}], "projects": []}


def _guard(root: Path):
    """The shape of ``_safe_artifact_path``: resolve, then require containment."""
    real = root.resolve()

    def safe(src):
        if not isinstance(src, str) or not src:
            return None
        try:
            p = Path(src).resolve()
            p.relative_to(real)
            return p
        except (OSError, ValueError):
            return None
    return safe


@pytest.fixture
def outputs(tmp_path):
    root = tmp_path / "outputs" / "20260923_alice"
    (root / "graph_result").mkdir(parents=True)
    (root / "graph").mkdir()
    return root


def _graph_bundle(outputs: Path, bundle_id=1) -> dict:
    rows_file = outputs / "graph_result" / f"graph_result_bundle_{bundle_id}.json"
    rows_file.write_text(json.dumps({"cypher": CYPHER, "rows": ROWS, "count": 3}))
    debug = outputs / "graph" / "graph_debug.json"
    debug.write_text(json.dumps({"entity_output": ENTITY}))
    return {
        "id": bundle_id, "user_query": "Find NHP samples with flow and sequencing data",
        "mode": "graph_query",
        "parser_plan": {"mode": "graph_query", "intent_summary": "Find NHP samples with both.",
                        "filters": {"sampletype_code": "NHP"}},
        "graph_plan": {"cypher": CYPHER, "explanation": "NHP with both", "parameters": {}},
        "graph_result": {"ok": True, "count": 3, "total": 3, "truncated": False, "error": None,
                         "data": ROWS},
        "graph_debug_path": str(debug),
        "terminal_reply": "There are 3 NHP samples.",
        "files": [
            {"key": "graph_debug", "label": "Graph query debug JSON", "path": str(debug),
             "filename": "graph_debug.json", "kind": "graph", "bundle_id": bundle_id},
            {"key": "graph_result", "label": "Graph query result rows", "path": str(rows_file),
             "filename": rows_file.name, "kind": "graph_result", "bundle_id": bundle_id},
        ],
    }


def _ns_entry(turn_id=1, bundle_id=1, **extra) -> dict:
    return {"turn_id": turn_id, "user_query": "Find NHP samples with flow and sequencing data",
            "mode": "graph_query", "router_choice": "nextseek_query", "status": "completed",
            "bundle_id": bundle_id, "key_entities": {"sampletypes": ["NHP"]}, **extra}


def _stage(tmp_path, outputs, chat_log, history, **kw):
    dest = tmp_path / "users" / "1-p" / "alice" / "_memory" / "S1" / "previous_turns"
    return dest, prior_turns.stage_prior_turns(
        chat_log=chat_log, results_history=history, dest_dir=dest,
        safe_ns_path=_guard(outputs.parent), **kw)


# --------------------------------------------------------------- an NS graph turn
def test_a_graph_turn_is_staged_with_its_search_details_rows_and_files(tmp_path, outputs):
    dest, manifest = _stage(tmp_path, outputs, [_ns_entry()], [_graph_bundle(outputs)])
    turn = dest / "turn-01"
    details = json.loads((turn / "search_details.json").read_text())
    assert details["entity"] == ENTITY                       # the full entity output
    assert details["parser"]["mode"] == "graph_query"
    assert details["parser"]["intent_summary"] == "Find NHP samples with both."
    assert details["graph"] == {"cypher": CYPHER, "explanation": "NHP with both", "parameters": {}}
    assert details["neo4j"]["count"] == 3 and details["neo4j"]["truncated"] is False
    assert details["reply"] == "There are 3 NHP samples."

    rows = json.loads((turn / "rows.json").read_text())
    assert rows["cypher"] == CYPHER and rows["rows"] == ROWS
    table = list(csv.DictReader(io.StringIO((turn / "rows.csv").read_text())))
    assert [r["Species"] for r in table] == [r["Species"] for r in ROWS]

    # The download the user was offered is there; the internal debug JSON is not.
    assert (turn / "graph_result_bundle_1.json").is_file()
    assert not (turn / "graph_debug.json").exists()

    assert manifest["turns"][0]["count"] == 3
    md = (dest / "MANIFEST.md").read_text()
    assert "turn 1 (nextseek_query, graph_query)" in md
    assert "`search_details.json`" in md and "`rows.csv`" in md
    assert json.loads((dest / "manifest.json").read_text()) == manifest


def test_the_entity_falls_back_to_the_chat_log_codes(tmp_path, outputs):
    bundle = _graph_bundle(outputs)
    Path(bundle["graph_debug_path"]).unlink()
    dest, _ = _stage(tmp_path, outputs, [_ns_entry()], [bundle])
    details = json.loads((dest / "turn-01" / "search_details.json").read_text())
    assert details["entity"] == {"sampletypes": ["NHP"]}


def test_a_rest_turn_carries_its_request_and_its_result_file(tmp_path, outputs):
    raw = outputs / "api_result_bundle_2.json"
    raw.write_text(json.dumps({"ok": True, "data": {"total": 2, "rows": [
        {"uid": "MUS-1", "Genotype": "CC001"}, {"uid": "MUS-2", "Genotype": "CC002"}]}}))
    bundle = {"id": 2, "user_query": "find mice", "mode": "new_search",
              "api_plan": {"endpoint": "/nextseek_api/samples/advanced_search/", "method": "POST",
                           "requestBody": {"sampletype": "MUS"}},
              "endpoint": "/nextseek_api/samples/advanced_search/", "method": "POST",
              "request_body": {"sampletype": "MUS"}, "raw_result_path": str(raw),
              "files": [{"key": "api_result", "label": "Full API result JSON", "path": str(raw),
                         "filename": raw.name, "kind": "api_result"}]}
    dest, manifest = _stage(tmp_path, outputs, [_ns_entry(turn_id=4, bundle_id=2, mode="new_search")],
                            [bundle])
    turn = dest / "turn-04"
    details = json.loads((turn / "search_details.json").read_text())
    assert details["api"]["endpoint"] == "/nextseek_api/samples/advanced_search/"
    assert details["api"]["request_body"] == {"sampletype": "MUS"}
    assert "graph" not in details
    assert json.loads((turn / "rows.json").read_text())["total"] == 2
    assert (turn / "api_result_bundle_2.json").is_file()
    assert manifest["turns"][0]["count"] == 2


# ---------------------------------------------------------------- scope guards
def test_a_file_outside_the_artifact_roots_is_never_copied(tmp_path, outputs):
    secret = tmp_path / "elsewhere" / "local_settings.py"
    secret.parent.mkdir()
    secret.write_text("SECRET = 1")
    bundle = _graph_bundle(outputs)
    bundle["files"].append({"key": "leak", "label": "x", "path": str(secret),
                            "filename": "local_settings.py", "kind": "report"})
    link = outputs / "graph_result" / "link.json"
    link.symlink_to(secret)
    bundle["report_saved_files"] = {"sneaky": str(link)}
    dest, manifest = _stage(tmp_path, outputs, [_ns_entry()], [bundle])
    staged = {p.name for p in (dest / "turn-01").iterdir()}
    assert "local_settings.py" not in staged and "link.json" not in staged
    reasons = {s["file"]: s["reason"] for s in manifest["turns"][0]["skipped"]}
    assert reasons["local_settings.py"] == "outside_artifact_root"
    assert reasons["link.json"] == "outside_artifact_root"
    assert "SECRET" not in "".join(p.read_text() for p in (dest / "turn-01").iterdir())


def test_only_this_chats_answered_turns_are_staged(tmp_path, outputs):
    """A bundle no chat_log entry names, an errored turn and an unrelated aside stay out."""
    history = [_graph_bundle(outputs, 1), _graph_bundle(outputs, 2), _graph_bundle(outputs, 3)]
    log = [_ns_entry(turn_id=1, bundle_id=1),
           {"turn_id": 2, "user_query": "weather", "router_choice": "unrelated", "status": "completed",
            "mode": "unrelated"},
           _ns_entry(turn_id=3, bundle_id=2, status="error")]
    dest, manifest = _stage(tmp_path, outputs, log, history)
    assert [t["turn_id"] for t in manifest["turns"]] == [1]
    assert sorted(p.name for p in dest.iterdir() if p.is_dir()) == ["turn-01"]


def test_a_cc_turn_is_staged_with_its_answer_and_published_files(tmp_path, outputs):
    art_root = tmp_path / "users" / "1-p" / "alice" / "output" / "artifacts"
    run = art_root / "run-abc"
    run.mkdir(parents=True)
    (run / "species.png").write_bytes(b"png")
    (run / "species.csv").write_text("Species,n\n")
    (run / "artifacts.zip").write_bytes(b"zip")
    outside = tmp_path / "other-user.csv"
    outside.write_text("theirs")
    (run / "escape.csv").symlink_to(outside)
    cc = {"turn_id": 2, "user_query": "plot the species of those", "mode": "cc",
          "router_choice": "container_cc", "status": "completed",
          "assistant_reply": "Here is the plot.", "cc_run_id": "run-abc"}
    dest, manifest = _stage(tmp_path, outputs, [_ns_entry(), cc], [_graph_bundle(outputs)],
                            cc_artifacts_root=art_root)
    assert [t["turn_id"] for t in manifest["turns"]] == [2, 1]      # newest first
    staged = {p.name for p in (dest / "turn-02").iterdir()}
    assert staged == {"answer.md", "species.png", "species.csv"}
    assert (dest / "turn-02" / "answer.md").read_text().startswith("Here is the plot.")


@pytest.mark.parametrize("run_id", ["../../bob/output/artifacts/x", "..", "", None, "a/b"])
def test_a_cc_run_id_that_is_not_a_plain_segment_stages_no_files(tmp_path, outputs, run_id):
    art_root = tmp_path / "art"
    art_root.mkdir()
    cc = {"turn_id": 1, "user_query": "q", "mode": "cc", "router_choice": "container_cc",
          "status": "completed", "assistant_reply": "a", "cc_run_id": run_id}
    dest, manifest = _stage(tmp_path, outputs, [cc], [], cc_artifacts_root=art_root)
    assert {f["file"] for f in manifest["turns"][0]["files"]} == {"answer.md"}


# ---------------------------------------------------------------- housekeeping
def test_only_the_last_turns_are_kept_and_stale_folders_go(tmp_path, outputs):
    history = [_graph_bundle(outputs, n) for n in range(1, 5)]
    log = [_ns_entry(turn_id=n, bundle_id=n) for n in range(1, 5)]
    dest, _ = _stage(tmp_path, outputs, log, history)
    (dest / "turn-01" / "stale.txt").write_text("old")
    dest, manifest = _stage(tmp_path, outputs, log, history, max_turns=2)
    assert [t["turn_id"] for t in manifest["turns"]] == [4, 3]
    assert sorted(p.name for p in dest.iterdir() if p.is_dir()) == ["turn-03", "turn-04"]


def test_nothing_to_stage_empties_the_directory_and_returns_none(tmp_path, outputs):
    dest, _ = _stage(tmp_path, outputs, [_ns_entry()], [_graph_bundle(outputs)])
    assert dest.is_dir()
    dest, manifest = _stage(tmp_path, outputs, [], [])
    assert manifest is None and not dest.exists()


def test_a_file_over_the_cap_is_listed_not_copied(tmp_path, outputs, monkeypatch):
    monkeypatch.setattr(prior_turns, "MAX_FILE_BYTES", 10)
    dest, manifest = _stage(tmp_path, outputs, [_ns_entry()], [_graph_bundle(outputs)])
    assert not (dest / "turn-01" / "graph_result_bundle_1.json").exists()
    assert manifest["turns"][0]["skipped"][0]["reason"].startswith("too_large")
    assert (dest / "turn-01" / "rows.json").is_file()               # the rows are still there


def test_staging_never_raises(tmp_path, outputs, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(prior_turns, "_turns_to_stage", boom)
    assert _stage(tmp_path, outputs, [_ns_entry()], [])[1] is None


def test_the_memory_pointer_names_the_manifest_and_the_newest_turn(tmp_path, outputs):
    _, manifest = _stage(tmp_path, outputs, [_ns_entry(turn_id=3)], [_graph_bundle(outputs)])
    text = prior_turns.memory_pointer(manifest)
    assert "/data/previous_turns/MANIFEST.md" in text
    assert "turn 3 (nextseek_query): `/data/previous_turns/turn-03/`" in text
    assert "nextseek-graph" in text and "rows.csv" in text
    assert prior_turns.memory_pointer(None) == ""


def test_the_real_download_guard_is_the_default(tmp_path, outputs, monkeypatch, settings):
    """With no guard passed, NessieAI.ns.artifacts._safe_artifact_path decides."""
    settings.BASE_DIR = str(tmp_path / "no-such-base")
    monkeypatch.setenv("NEXTSEEK_OUTPUTS_DIR", str(outputs.parent))
    dest = tmp_path / "dest"
    manifest = prior_turns.stage_prior_turns(chat_log=[_ns_entry()],
                                             results_history=[_graph_bundle(outputs)], dest_dir=dest)
    assert (dest / "turn-01" / "graph_result_bundle_1.json").is_file()
    monkeypatch.setenv("NEXTSEEK_OUTPUTS_DIR", str(tmp_path / "somewhere-else"))
    manifest = prior_turns.stage_prior_turns(chat_log=[_ns_entry()],
                                             results_history=[_graph_bundle(outputs)], dest_dir=dest)
    reasons = {s["reason"] for s in manifest["turns"][0]["skipped"]}
    assert reasons == {"outside_artifact_root"}
    assert not (dest / "turn-01" / "graph_result_bundle_1.json").exists()


# ------------------------------------------------------------ provision and mount
def _paths():
    return CCPaths(users_volume="dmac-cc-users", user_root_mount="/dmac/users")


def test_the_staging_dir_is_this_sessions_memory_subtree():
    dirs = build_user_dirs(_paths(), "42-px", "demo", session_id="S1")
    assert dirs.previous_turns_subpath == "42-px/demo/_memory/S1/previous_turns"
    assert dirs.previous_turns_mnt == "/dmac/users/42-px/demo/_memory/S1/previous_turns"
    assert Path(dirs.previous_turns_mnt).name == prior_turns.DIRNAME
    assert build_user_dirs(_paths(), "42-px", "demo").previous_turns_subpath is None
    with pytest.raises(ValueError):
        build_user_dirs(_paths(), "42-px", "demo", session_id="../bob")


def test_the_previous_turns_mount_is_read_only_and_optional():
    from NessieAI.cc import cc_engine

    def by_target(**kw):
        return {m["Target"]: m for m in cc_engine._build_volumes(
            paths=_paths(), project_dirname="42-px", user_id="demo", run_id="R1", **kw)}

    mounts = by_target(cc_state_key="S1", previous_turns=True)
    m = mounts[prior_turns.CONTAINER_PATH]
    assert m["ReadOnly"] is True
    assert m["VolumeOptions"]["Subpath"] == "42-px/demo/_memory/S1/previous_turns"
    assert prior_turns.CONTAINER_PATH not in by_target(cc_state_key="S1")
    assert prior_turns.CONTAINER_PATH not in by_target(cc_state_key=None, previous_turns=True)


def test_the_agent_is_told_to_start_from_the_previous_turn():
    claude_md = (Path(prior_turns.__file__).resolve().parents[1]
                 / "docker" / "cc-runtime" / "container" / "CLAUDE.md").read_text()
    section = claude_md.split("## Follow-ups: start from the previous turn", 1)[1].split("\n## ", 1)[0]
    assert "/data/previous_turns/MANIFEST.md" in section
    assert "search_details.json" in section and "rows.csv" in section
    assert "nextseek-graph --query" in section
    assert "do not call `nextseek-query`" in section
    counts = claude_md.split("## Counts and breakdowns", 1)[1].split("\n## ", 1)[0]
    assert "use `nextseek-aggregate`" in counts
    assert "aggregate that turn's `rows.json` or `rows.csv` directly" in counts
