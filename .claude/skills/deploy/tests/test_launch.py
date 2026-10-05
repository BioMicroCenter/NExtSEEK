"""Tests for launch.py and rules.py (the deploy skill's launch scripts).

Run from the skill directory:
    uv run --no-project --with pytest --with pydantic python -m pytest tests -q -p no:cacheprovider
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
# The tests never read the real box config: they use the committed example.
os.environ["NEXTSEEK_BOXES"] = str(SKILL / "boxes.example.json")
BOXES = json.loads((SKILL / "boxes.example.json").read_text())
RUN_AS = BOXES["instances"]["dev"]["run_as"]
FIX = Path(__file__).resolve().parent / "fixtures"
spec = importlib.util.spec_from_file_location("launch", SKILL / "scripts" / "launch.py")
L = importlib.util.module_from_spec(spec)
sys.modules["launch"] = L   # pydantic resolves the postponed annotations through sys.modules
spec.loader.exec_module(L)
rules = L.rules

EXP = "1c070c08" + "a" * 32
NOW_OK = "2026-09-26T13:00:00Z"


def brief_form(**over):
    b = {
        "instance": "dev", "expected_sha": EXP[:8], "parent": "SUPERVISOR-TEST",
        "change": "Rebuild 2: model fallback everywhere, a cost on every turn.",
        "images": ["app", "cc-agent"],
        "live_markers": [{"pattern": "cost_by_price_table_usd", "file": "/app/NessieAI/cc/translate.py",
                          "proves": "the CC price table is in the running app"}],
        "ci": True, "tag": "20260926-1300",
    }
    b.update(over)
    return b


def run(argv, capsys=None):
    code = L.main(argv)
    out = capsys.readouterr() if capsys else None
    return code, out


def make_brief(tmp_path, capsys, **over):
    tmp_path.mkdir(parents=True, exist_ok=True)
    f = tmp_path / "brief-form.json"
    f.write_text(json.dumps(brief_form(**over)))
    d = tmp_path / "launch"
    code, out = run(["brief", "--form", str(f), "--out-dir", str(d), "--now", NOW_OK], capsys)
    return code, out, d


# --------------------------------------------------------------------------- brief
def test_a_valid_dev_brief_writes_brief_json(tmp_path, capsys):
    code, out, d = make_brief(tmp_path, capsys)
    assert code == 0, out.err
    data = json.loads((d / "brief.json").read_text())
    assert data["schema"] == "launch-brief/v1"
    assert data["derived"]["tag"] == "20260926-1300"
    assert data["derived"]["images"] == ["app", "cc-agent"]
    assert data["derived"]["report_to"] == "SUPERVISOR-TEST"
    assert "REPORT_TO: SUPERVISOR-TEST" in out.out


def test_parent_must_be_stated_even_as_null(tmp_path, capsys):
    f = tmp_path / "b.json"
    b = brief_form()
    del b["parent"]
    f.write_text(json.dumps(b))
    code, out = run(["brief", "--form", str(f), "--out-dir", str(tmp_path / "d"), "--now", NOW_OK], capsys)
    assert code == 2 and "'parent' is required" in out.err


def test_parent_null_means_the_report_stays_with_the_launcher(tmp_path, capsys):
    code, out, d = make_brief(tmp_path, capsys, parent=None)
    assert code == 0
    assert json.loads((d / "brief.json").read_text())["derived"]["report_to"] == "self"


@pytest.mark.parametrize("over,needle", [
    ({"expected_sha": "zzz"}, "expected_sha"),
    ({"images": ["app", "nginx"]}, "images"),
    ({"allowed_extras": ["reboot the box"]}, "unknown extras"),
    ({"tag": "tonight"}, "tag must look like"),
])
def test_schema_errors_exit_2_and_name_the_field(tmp_path, capsys, over, needle):
    code, out, _ = make_brief(tmp_path, capsys, **over)
    assert code == 2 and needle in out.err


def _cases_file(tmp_path, family="sample_search", vid="ss.q1"):
    p = tmp_path / "cases.json"
    p.write_text(json.dumps({"families": {family: {"description": "x", "variants": [
        {"id": vid, "family": family, "name": "q", "turns": [{"label": "main", "query": "How many?",
         "pass_criteria": [{"field": "last_reply", "op": "nonempty", "value": None}]}]}]}}}))
    return str(p)


@pytest.mark.parametrize("over_fn,needle", [
    (lambda t: {"instance": "prod", "nessie": {"cases": [{"file": _cases_file(t), "cc_turns_estimate": 1}]},
                "paid": {"approved": True, "budget_usd": 5, "approved_by": "operator"}},
     "prod_nessie: true"),
    (lambda t: {"nessie": {"cases": [{"file": _cases_file(t), "cc_turns_estimate": 1}]}}, "is paid"),
    (lambda t: {"nessie": {"cases": [{"file": _cases_file(t), "cc_turns_estimate": 30}]},
                "paid": {"approved": True, "budget_usd": 5, "approved_by": "operator"}}, "over the budget"),
    (lambda t: {"images": ["bedrock-proxy", "app"]}, "bedrock-proxy rebuild ok"),
    (lambda t: {"instance": "local"}, "off on this workstation"),
    (lambda t: {"prod_nessie": True}, "contradictory"),
    (lambda t: {"allowed_extras": ["seek restart ok"], "ruled_out": ["seek restart ok"]},
     "both allowed and ruled out"),
    (lambda t: {"instance": "prod", "prod_nessie": True,
                "nessie": {"cases": [{"file": _cases_file(t, "entity_write", "ew.q1"), "cc_turns_estimate": 0}]},
                "paid": {"approved": True, "budget_usd": 5, "approved_by": "operator"}}, "never on prod"),
    (lambda t: {"waivers": [{"check": "origin_match", "reason": "operator said fine"}]}, "cannot be waived"),
    (lambda t: {"instance": "prod", "migrations_expected": True}, "db backup ok"),
])
def test_stop_rules_exit_5_with_the_exact_problem(tmp_path, capsys, over_fn, needle):
    code, out, d = make_brief(tmp_path, capsys, **over_fn(tmp_path))
    assert code == 5, out
    assert needle in out.err
    # written for the report only; every later step refuses it
    assert json.loads((d / "brief.json").read_text())["verdict"] == "stop"
    code, out = run(["preflight-script", "--brief", str(d / "brief.json")], capsys)
    assert code == 5 and "was refused" in out.err


@pytest.mark.parametrize("now,ok", [
    ("2026-09-26T05:00:00Z", False),   # inside the dump lock
    ("2026-09-26T03:50:00Z", False),   # would run into it
    ("2026-09-26T01:20:00Z", False),   # would run into the graph sync
    ("2026-09-26T12:05:00Z", True),
    ("2026-09-26T22:00:00Z", True),
])
def test_the_dev_time_windows(tmp_path, capsys, now, ok):
    f = tmp_path / "b.json"
    f.write_text(json.dumps(brief_form()))
    code, out = run(["brief", "--form", str(f), "--out-dir", str(tmp_path / "d"), "--now", now], capsys)
    assert (code == 0) is ok, out
    if not ok:
        assert code == 6 and "WINDOW" in out.err


def test_prod_has_no_dev_windows(tmp_path, capsys):
    code, out, _ = make_brief(tmp_path, capsys, instance="prod")
    f = tmp_path / "b.json"
    f.write_text(json.dumps(brief_form(instance="prod")))
    code, out = run(["brief", "--form", str(f), "--out-dir", str(tmp_path / "p"),
                     "--now", "2026-09-26T05:00:00Z"], capsys)
    assert code == 0, out.err


def test_an_existing_brief_is_not_overwritten(tmp_path, capsys):
    make_brief(tmp_path, capsys)
    code, out, _ = make_brief(tmp_path, capsys)
    assert code == 3


# --------------------------------------------------------------------------- preflight
def good_preflight(**kv):
    base = {
        "utc": "2026-09-26T13:01:00Z", "disk_free_gb": "112", "mem_available_gib": "20",
        "swap_total_gib": "3", "swap_used_gib": "0", "busy": "none", "branch": "dev",
        "head": "926be1e3" + "b" * 32, "head_subject": "fix: a thing", "fetch": "ok",
        "expected": EXP, "origin": EXP, "origin_match": "yes", "ff": "yes", "range_count": "12",
        "nextseek_env": "ok", "ci_env_keys": "CI_SMOKE_PASS,CI_SMOKE_USER,CI_WRITE_PASS,CI_WRITE_USER,",
        "http": "200", "labs_source": "present", "done": "yes",
    }
    base.update(kv)
    lines = [f"KV {k}={v}" for k, v in base.items()]
    lines += ["DIRTY ?? logs/",
              "DIRTY  M NessieAI/chat_nextseek/src/chat_nextseek/context/projects_db.json",
              "CONTAINER nextseek status=running oom=false restarts=0 started=2026-09-25T12:39:20Z",
              "CONTAINER seek status=running oom=false restarts=0 started=2026-09-14T19:41:23Z",
              "TMUX 0: 3 windows (created Mon Sep 14 15:01:48 2026)",
              "MEM seek 7.7GiB / 16GiB"]
    return "\n".join(lines) + "\n"


def preflight(tmp_path, capsys, text, **brief_over):
    code, out, d = make_brief(tmp_path, capsys, **brief_over)
    assert code == 0, out.err
    (d / "preflight.out").write_text(text)
    code, out = run(["preflight", "--brief", str(d / "brief.json")], capsys)
    return code, out, d


def test_preflight_script_renders_clean_for_both_boxes(tmp_path, capsys):
    for inst, needle in (("dev", "labs_source"), ("prod", "compose_config")):
        code, out, d = make_brief(tmp_path / inst, capsys, instance=inst)
        assert code == 0
        code, out = run(["preflight-script", "--brief", str(d / "brief.json")], capsys)
        assert code == 0 and "@@" not in out.out and needle in out.out and EXP[:8] in out.out
        p = tmp_path / inst / "pre.sh"
        p.write_text(out.out)
        assert subprocess.run(["bash", "-n", str(p)]).returncode == 0


def test_a_healthy_preflight_is_ok(tmp_path, capsys):
    code, out, d = preflight(tmp_path, capsys, good_preflight())
    assert code == 0, out.err
    pf = json.loads((d / "preflight.json").read_text())
    assert pf["verdict"] == "ok" and pf["box_head"].startswith("926be1e3") and pf["range_count"] == 12


@pytest.mark.parametrize("text_fn,check", [
    (lambda: good_preflight(disk_free_gb="12"), "disk"),
    (lambda: good_preflight(origin_match="no"), "origin_match"),
    (lambda: good_preflight(ff="no"), "ff"),
    (lambda: good_preflight(busy="1234 python manage.py graph_sync --full;"), "busy"),
    (lambda: good_preflight(http="502"), "http"),
    (lambda: good_preflight(branch="main"), "branch"),
    (lambda: good_preflight(expected="NOT_FOUND"), "expected"),
    (lambda: good_preflight() + "DIRTY  M nextseek_api/views.py\n", "dirty"),
    (lambda: good_preflight() + "DIRTY_TOUCHED nextseek_api/views.py\n", "dirty_touched"),
    (lambda: good_preflight().replace("oom=false restarts=0 started=2026-09-14", "oom=true restarts=0 started=2026-09-14"), "oom"),
    (lambda: good_preflight() + "TMUX launch-20260926-0900: 1 windows\n", "tmux"),
    (lambda: good_preflight().replace("KV done=yes\n", ""), "complete"),
])
def test_each_stop_row(tmp_path, capsys, text_fn, check):
    code, out, d = preflight(tmp_path, capsys, text_fn())
    assert code == 5, out
    assert f"- {check}:" in out.err


def test_a_seek_worker_kill_with_seek_answering_only_warns(tmp_path, capsys):
    # 2026-09-29: SEEK's cap kills a runaway Puma worker by design; the container stays up and answers
    text = good_preflight().replace("oom=false restarts=0 started=2026-09-14", "oom=true restarts=0 started=2026-09-14")
    text = text.replace("KV done=yes\n", "KV seek_http=200\nKV done=yes\n")
    code, out, d = preflight(tmp_path, capsys, text)
    assert code == 0, out.err
    rows = {r["id"]: r for r in json.loads((d / "preflight.json").read_text())["checks"]}
    assert rows["oom"]["verdict"] == "ok" and rows["seek_worker_oom"]["verdict"] == "warn"


def test_a_seek_kill_still_stops_when_seek_does_not_answer_or_another_container_ooms(tmp_path, capsys):
    base = good_preflight().replace("KV done=yes\n", "KV seek_http=502\nKV done=yes\n")
    text = base.replace("oom=false restarts=0 started=2026-09-14", "oom=true restarts=0 started=2026-09-14")
    code, out, d = preflight(tmp_path / "a", capsys, text)
    assert code == 5 and "- oom:" in out.err
    text = good_preflight().replace("KV done=yes\n", "KV seek_http=200\nKV done=yes\n").replace(
        "CONTAINER nextseek status=running oom=false", "CONTAINER nextseek status=running oom=true")
    code, out, d = preflight(tmp_path / "b", capsys, text)
    assert code == 5 and "- oom:" in out.err


def test_a_waiver_turns_a_stop_into_waived(tmp_path, capsys):
    text = good_preflight().replace("oom=false restarts=0 started=2026-09-14", "oom=true restarts=0 started=2026-09-14")
    code, out, d = preflight(tmp_path, capsys, text,
                             waivers=[{"check": "oom", "reason": "operator: seek OOM on 09-14 is old news"}])
    assert code == 0, out.err
    rows = {r["id"]: r for r in json.loads((d / "preflight.json").read_text())["checks"]}
    assert rows["oom"]["verdict"] == "waived" and "old news" in rows["oom"]["rule"]


def test_missing_labs_source_only_warns_without_an_app_rebuild(tmp_path, capsys):
    code, out, d = preflight(tmp_path, capsys, good_preflight(labs_source="missing"), images=["cc-agent"],
                             live_markers=[])
    assert code == 0, out.err
    code, out, d = preflight(tmp_path / "x", capsys, good_preflight(labs_source="missing"))
    assert code == 5 and "labs_source" in out.err


# --------------------------------------------------------------------------- commits + review
def git_repo(tmp_path, n_commits=3, touch=("nextseek_api/views.py",)):
    r = tmp_path / "repo"
    r.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org"}
    g = lambda *a: subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True, text=True, env=env).stdout
    g("init", "-q", "-b", "dev")
    (r / "README.md").write_text("x\n")
    g("add", "."); g("commit", "-qm", "base")
    base = g("rev-parse", "HEAD").strip()
    for i in range(n_commits):
        path = touch[i % len(touch)]
        (r / path).parent.mkdir(parents=True, exist_ok=True)
        (r / path).write_text(f"{i}\n")
        g("add", "."); g("commit", "-qm", f"change {i} to {path}")
    head = g("rev-parse", "HEAD").strip()
    return r, base, head


def commits_setup(tmp_path, capsys, n=3, touch=("nextseek_api/views.py",), **brief_over):
    repo, base, head = git_repo(tmp_path, n, touch)
    code, out, d = make_brief(tmp_path, capsys, expected_sha=head[:8], **brief_over)
    assert code == 0, out.err
    code, out = run(["commits", "--brief", str(d / "brief.json"), "--repo", str(repo), "--base", base], capsys)
    return code, out, d


def test_commits_lists_the_range_and_derives_images(tmp_path, capsys):
    code, out, d = commits_setup(tmp_path, capsys, images=["app"])
    assert code == 0, out.err
    form = json.loads((d / "commit-review-form.json").read_text())
    assert form["range"]["total"] == 3 and form["range"]["skipped"] == 0
    assert form["derived"]["images_needed"] == ["app"]
    assert form["behaviour_changes"] == [] and form["summary"] is None


def test_more_than_fifty_commits_lists_the_latest_fifty(tmp_path, capsys):
    code, out, d = commits_setup(tmp_path, capsys, n=53, images=["app"])
    assert code == 0, out.err
    form = json.loads((d / "commit-review-form.json").read_text())
    assert form["range"] == {**form["range"], "total": 53, "listed": 50, "skipped": 3}
    assert form["commits"][0]["subject"] == "change 52 to nextseek_api/views.py"
    assert "3 skipped" in out.out


@pytest.mark.parametrize("touch,images,needle", [
    (("NessieAI/docker/cc-runtime/Dockerfile",), ["app"], "needs cc-agent"),
    (("docker/nginx.conf",), ["app"], "(nginx)"),
    (("nextseek_api/migrations/0042_x.py",), ["app"], "(migration)"),
    (("NessieAI/chat_nextseek/src/chat_nextseek/context/projects_db.json",), ["app"], "needs cc-agent"),
])
def test_the_range_and_the_brief_must_agree(tmp_path, capsys, touch, images, needle):
    code, out, d = commits_setup(tmp_path, capsys, touch=touch, images=images)
    assert code == 5 and needle in out.err


SEED = "startup/seed/sql/projects_context.curated.sql"


def test_an_acknowledged_flagged_path_does_not_stop_the_launch(tmp_path, capsys):
    for x in "abc":
        (tmp_path / x).mkdir()
    code, out, d = commits_setup(tmp_path / "a", capsys, touch=(SEED,), images=[])
    assert code == 5 and "(seed)" in out.err
    ack = [{"path": SEED, "reason": "operator: curated context seed is wanted"}]
    code, out, d = commits_setup(tmp_path / "b", capsys, touch=(SEED,), images=[], acknowledged_flags=ack)
    assert code == 0, out.err
    assert "acknowledged by the operator" in json.dumps(json.loads((d / "commit-review-form.json").read_text())["derived"]["warnings"])
    # an acknowledgement is per path: another seed file still stops
    code, out, d = commits_setup(tmp_path / "c", capsys, touch=(SEED, "startup/seed/sql/other.sql"), images=[],
                                 acknowledged_flags=ack)
    assert code == 5 and "other.sql" in out.err


def test_acknowledged_flags_are_validated(tmp_path, capsys):
    code, out, d = make_brief(tmp_path, capsys, acknowledged_flags=[{"path": SEED, "reason": "x"}])
    assert code == 2 and "reason" in out.err


def test_images_null_derives_from_the_range(tmp_path, capsys):
    code, out, d = commits_setup(tmp_path, capsys, touch=("NessieAI/docker/ns-sidecar/app/x.py", "seek/views.py"),
                                 images=None)
    assert code == 0, out.err
    assert "IMAGES_NEEDED: app, nextseek-sidecar" in out.out


def filled_review(d, drop_one=False):
    form = json.loads((d / "commit-review-form.json").read_text())
    shas = [c["sha"] for c in form["commits"]]
    form["summary"] = "Three edits to the views; one behaviour change, proved live by a smoke test."
    form["behaviour_changes"] = [{
        "id": "BC1", "summary": "views answer faster", "commits": shas[:2] if drop_one else shas[:2],
        "unit_tests": [{"path": "nextseek_api/tests/test_views.py", "lane": "blocking"}],
        "live_check": None, "status": "unit_only", "proposals": ["P1"]}]
    form["no_behaviour_change"] = [] if drop_one else [{"commits": shas[2:], "reason": "docs"}]
    form["proposed_checks"] = [{"id": "P1", "kind": "smoke", "where": "ci/smoke/test_views_live.py",
                                "proves": "the fast path is served", "fails_when": "p95 over 2 s",
                                "cost": "free", "priority": "soon", "covers": ["BC1"]}]
    p = d / "filled.json"
    p.write_text(json.dumps(form))
    return p


def test_review_writes_the_section_and_says_where_it_goes(tmp_path, capsys):
    code, out, d = commits_setup(tmp_path, capsys, images=["app"])
    code, out = run(["review", "--brief", str(d / "brief.json"), "--form", str(filled_review(d))], capsys)
    assert code == 0, out.err
    assert "DELIVER: SendMessage to 'SUPERVISOR-TEST'" in out.out
    md = (d / "commit-review.md").read_text()
    assert "| P1 | smoke |" in md and "## Behaviour changes" in md
    # deterministic: a second render of the same input is byte-identical
    first = md
    code, out = run(["review", "--brief", str(d / "brief.json"), "--form", str(filled_review(d)), "--force"], capsys)
    assert (d / "commit-review.md").read_text() == first


def test_review_refuses_an_unaccounted_commit(tmp_path, capsys):
    code, out, d = commits_setup(tmp_path, capsys, images=["app"])
    code, out = run(["review", "--brief", str(d / "brief.json"), "--form", str(filled_review(d, drop_one=True))], capsys)
    assert code == 2 and "not accounted for" in out.err


@pytest.mark.parametrize("mutate,needle", [
    (lambda f: f["behaviour_changes"][0].update(status="live"), "needs a live_check"),
    (lambda f: f["behaviour_changes"][0].update(proposals=[]), "needs a proposal or an accepted_gap"),
    (lambda f: f["proposed_checks"][0].update(covers=["BC9"]), "unknown behaviour changes"),
    (lambda f: f["behaviour_changes"][0].update(proposals=["P7"]), "does not exist"),
    (lambda f: f["proposed_checks"][0].update(cost="cheap"), "cost"),
])
def test_review_rules(tmp_path, capsys, mutate, needle):
    code, out, d = commits_setup(tmp_path, capsys, images=["app"])
    p = filled_review(d)
    f = json.loads(p.read_text())
    mutate(f)
    p.write_text(json.dumps(f))
    code, out = run(["review", "--brief", str(d / "brief.json"), "--form", str(p)], capsys)
    assert code == 2 and needle in out.err, out.err


# --------------------------------------------------------------------------- runner
def rendered_runner(tmp_path, capsys, **brief_over):
    code, out, d = preflight(tmp_path, capsys, good_preflight(), **brief_over)
    assert code == 0, out.err
    code, out = run(["runner", "--brief", str(d / "brief.json")], capsys)
    assert code == 0, out.err
    tag = json.loads((d / "brief.json").read_text())["derived"]["tag"]
    return d, (d / "runner" / f"launch-{tag}-run.sh").read_text()


def test_the_runner_renders_and_parses(tmp_path, capsys):
    d, text = rendered_runner(tmp_path, capsys, images=["cc-agent", "app"],
                              allowed_extras=["bedrock-proxy rebuild ok"])
    assert "@@" not in text
    assert f"EXP={EXP}" in text
    assert 'COMPONENTS="app cc-agent"' in text          # rules order, not the brief's
    assert "--no-nessie" in text and "DO_NESSIE=0" in text and "DO_LABS=1" in text
    assert "test_route_is_reachable" in text            # the known dev CI reds
    assert "0400-1200" in text and "0145-0245" in text  # the stop windows
    assert "marker nextseek /app/NessieAI/cc/translate.py 'cost_by_price_table_usd'" in text


def test_the_paid_step_needs_every_model_id_proven(tmp_path, capsys):
    # 2026-09-28: a refused id silently moves to the fallback (F1), so a paid run needs the green line itself
    from rules import HEALTH_NEEDS
    assert ("model ids reachable", "app") in HEALTH_NEEDS
    d, text = rendered_runner(tmp_path, capsys, images=["app"], allowed_extras=[])
    nessie = text.split("if [ $DO_NESSIE = 1 ]; then", 1)[1]
    assert "grep -qE '✓ model ids reachable'" in nessie.split("docker compose exec", 1)[0]


def _member_brief(tmp_path):
    admin = tmp_path / "cases-admin.json"
    member = tmp_path / "cases-member.json"
    admin.write_text(Path(_cases_file(tmp_path)).read_text())
    member.write_text(Path(_cases_file(tmp_path)).read_text())
    return {"nessie": {"cases": [{"file": str(admin), "cc_turns_estimate": 1},
                                 {"file": str(member), "cc_turns_estimate": 1}]},
            "paid": {"approved": True, "budget_usd": 5, "approved_by": "operator"}}


def test_a_member_cases_file_runs_as_the_non_admin_login(tmp_path, capsys):
    d, text = rendered_runner(tmp_path, capsys, **_member_brief(tmp_path))
    names = sorted(p.name for p in (d / "runner").glob("launch-*-cases-*"))
    assert names == ["launch-20260926-1300-cases-1.json", "launch-20260926-1300-cases-2-member.json"]
    block = "case \"$f\" in" + text.split("case \"$f\" in", 1)[1].split("esac", 1)[0] + "esac"
    s = tmp_path / "login.sh"
    s.write_text("CI_SMOKE_USER=smoke CI_WRITE_USER=writer\nfor f in a-cases-1.json a-cases-2-member.json; do\n"
                 f"{block}\necho \"$f $LOGIN $U $PW\"\ndone\n")
    r = subprocess.run(["bash", str(s)], capture_output=True, text=True)
    assert r.stdout.splitlines() == ["a-cases-1.json admin writer CI_WRITE_PASS",
                                     "a-cases-2-member.json member smoke CI_SMOKE_PASS"]
    assert '--user "$U" --password-env "$PW"' in text and '-e "$PW"' in text
    st = L.parse_status("NESSIE_START run=dev-x-cases-2-member utc=2026-10-04T01:19:01Z login=member 01:19:01\n"
                        "NESSIE_START run=dev-x-cases-1 utc=2026-10-04T01:00:01Z 01:00:01\n")
    assert [n["login"] for n in st["nessie"]] == ["member", "admin"]


def test_a_member_cases_file_needs_the_non_admin_login_on_the_box(tmp_path, capsys):
    text = good_preflight(ci_env_keys="CI_WRITE_PASS,CI_WRITE_USER,")
    code, out, d = preflight(tmp_path, capsys, text, **_member_brief(tmp_path))
    assert code == 5 and "- member_login:" in out.err and "CI_SMOKE_USER" in out.err
    code, out, d = preflight(tmp_path / "ok", capsys, good_preflight(), **_member_brief(tmp_path))
    assert code == 0, out.err
    rows = {r["id"]: r for r in json.loads((d / "preflight.json").read_text())["checks"]}
    assert rows["member_login"]["verdict"] == "ok"


def _functions(text):
    return text.split("# >>> functions", 1)[1].split("# <<< functions", 1)[0]


def bash_judge(tmp_path, text, log, pending):
    header = "\n".join(ln for ln in text.splitlines()[:30]
                       if re.match(r"^(KNOWN_HEALTH_REDS|KNOWN_CI_REDS|SUMMARY_RED|WINDOWS)=", ln))
    script = tmp_path / "j.sh"
    script.write_text(f"{header}\nST={tmp_path}/st\n{_functions(text)}\nPENDING='{pending}'\n"
                      f'health_reds "{log}" | while IFS= read -r l; do echo "$(classify "$l")|$l"; done\n')
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return [tuple(x.split("|", 1)) for x in r.stdout.splitlines()]


def test_bash_and_python_judges_agree_on_a_real_app_log(tmp_path, capsys):
    d, text = rendered_runner(tmp_path, capsys)
    log = FIX / "real-app-p3.log"
    b = bash_judge(tmp_path, text, log, "cc-agent")
    known = json.loads((d / "brief.json").read_text())["derived"]["known_health_reds"]
    p = [(L.classify_red(r, {"cc-agent"}, known), r) for r in L.health_reds(log.read_text())]
    assert b == p
    assert ("known", "graph drift: DRIFT: 1 of 43 checks failed: catalog.assistant_investigations") in p
    assert any(c == "known" and r.startswith("no usable GHCR credential") for c, r in p)


def test_a_red_for_a_pending_component_is_expected_and_after_it_is_stale(tmp_path, capsys):
    d, text = rendered_runner(tmp_path, capsys)
    log = tmp_path / "x.log"
    log.write_text("       ✗ cc-agent runtime: STALE: dmac-assistant:poc: node v20 where the checkout \n"
                   "builds FROM node:22\n"
                   "       ✗ app image code: STALE: of 2150 tracked app files, 3 differ\n"
                   "       ✗ something new: broke\n"
                   "       ✗ CI failed: 2 failed, 304 passed\n")
    got = bash_judge(tmp_path, text, log, "cc-agent")
    assert got[0][0] == "pending cc-agent" and got[0][1].endswith("builds FROM node:22")
    assert got[1][0] == "stale app"
    assert got[2][0] == "unexplained"
    assert got[3][0] == "skip"


def judges_bad(tmp_path, text, log, pending, known):
    """The reds that stop, from the runner's bash judge() and from launch.py, with the instance's known reds."""
    funcs = _functions(text)
    script = tmp_path / "jb.sh"
    script.write_text(f"KNOWN_HEALTH_REDS='{L.ere_alternation(known)}'\nSUMMARY_RED='{rules.SUMMARY_RED}'\n"
                      f"ST={tmp_path}/st\n{funcs}\nPENDING='{pending}'\njudge \"{log}\" app\n")
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    reds = [{"text": t, "class": L.classify_red(t, set(pending.split()), known)}
            for t in L.health_reds(Path(log).read_text())]
    reds = [x for x in reds if x["class"] != "skip"]
    return r.stdout.splitlines(), L.bad_reds(reds), reds


def _known(instance):
    return [k.match for k in rules.INSTANCES[instance].known_reds if k.kind == "health"]


def test_the_rebuild_summary_does_not_stop_when_every_other_red_is_pending(tmp_path, capsys):
    # prod-launch-20261003-2359: the only red was cc-agent context with cc-agent still to rebuild
    d, text = rendered_runner(tmp_path, capsys)
    log = FIX / "real-app-prod-20261003-2359.log"
    b, p, reds = judges_bad(tmp_path, text, log, "cc-agent", _known("prod"))
    assert b == p == []
    assert [x["class"] for x in reds] == ["pending cc-agent", "summary"]


def test_the_rebuild_summary_still_stops_beside_a_stale_or_unexplained_red(tmp_path, capsys):
    d, text = rendered_runner(tmp_path, capsys)
    log = tmp_path / "mixed.log"
    log.write_text((FIX / "real-app-prod-20261003-2359.log").read_text().replace(
        "       ✗ Rebuild finished",
        "       ✗ app image code: STALE: of 2212 tracked app files, 3 differ\n       ✗ Rebuild finished"))
    b, p, _ = judges_bad(tmp_path, text, log, "cc-agent", _known("prod"))
    assert b == p and len(p) == 2
    assert p[0].startswith("app image code: STALE") and p[1].startswith("Rebuild finished but is red")
    # the summary of a rebuild whose app or front door is not up is never excused
    log.write_text("       ✗ Rebuild finished but is red: stack health. The app or front door is not up; see "
                   "DEPLOYMENT.md section 5 (Rollback).\n")
    b, p, _ = judges_bad(tmp_path, text, log, "", _known("prod"))
    assert b == p and len(p) == 1
    # a summary with no other red is not explained by anything, so it still stops
    log.write_text("       ✗ Rebuild finished but is red: stack health. No rollback is needed: the build and "
                   "restart succeeded, only the health judgement is red.\n")
    b, p, _ = judges_bad(tmp_path, text, log, "cc-agent", _known("prod"))
    assert b == p and len(p) == 1 and p[0].startswith("Rebuild finished but is red")


DEV_2010 = FIX / "real-app-dev-20261003-2010.log"


def test_op14_drift_alone_is_known_on_dev_and_stops_on_prod(tmp_path, capsys):
    # dev-launch-20261003-2010: graph sync health red only from the drift OP14 leaves red on dev
    d, text = rendered_runner(tmp_path, capsys)
    b, p, reds = judges_bad(tmp_path, text, DEV_2010, "cc-agent", _known("dev"))
    assert b == p == []
    assert any(x["class"] == "known" and x["text"].startswith("graph sync health") for x in reds)
    b, p, _ = judges_bad(tmp_path, text, DEV_2010, "cc-agent", _known("prod"))
    assert b == p and any(x.startswith("graph sync health") for x in p) and p[-1].startswith("Rebuild finished")


@pytest.mark.parametrize("old,new", [
    # another drift check failed too
    ("in: catalog.assistant_investigations. It", "in: catalog.assistant_investigations, 1.lineage.duplicate_edges. It"),
    # a stale job: its problem line comes before the drift line
    ("           drift run 101 (finished", "           outbox is stale: 2.0 h old against 1.0 h\n           drift run 101 (finished"),
    # an overdue failed run
    ("failed runs: 0 \n(0 overdue)", "failed runs: 1 \n(1 overdue)"),
    # an overdue failing outbox row
    ("failing outbox rows: 0 (0 overdue)", "failing outbox rows: 2 (1 overdue)"),
])
def test_graph_sync_health_still_stops_on_dev_for_anything_but_the_op14_drift(tmp_path, capsys, old, new):
    d, text = rendered_runner(tmp_path, capsys)
    src = DEV_2010.read_text()
    assert old in src
    log = tmp_path / "x.log"
    log.write_text(src.replace(old, new))
    b, p, _ = judges_bad(tmp_path, text, log, "cc-agent", _known("dev"))
    assert b == p and p[0].startswith("graph sync health") and p[-1].startswith("Rebuild finished")


def test_the_off_prod_drift_set_is_the_ci_smoke_one():
    import ast
    src = (SKILL.parents[2] / "ci" / "smoke" / "test_graph_sync_status.py").read_text()
    m = re.search(r"^OFF_PROD_ALLOWED_DRIFT = frozenset\((\{[^}]*\})\)", src, re.M)
    assert m and ast.literal_eval(m.group(1)) == set(rules.OFF_PROD_ALLOWED_DRIFT)


def test_in_window(tmp_path, capsys):
    d, text = rendered_runner(tmp_path, capsys)
    fn = _functions(text)
    for hhmm, expect in (("0500", "0400-1200"), ("1300", ""), ("0200", "0145-0245")):
        s = tmp_path / "w.sh"
        s.write_text(f'WINDOWS="0400-1200 0145-0245"\n{fn}\ndate(){{ echo {hhmm}; }}\nin_window\n')
        r = subprocess.run(["bash", str(s)], capture_output=True, text=True)
        assert r.stdout.strip() == expect


# --------------------------------------------------------------------------- ssh door
def test_ssh_dry_run_builds_one_quoted_remote_command(tmp_path, capsys):
    code, out, d = preflight(tmp_path, capsys, good_preflight())
    (tmp_path / "pre.sh").write_text("echo hi\n")
    code, out = run(["ssh", "--brief", str(d / "brief.json"), "--purpose", "preflight",
                     "--script", str(tmp_path / "pre.sh"), "--dry-run"], capsys)
    assert code == 0
    assert f"sudo -n -u {RUN_AS} bash -l" in out.out
    assert "'echo " in out.out  # the whole remote command is one quoted argument


def test_after_a_failed_connection_the_door_stays_shut(tmp_path, capsys):
    code, out, d = preflight(tmp_path, capsys, good_preflight())
    (d / ".ssh-failed").write_text("preflight: exit 255")
    (tmp_path / "pre.sh").write_text("echo hi\n")
    code, out = run(["ssh", "--brief", str(d / "brief.json"), "--purpose", "read",
                     "--script", str(tmp_path / "pre.sh"), "--dry-run"], capsys)
    assert code == 7 and "One try, then report" in out.err


def test_start_inside_the_window_is_refused(tmp_path, capsys):
    code, out, d = preflight(tmp_path, capsys, good_preflight())
    code, out = run(["ssh", "--brief", str(d / "brief.json"), "--purpose", "start", "--dry-run",
                     "--now", "2026-09-27T06:00:00Z"], capsys)
    assert code == 6


def test_a_second_connection_while_one_is_open_is_refused(tmp_path, capsys):
    import fcntl
    code, out, d = preflight(tmp_path, capsys, good_preflight())
    (d / ".box.lock").touch()
    fd = os.open(d / ".box.lock", os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        (tmp_path / "pre.sh").write_text("echo hi\n")
        code, out = run(["ssh", "--brief", str(d / "brief.json"), "--purpose", "read",
                         "--script", str(tmp_path / "pre.sh"), "--dry-run"], capsys)
        assert code == 7 and "one at a time" in out.err
    finally:
        os.close(fd)


# --------------------------------------------------------------------------- judge + report
STATUS = """START tag=20260926-1300 instance=dev components=app cc-agent 13:00:01
HEAD 1c070c08 Merge fix/cc-leftovers-fallback-pricing 13:00:04
REBUILD app exit=1 13:12:00
ROLLBACK app nextseek-nextseek:pre-20260926T090004-1c070c08 13:12:00
REBUILT app 13:12:00
STATIC exit=0 804 post-processed. 13:12:15
LABS copied 13:12:15
RED app [pending cc-agent] cc-agent runtime: STALE: node v20 13:13:20
RED app [known] graph drift: DRIFT: 1 of 43 checks failed: catalog.assistant_investigations 13:13:20
REBUILD cc-agent exit=0 13:18:00
ROLLBACK cc-agent dmac-assistant:pre-20260926T091320-1c070c08 13:18:00
REBUILT cc-agent 13:18:00
CHECKS CHECK http 200 13:19:30
CI exit=1 13:32:00
CI_RESULT CI failed: 2 failed, 309 passed, 59 skipped, 13 xfailed in 11:02 13:32:00
ALL_DONE 13:32:01
"""
CHECKS = """CHECK http 200
CHECK container nextseek status=running restarts=0 oom=false started=2026-09-26T13:12:30Z
CHECK boot_markers 0
CHECK cc_runner (True, 'ok')
CHECK labs_line [CONFIG][LABS] 40 labs 'source': 'previous_file'
CHECK refresh_marker 2026-09-26 13:14:00.000000000 +0000
CHECK sidecar_ops match
IMAGE nextseek-nextseek:latest 2026-09-26T09:11:00-04:00
MARKER count=2 container=nextseek file=/app/NessieAI/cc/translate.py pattern=cost_by_price_table_usd
CHECK memory available_gib=21
"""
CI_LOG = """       ✓ app + front door: nextseek + nextseek_nginx running
       ✓ cc-agent runtime: node v22.11.0, Claude Code 2.1.282, matplotlib 3.9
       ✗ graph drift: DRIFT: 1 of 43 checks failed:
catalog.assistant_investigations
FAILED ci/smoke/test_reachability.py::test_route_is_reachable[/seek/sample_types/id={sample_type_id}/]
FAILED ci/smoke/test_reachability.py::test_route_is_reachable[/nextseek_api/sample_types/{sample_type_id}/]
====== 2 failed, 309 passed, 59 skipped, 13 xfailed in 662.00s (0:11:02) =======
       ✗ CI failed: 2 failed, 309 passed, 59 skipped, 13 xfailed in 11:02
(readiness 5:22). See DEPLOYMENT.md for the rollback procedure.
"""


def judged(tmp_path, capsys, ci_log=CI_LOG):
    d, _ = rendered_runner(tmp_path, capsys)
    ev = d / "launch-20260926-1300"
    ev.mkdir()
    (d / "launch-20260926-1300.status").write_text(STATUS)
    (ev / "app.log").write_text((FIX / "real-app-p3.log").read_text())
    (ev / "cc.log").write_text("       ✓ cc-agent image rebuilt; no persistent container to restart\n"
                               "       ✓ cc-agent runtime: node v22.11.0, Claude Code 2.1.282\n")
    (ev / "checks.log").write_text(CHECKS)
    (ev / "static.log").write_text("804 static files post-processed.\n")
    (ev / "ci.log").write_text(ci_log)
    code, out = run(["judge", "--brief", str(d / "brief.json")], capsys)
    assert code == 0, out.err
    return d


def test_judge_writes_facts_and_a_prefilled_form(tmp_path, capsys):
    d = judged(tmp_path, capsys)
    facts = json.loads((d / "facts.json").read_text())
    v = facts["verdict"]
    assert v["on_expected_sha"] and v["every_image_rebuilt"] and v["unexplained_rebuild_reds"] == 0
    assert v["ci_green_apart_from_known"] is True and v["ci_health_reds"] == []
    assert facts["rebuilds"]["app"]["built_from"] == "1c070c08"
    assert facts["ci"]["counts"] == {"failed": 2, "passed": 309, "skipped": 59, "xfailed": 13}
    form = json.loads((d / "report-form.json").read_text())
    assert form["headline"]["verdict"] is None
    assert [f["known"] for f in form["ci"]["failures"]] == [True, True]


def fill(d, **over):
    form = json.loads((d / "report-form.json").read_text())
    form["headline"] = {"verdict": "shipped", "line": "on 1c070c08, app and cc-agent rebuilt, CI green apart from the 2 known SEEK reds"}
    form["summary"] = ("The dev box is on 1c070c08 with app and cc-agent rebuilt; CI is green apart from the "
                       "two known SEEK reds; no Nessie in the brief.")
    for k, v in over.items():
        form[k] = v
    p = d / "filled-report.json"
    p.write_text(json.dumps(form))
    return p


def review_done(d):
    # a minimal valid commit review for the report step
    cr = {"schema": "launch-commit-review/v1", "range": {"base": "a" * 40, "head": EXP, "total": 1, "listed": 1, "skipped": 0},
          "derived": {}, "commits": [{"sha": "1c070c08", "full": EXP, "subject": "merge", "date": "2026-09-25",
                                      "merge": True, "files": [], "images": [], "flags": []}],
          "summary": "One merge commit, reviewed.", "behaviour_changes": [],
          "no_behaviour_change": [{"commits": ["1c070c08"], "reason": "merge_commit"}], "proposed_checks": [],
          "validated_at": "x", "deliver_to": "SUPERVISOR-TEST"}
    (d / "commit-review.json").write_text(json.dumps(cr))
    (d / "commit-review.md").write_text("# Commit review\n")


def test_report_renders_deterministically(tmp_path, capsys):
    d = judged(tmp_path, capsys)
    review_done(d)
    code, out = run(["report", "--brief", str(d / "brief.json"), "--form", str(fill(d))], capsys)
    assert code == 0, out.err
    md = (d / "LAUNCH-REPORT.md").read_text()
    assert md.startswith("# Launch dev at 1c070c08: on 1c070c08")
    for section in ("## State", "## Steps", "## Live markers", "## CI", "## Nessie questions",
                    "## Findings", "## Proposals", "## Commit review and proposed CI checks"):
        assert section in md
    code, out = run(["report", "--brief", str(d / "brief.json"), "--form", str(fill(d)), "--force"], capsys)
    assert (d / "LAUNCH-REPORT.md").read_text() == md


def test_report_refuses_an_unfilled_form(tmp_path, capsys):
    d = judged(tmp_path, capsys)
    review_done(d)
    code, out = run(["report", "--brief", str(d / "brief.json")], capsys)
    assert code == 2 and "headline.verdict" in out.err


def test_report_refuses_to_hide_a_red(tmp_path, capsys):
    d = judged(tmp_path, capsys)
    review_done(d)
    p = fill(d)
    f = json.loads(p.read_text())
    f["ci"]["failures"] = f["ci"]["failures"][:1]
    p.write_text(json.dumps(f))
    code, out = run(["report", "--brief", str(d / "brief.json"), "--form", str(p)], capsys)
    assert code == 2 and "exactly the failed test ids" in out.err


def test_shipped_is_refused_when_ci_has_a_new_red(tmp_path, capsys):
    d = judged(tmp_path, capsys, ci_log=CI_LOG.replace(
        "FAILED ci/smoke/test_reachability.py::test_route_is_reachable[/nextseek_api/sample_types/{sample_type_id}/]",
        "FAILED ci/smoke/test_deploy_live.py::test_the_served_chat_bundle_is_the_committed_one"))
    review_done(d)
    p = fill(d)
    f = json.loads(p.read_text())
    for x in f["ci"]["failures"]:
        if not x["known"]:
            x.update(first_error="E  404 on the manifest's JS", caused_by_change="yes")
    p.write_text(json.dumps(f))
    code, out = run(["report", "--brief", str(d / "brief.json"), "--form", str(p)], capsys)
    assert code == 2 and "'shipped' is not allowed" in out.err
    f["headline"]["verdict"] = "shipped_with_findings"
    f["findings"] = [{"severity": "high", "text": "collectstatic did not serve the new bundle",
                      "evidence": ["ci.log"]}]
    p.write_text(json.dumps(f))
    code, out = run(["report", "--brief", str(d / "brief.json"), "--form", str(p)], capsys)
    assert code == 0, out.err


def test_parse_nessie_log_derives_per_case_cost_from_the_running_total():
    got = L.parse_nessie_log((FIX / "real-nessie-tail.log").read_text())
    costs = [c["cost_usd"] for c in got["cases"]]
    assert all(c >= 0 for c in costs)
    assert got["gate"].startswith("GATE:")


def test_left_behind_is_prefilled_and_cannot_be_dropped(tmp_path, capsys):
    d = judged(tmp_path, capsys)
    review_done(d)
    p = fill(d)
    f = json.loads(p.read_text())
    kinds = {x["kind"] for x in f["left_behind"]}
    assert {"tmux_session", "box_folder", "box_file", "image_tag", "local_folder"} <= kinds
    assert any(x["name"].startswith("nextseek-nextseek:pre-") for x in f["left_behind"])
    f["left_behind"] = [x for x in f["left_behind"] if x["kind"] != "image_tag"]
    p.write_text(json.dumps(f))
    code, out = run(["report", "--brief", str(d / "brief.json"), "--form", str(p)], capsys)
    assert code == 2 and "was dropped" in out.err


def test_an_evidence_file_that_does_not_exist_is_refused(tmp_path, capsys):
    d = judged(tmp_path, capsys)
    review_done(d)
    p = fill(d, anomalies=[{"severity": "low", "text": "neo4j near its cap",
                            "evidence": ["checks.log", "neo4j-memory.log", "task 1241"]}])
    f = json.loads(p.read_text())
    f["headline"]["verdict"] = "shipped_with_findings"
    p.write_text(json.dumps(f))
    code, out = run(["report", "--brief", str(d / "brief.json"), "--form", str(p)], capsys)
    assert code == 2 and "neo4j-memory.log" in out.err and "checks.log" not in out.err.split("neo4j")[0]


def test_a_step_the_runner_never_reached_points_at_no_evidence(tmp_path, capsys):
    d, _ = rendered_runner(tmp_path, capsys)
    ev = d / "launch-20260926-1300"
    ev.mkdir()
    (d / "launch-20260926-1300.status").write_text(
        "START tag=20260926-1300 instance=dev components=app cc-agent 13:00:01\n"
        "HEAD 1c070c08 Merge 13:00:04\nREBUILD app exit=2 13:05:00\n"
        "STOPPED: app: no 'app rebuilt and restarted' line 13:05:00\nALL_DONE 13:05:00\n")
    (ev / "app.log").write_text("       ✗ stopped before building: disk under the floor\n")
    code, out = run(["judge", "--brief", str(d / "brief.json")], capsys)
    assert code == 0, out.err
    form = json.loads((d / "report-form.json").read_text())
    steps = {s["step"]: s for s in form["steps"]}
    assert steps["app"]["status"] == "failed" and steps["app"]["evidence"] == ["app.log"]
    assert steps["cc-agent"]["status"] == "stopped" and steps["cc-agent"]["evidence"] == []
    assert steps["ci"]["status"] == "stopped" and steps["ci"]["evidence"] == []


def test_the_brief_prints_every_class_of_problem_at_once(tmp_path, capsys):
    f = tmp_path / "b.json"
    f.write_text(json.dumps(brief_form(images=["bedrock-proxy", "app"])))
    code, out = run(["brief", "--form", str(f), "--out-dir", str(tmp_path / "d"),
                     "--now", "2026-09-26T05:10:00Z"], capsys)
    assert code == 5 and "STOP" in out.err and "WINDOW" in out.err
    assert json.loads((tmp_path / "d" / "brief.json").read_text())["problems"]["window"]


def test_a_refused_brief_can_still_be_reported(tmp_path, capsys):
    code, out, d = make_brief(tmp_path, capsys, images=["bedrock-proxy", "app"])
    assert code == 5
    form = json.loads((SKILL / "examples" / "report-stopped.json").read_text())
    form["steps"] = [{"step": "preflight", "status": "skipped", "evidence": [], "note": "the brief was refused"}]
    form["findings"][0]["evidence"] = ["brief.json"]
    p = tmp_path / "rf.json"
    p.write_text(json.dumps(form))
    code, out = run(["report", "--brief", str(d / "brief.json"), "--form", str(p), "--no-facts"], capsys)
    assert code == 0, out.err
    md = (d / "LAUNCH-REPORT.md").read_text()
    assert "## Why the brief was refused" in md and "bedrock-proxy rebuild ok" in md
    assert "the launch stopped before the runner started" in md


def test_a_stale_preflight_blocks_the_start(tmp_path, capsys):
    code, out, d = preflight(tmp_path, capsys, good_preflight(utc="2026-09-26T09:00:00Z"))
    assert code == 0
    code, out = run(["ssh", "--brief", str(d / "brief.json"), "--purpose", "start", "--dry-run",
                     "--now", "2026-09-26T13:00:00Z"], capsys)
    assert code == 5 and "run it again" in out.err


def test_the_stopped_report_shows_the_checkout_from_the_preflight(tmp_path, capsys):
    text = good_preflight().replace("oom=false restarts=0 started=2026-09-14", "oom=true restarts=0 started=2026-09-14")
    code, out, d = preflight(tmp_path, capsys, text)
    assert code == 5
    p = tmp_path / "rf.json"
    form = json.loads((SKILL / "examples" / "report-stopped.json").read_text())
    form["steps"][0]["evidence"] = ["preflight.json", "preflight.out"]
    p.write_text(json.dumps(form))
    code, out = run(["report", "--brief", str(d / "brief.json"), "--form", str(p), "--no-facts"], capsys)
    assert code == 0, out.err
    md = (d / "LAUNCH-REPORT.md").read_text()
    assert "| Checkout | 926be1e3 fix: a thing (not moved; from the preflight) |" in md
    assert "| oom | seek | stop |" in md


def test_the_filled_example_is_valid():
    ex = json.loads((SKILL / "examples" / "commit-review-filled.json").read_text())
    rv = L.CommitReview.model_validate(ex)
    assert rv.range["total"] == 106 and rv.range["listed"] == 50
    assert len(rv.behaviour_changes) >= 10 and rv.proposed_checks


@pytest.mark.parametrize("mutate,ok,needle", [
    (lambda f, shas: f["behaviour_changes"][0].update(covers_skipped=["abcdef12"]), True, ""),
    (lambda f, shas: f["behaviour_changes"][0].update(covers_skipped=[shas[2]]), False, "put them in commits"),
    (lambda f, shas: f["behaviour_changes"][0].update(covers_skipped=["not-a-sha"]), False, "non-sha"),
    (lambda f, shas: f["behaviour_changes"][0].update(status="needs_fault_injection"), True, ""),
])
def test_skipped_commits_and_fault_injection(tmp_path, capsys, mutate, ok, needle):
    code, out, d = commits_setup(tmp_path, capsys, images=["app"])
    p = filled_review(d)
    f = json.loads(p.read_text())
    mutate(f, [c["sha"] for c in f["commits"]])
    p.write_text(json.dumps(f))
    code, out = run(["review", "--brief", str(d / "brief.json"), "--form", str(p)], capsys)
    assert (code == 0) is ok, out.err
    if not ok:
        assert needle in out.err


# --------------------------------------------------------------------------- box config
def test_missing_box_config_names_the_file(tmp_path, monkeypatch, capsys):
    code, out, d = make_brief(tmp_path, capsys)
    assert code == 0, out.err
    missing = tmp_path / "nope.json"
    monkeypatch.setenv("NEXTSEEK_BOXES", str(missing))
    rules.INSTANCES.clear()
    code, out = run(["preflight-script", "--brief", str(d / "brief.json")], capsys)
    assert code == L.EXIT_INVALID and str(missing) in out.err, out.err


def test_absent_instance_key_is_named(tmp_path, monkeypatch):
    bad = json.loads((SKILL / "boxes.example.json").read_text())
    del bad["instances"]["dev"]["run_as"]
    f = tmp_path / "boxes.json"
    f.write_text(json.dumps(bad))
    monkeypatch.setenv("NEXTSEEK_BOXES", str(f))
    rules.INSTANCES.clear()
    with pytest.raises(rules.BoxesConfigError, match="dev.*run_as"):
        rules.INSTANCES["dev"]


@pytest.mark.parametrize("path, images", [
    ("NessieAI/docker/bedrock-proxy/app/main.py", ("bedrock-proxy",)),
    ("NessieAI/docker/bedrock-proxy/Dockerfile", ("bedrock-proxy",)),
    ("NessieAI/docker/ns-sidecar/__init__.py", ("nextseek-sidecar",)),
    ("NessieAI/docker/ns-sidecar/app/contract.py", ("nextseek-sidecar",)),
    ("NessieAI/docker/bedrock-proxy/README.md", ()),
    ("NessieAI/docker/ns-sidecar/README.md", ()),
    ("NessieAI/docker/cc-runtime/docs/nextseek/01-overview.md", ("cc-agent",)),
])
def test_proxy_and_sidecar_rebuild_only_for_their_build_inputs(path, images):
    assert rules.rule_for(path).images == images


# --------------------------------------------------------------------------- the short form
class FakeBox:
    """Stands in for `launch.py ssh`: records every call and plays the box's side."""

    def __init__(self, preflight_text="", fail=None):
        self.calls, self.preflight_text, self.fail = [], preflight_text, fail

    def __call__(self, a):
        self.calls.append(a.purpose)
        if a.purpose == self.fail:
            raise L.Stop(L.EXIT_SSH, [f"the {a.purpose} connection failed"])
        d = Path(a.brief).parent
        if a.purpose == "preflight":
            Path(a.out).write_text(self.preflight_text)
        elif a.purpose == "watch":
            Path(a.out).write_text("ALL_DONE\n")
        elif a.purpose == "pull":
            ev = d / "launch-20260926-1300"
            ev.mkdir(exist_ok=True)
            (d / "launch-20260926-1300.status").write_text(STATUS)
            (ev / "app.log").write_text((FIX / "real-app-p3.log").read_text())
            (ev / "cc.log").write_text("       ✓ cc-agent image rebuilt; no persistent container to restart\n")
            (ev / "checks.log").write_text(CHECKS)
            (ev / "ci.log").write_text(CI_LOG)
        return 0


def _prepare(tmp_path, capsys, monkeypatch, box=None, **brief_over):
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo, base, head = git_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(repo)], check=True)
    box = box or FakeBox()
    if not box.preflight_text:
        box.preflight_text = good_preflight(expected=head, origin=head, head=base)
    monkeypatch.setattr(L, "cmd_ssh", box)
    f = tmp_path / "brief-form.json"
    f.write_text(json.dumps(brief_form(expected_sha=head[:8], **brief_over)))
    d = tmp_path / "launch"
    code, out = run(["prepare", "--form", str(f), "--out-dir", str(d), "--repo", str(repo), "--now", NOW_OK], capsys)
    return code, out, d, box


def test_prepare_runs_brief_to_commits_and_prints_what_comes_next(tmp_path, capsys, monkeypatch):
    code, out, d, box = _prepare(tmp_path, capsys, monkeypatch)
    assert code == 0, out.err
    assert box.calls == ["preflight"]
    assert "READ-ONLY" in (d / "preflight.sh").read_text() and (d / "commit-review-form.json").is_file()
    assert "OK     disk" in out.out and "COMMIT " in out.out and "change 2 to nextseek_api/views.py" in out.out
    assert "ANNOUNCE (say it before you run start): launch 20260926-1300" in out.out and "free" in out.out
    assert "NEXT: fill" in out.out


def test_prepare_stops_at_the_first_refusal_with_its_code(tmp_path, capsys, monkeypatch):
    # a refused brief: exit 5, and no connection at all
    code, out, d, box = _prepare(tmp_path / "a", capsys, monkeypatch, prod_nessie=True)
    assert code == 5 and "contradictory" in out.err and "STOPPED at brief" in out.out
    assert box.calls == []
    # a refused preflight: exit 5, one connection, no commits step
    box = FakeBox(good_preflight(disk_free_gb="3"))
    code, out, d, box = _prepare(tmp_path / "b", capsys, monkeypatch, box=box)
    assert code == 5 and "- disk:" in out.err and box.calls == ["preflight"]
    assert not (d / "commit-review-form.json").exists()
    # a failed connection: exit 7 and nothing after it
    code, out, d, box = _prepare(tmp_path / "c", capsys, monkeypatch, box=FakeBox(fail="preflight"))
    assert code == 7 and not (d / "preflight.json").exists()


def test_start_reviews_renders_and_starts_and_stops_before_the_box_on_a_bad_form(tmp_path, capsys, monkeypatch):
    code, out, d, box = _prepare(tmp_path, capsys, monkeypatch)
    assert code == 0, out.err
    bad = filled_review(d, drop_one=True)
    code, out = run(["start", "--brief", str(d / "brief.json"), "--form", str(bad)], capsys)
    assert code == 2 and "not accounted for" in out.err and box.calls == ["preflight"]
    assert not (d / "runner").exists()
    code, out = run(["start", "--brief", str(d / "brief.json"), "--form", str(filled_review(d))], capsys)
    assert code == 0, out.err
    assert box.calls == ["preflight", "start"]
    assert "DELIVER: SendMessage to 'SUPERVISOR-TEST'" in out.out and "## Behaviour changes" in out.out
    assert "bash -n clean" in out.out


def test_finish_watches_pulls_and_judges_and_stops_after_a_failed_watch(tmp_path, capsys, monkeypatch):
    code, out, d, box = _prepare(tmp_path, capsys, monkeypatch)
    code, out = run(["start", "--brief", str(d / "brief.json"), "--form", str(filled_review(d))], capsys)
    assert code == 0, out.err
    box.fail = "watch"
    code, out = run(["finish", "--brief", str(d / "brief.json")], capsys)
    assert code == 7 and box.calls[-1] == "watch" and not (d / "facts.json").exists()
    box.fail = None
    code, out = run(["finish", "--brief", str(d / "brief.json")], capsys)
    assert code == 0, out.err
    assert box.calls[-2:] == ["watch", "pull"]
    assert "HEAD on expected sha" in out.out and f"FACTS: {d / 'facts.json'}" in out.out


def test_the_watch_exits_0_once_the_runner_is_done_and_1_before(tmp_path, capsys):
    # `finish` stops at the first non-zero exit, so the watch's exit must say whether the runner finished,
    # not whether a Nessie log happens to exist (a stopped or CI-only launch has none)
    d, _ = rendered_runner(tmp_path, capsys)
    home = tmp_path / "home"
    (home / "launch-20260926-1300").mkdir(parents=True)
    st = home / "launch-20260926-1300.status"
    script = tmp_path / "w.sh"
    script.write_text("sleep(){ :; }\n" + re.sub(r"(?m)^H=\S+;", f"H={home};",
                                                 (d / "runner" / "watch.sh").read_text()))
    st.write_text("START x 00:00:01\nSTOPPED: unexplained red after app: x 00:00:02\nALL_DONE 00:00:02\n")
    assert subprocess.run(["bash", str(script)], capture_output=True).returncode == 0
    st.write_text("START x 00:00:01\n")
    assert subprocess.run(["bash", str(script)], capture_output=True).returncode == 1


def test_the_sidecar_op_check_applies_only_on_the_sidecar_road(tmp_path, capsys):
    """Approach 1, piece 2: with NEXTSEEK_CC_OPS_ROAD=direct the op tools never touch the sidecar, so a stale sidecar
    is not a finding; a build without the switch always used the sidecar."""
    _, text = rendered_runner(tmp_path, capsys)
    assert 'echo "CHECK ops_road $road"' in text
    assert "test -f /app/NessieAI/cc/ops_road.py" in text
    assert 'echo "CHECK sidecar_ops skipped"' in text
    assert text.index('if [ "$road" = sidecar ]') < text.index('echo "CHECK sidecar_ops match"')


def test_the_checks_parser_reads_the_road():
    ck = L.parse_checks("CHECK ops_road direct\nCHECK sidecar_ops skipped\n")
    assert (ck["ops_road"], ck["sidecar_ops"]) == ("direct", "skipped")


# --------------------------------------------------------------------------- laya_mode (JevLevROUTING)
@pytest.mark.parametrize("inst", ["dev", "prod"])
def test_the_laya_mode_kv_is_in_the_preflight_script_on_every_instance(tmp_path, capsys, inst):
    code, out, d = make_brief(tmp_path / inst, capsys, instance=inst)
    assert code == 0
    code, out = run(["preflight-script", "--brief", str(d / "brief.json")], capsys)
    assert "NESSIE_LAYA_SHADOW" in out.out and "NESSIE_LAYA_LIVE" in out.out and "KV laya_mode=" in out.out
    p = tmp_path / inst / "pre.sh"
    p.write_text(out.out)
    assert subprocess.run(["bash", "-n", str(p)]).returncode == 0


@pytest.mark.parametrize("mode", ["shadow=0 live=", "shadow=1 live=", "shadow=unset live=", "unknown"])
def test_laya_mode_passes_for_off_shadow_or_absent(tmp_path, capsys, mode):
    code, out, d = preflight(tmp_path, capsys, good_preflight(laya_mode=mode))
    assert code == 0, out.err
    rows = {r["id"]: r for r in json.loads((d / "preflight.json").read_text())["checks"]}
    assert rows["laya_mode"]["verdict"] == "ok"


@pytest.mark.parametrize("mode", ["shadow=0 live=1", "shadow=1 live=true", "shadow=1 live=20261003-abcdef012345"])
def test_laya_live_stops_unless_the_brief_names_that_revision(tmp_path, capsys, mode):
    code, out, d = preflight(tmp_path, capsys, good_preflight(laya_mode=mode))
    assert code == 5 and "- laya_mode:" in out.err


def test_laya_live_passes_when_the_brief_names_the_revision(tmp_path, capsys):
    mode = "shadow=1 live=20261003-abcdef012345"
    code, out, d = preflight(tmp_path, capsys, good_preflight(laya_mode=mode),
                             laya_live_revision="20261003-abcdef012345")
    assert code == 0, out.err
    code, out, d = preflight(tmp_path / "other", capsys, good_preflight(laya_mode=mode),
                             laya_live_revision="20261004-000000000000")
    assert code == 5 and "- laya_mode:" in out.err


@pytest.mark.parametrize("live_line", ['NESSIE_LAYA_LIVE="20261003-abcdef012345" # go',
                                       "NESSIE_LAYA_LIVE= 20261003-abcdef012345 "])
def test_the_laya_kv_drops_an_inline_comment_and_spaces_as_compose_does(tmp_path, capsys, live_line):
    box = tmp_path / "box"
    (box / "docker").mkdir(parents=True)
    (box / "docker" / "nextseek.env").write_text('NESSIE_LAYA_SHADOW="1"\n' + live_line + "\n")
    kv = subprocess.run(["bash", "-c", L.LAYA_KV], cwd=box, capture_output=True, text=True).stdout.strip()
    assert kv == "KV laya_mode=shadow=1 live=20261003-abcdef012345"
    code, out, d = preflight(tmp_path / "brief", capsys, good_preflight(laya_mode=kv[len("KV laya_mode="):]))
    assert code == 5 and "- laya_mode:" in out.err


def test_a_laya_mode_value_the_rule_cannot_read_is_a_stop(tmp_path, capsys):
    code, out, d = preflight(tmp_path, capsys, good_preflight(laya_mode="shadow=1 live=x # go"))
    assert code == 5 and "- laya_mode:" in out.err


@pytest.mark.parametrize("value,verdict", [('"0"', "ok"), ("0", "ok"), ('"1"', "stop"), ("1", "stop")])
def test_the_prod_posterior_row_reads_a_quoted_value_and_a_real_1_still_stops(tmp_path, capsys, value, verdict):
    box = tmp_path / "box"
    (box / "docker").mkdir(parents=True)
    (box / "docker" / "nextseek.env").write_text(f"NEXTSEEK_POSTERIOR_ROUTING_ENABLED={value}\n")
    line = next(x for x in L.PREFLIGHT_EXTRA["prod"].splitlines() if "posterior_routing" in x)
    kv = subprocess.run(["bash", "-c", line], cwd=box, capture_output=True, text=True).stdout.strip()
    assert kv == "KV posterior_routing=" + value.strip('"')
    code, out, d = preflight(tmp_path / "brief", capsys, good_preflight(posterior_routing=kv.split("=", 1)[1]),
                             instance="prod")
    rows = {r["id"]: r for r in json.loads((d / "preflight.json").read_text())["checks"]}
    assert rows["posterior_routing"]["verdict"] == verdict
