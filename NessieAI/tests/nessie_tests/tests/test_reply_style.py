"""The reply-style scorer, against replies this project really sent.

Every string below is verbatim from the 2026-09-21 re-run, which is the run the
markers were written from: the measurement and the thing measured must not drift
apart, so the examples are the evidence rather than invented prose.

The scorer is a marker counter, not a judge. What these tests pin is that it
counts what it claims: the debug block never reaches a score, a clean answer
fires nothing, and a trailing machinery clause is counted as a clause rather than
swallowing its whole sentence (which called a 19-word reply 100% machinery).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "output-skill" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_nessie_skill_{name}", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


reply_style = _load("reply_style")

# The clearest offender of the run: 8 words of answer, 33 of machinery.
_IMPACT = (
    "There are 2,640 human Patient (PAT) samples in the Impact project.\n\n"
    "This count was determined by a graph query over the sample network, constrained by the "
    "sample type PAT (Patient), the project Impact, and the keywords 'Impact' and 'human'."
)
# A trailing clause instead of a second sentence.
_KAMM = "The Kamm lab (KAM) has **140** RNA samples (RNA Sample), based on a graph query over the sample network."


def test_the_debug_block_is_not_part_of_the_reply():
    body = reply_style.reply_body("There are 4 HeLa samples.\n\n**Debug info**\n\n```json\n{\"a\": 1}\n```")

    assert body == "There are 4 HeLa samples."
    assert "json" not in body


def test_an_empty_reply_scores_nothing_rather_than_raising():
    assert reply_style.reply_body(None) == ""
    assert reply_style.score_reply(None)["words"] == 0


def test_the_machinery_sentence_is_caught_and_the_answer_is_not():
    score = reply_style.score_reply(_IMPACT)

    assert score["search_machinery"] and score["constrained_by"]
    assert not score["opens_with_machinery"], "the first sentence of this one IS the answer"
    assert 0.5 < score["machinery_share"] < 0.9, score["machinery_share"]


def test_a_trailing_clause_does_not_make_the_whole_sentence_machinery():
    score = reply_style.score_reply(_KAMM)

    assert score["search_machinery"]
    assert score["machinery_words"] < score["words"] / 2, "counted by clause, not by sentence"


def test_a_type_written_both_ways_is_caught_in_either_order():
    assert reply_style.score_reply("There are 140 RNA samples (RNA Sample).")["code_and_name"]
    assert reply_style.score_reply("There are 890 Mass Spectrometry Data (D.MSP) samples.")["code_and_name"]
    assert reply_style.score_reply("D.MSP (Mass Spectrometry Data) holds 890.")["code_and_name"]


def test_a_clean_answer_fires_no_marker():
    score = reply_style.score_reply("The Kamm lab has 140 RNA samples. Four are from 2024.")

    assert not any(score[name] for name in reply_style.MARKERS)
    assert score["machinery_words"] == 0


def test_an_offer_to_re_run_the_search_is_a_marker():
    score = reply_style.score_reply(
        "No mouse samples match. You might try searching for specific Collaborative Cross "
        "strain designations (such as CC001)."
    )

    assert score["offers_to_rerun"]


def test_only_the_nextseek_replies_are_scored_unless_every_route_is_asked_for():
    turns = [
        {"id": 1, "route": "nextseek_query", "reply": "There are 4 HeLa samples."},
        {"id": 2, "route": "container_cc", "reply": "The Shoulders project contains 568 samples."},
        {"id": 3, "route": "nextseek_query", "reply": ""},
    ]

    assert [row["id"] for row in reply_style.score_run(turns)] == [1]
    assert [row["id"] for row in reply_style.score_run(turns, every_route=True)] == [1, 2]


def test_the_report_names_every_marker_and_survives_an_empty_run():
    rows = reply_style.score_run([{"id": 1, "route": "nextseek_query", "reply": _IMPACT}])
    text = reply_style.report(rows, worst=1)

    for name in reply_style.MARKERS:
        assert name in text
    assert "1 replies scored" in text
    assert reply_style.report([]) == "no replies scored"


def test_it_reads_a_run_review_directory_or_a_turns_file(tmp_path):
    (tmp_path / "turns.json").write_text(
        json.dumps([{"id": 1, "route": "nextseek_query", "reply": _KAMM}]), encoding="utf-8")

    assert reply_style.load_turns(tmp_path) == reply_style.load_turns(tmp_path / "turns.json")
    assert reply_style.main([str(tmp_path)]) == 0
