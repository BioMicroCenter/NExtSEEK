import json, os, stat

import pytest

from scripts.laya import draft_heldout, freeze_heldout, split


@pytest.fixture(autouse=True)
def stub_hash(monkeypatch):  # norm_text_hash belongs to U3; stub it here
    import hashlib
    monkeypatch.setattr(draft_heldout, "norm_text_hash",
                        lambda t: hashlib.sha256(" ".join(t.lower().split()).encode()).hexdigest())


def _pool():
    fams = [f"f{i}" for i in range(60)]
    held_f = [f for f in fams if split.heldout_bucket(f, "none")][:3]
    free_f = [f for f in fams if not split.heldout_bucket(f, "none")][:6]
    ents = [f"E{i}" for i in range(60)]
    free_e = [e for e in ents if not split.heldout_bucket("none", e)][:3]
    rows = []
    for i, f in enumerate(held_f + free_f):
        rows.append({"chat_id": f"c{i}", "query": f"Question number {i} about {f}", "history": [],
                     "family": f, "entity": free_e[i % 3], "route": "nextseek_query", "truth_kind": "family",
                     "prompt_seen": False})
    rows.append({"chat_id": "cx", "query": "seen in prompt", "history": [], "family": held_f[0],
                 "entity": free_e[0], "route": "nextseek_query", "truth_kind": "family", "prompt_seen": True})
    # a follow-up in a held-out chat keeps its (free) family but must go with the chat
    rows.append({"chat_id": "c0", "query": "and the follow up?", "history": [{"user_message": "Question number 0"}],
                 "family": free_f[0], "entity": free_e[0], "route": "container_cc", "truth_kind": "family",
                 "prompt_seen": False})
    return rows, held_f, free_f


def _write(tmp_path, rows, name="pool.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def test_draft_is_disjoint_and_drops_prompt_seen(tmp_path):
    rows, held_f, free_f = _pool()
    held, rest = draft_heldout.draft(rows)
    assert {r["family"] for r in held if r["chat_id"] != "c0"} <= set(held_f)
    assert "seen in prompt" not in {r["query"] for r in held}
    assert {r["chat_id"] for r in held}.isdisjoint({r["chat_id"] for r in rest})
    assert not any(split.heldout_bucket(r["family"], r["entity"]) for r in rest)
    assert any(r["query"] == "and the follow up?" for r in held)  # chat travels together
    assert all(len(r["hash"]) == 64 and r["split"] == "heldout" for r in held)


def test_topup_counts_have_no_text(tmp_path):
    rows, _, _ = _pool()
    held, _ = draft_heldout.draft(rows)
    t = draft_heldout.topup(held)
    assert t["need"]["unrelated"] == 25 and t["have"]["nextseek_query"] >= 1
    assert "Question number" not in json.dumps(t)


def test_freeze_writes_0444_manifest_without_text_and_refuses_overwrite(tmp_path, capsys):
    rows, _, _ = _pool()
    held, _ = draft_heldout.draft(rows)
    dp = _write(tmp_path, held, "draft.jsonl")
    out = tmp_path / "heldout"
    sha = freeze_heldout.freeze(dp, out)
    jl, man = out / "heldout-v1.jsonl", out / "heldout-v1.manifest.jsonl"
    assert stat.S_IMODE(os.stat(jl).st_mode) == 0o444 and stat.S_IMODE(os.stat(man).st_mode) == 0o444
    assert sha in capsys.readouterr().out
    text = man.read_text()
    assert "Question number" not in text and "follow up" not in text
    assert set(json.loads(text.splitlines()[0])) == {"hash", "split", "family", "entity", "route", "truth_kind"}
    assert split.load_manifest(man) == {r["hash"] for r in held}
    with pytest.raises(FileExistsError):
        freeze_heldout.freeze(dp, out)


def test_freeze_refuses_a_folder_inside_a_git_repo(tmp_path):
    rows, _, _ = _pool()
    held, _ = draft_heldout.draft(rows)
    dp = _write(tmp_path, held, "draft.jsonl")
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    with pytest.raises(ValueError):
        freeze_heldout.freeze(dp, tmp_path / "repo" / "heldout")


def test_freeze_refuses_a_bad_route(tmp_path):
    rows, _, _ = _pool()
    held, _ = draft_heldout.draft(rows)
    held[0]["route"] = "bogus"
    with pytest.raises(ValueError):
        freeze_heldout.freeze(_write(tmp_path, held, "d.jsonl"), tmp_path / "o")
