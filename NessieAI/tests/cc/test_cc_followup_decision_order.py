"""One decision order when a follow-up needs a field the rows do not show (CC-RERUN-FINDINGS fix 4).

The container CLAUDE.md gave two routes for the same situation: fetch more metadata by UID
through the retrieve endpoint, and "use nextseek-aggregate when the rows do not hold the
field". Agents split between them, and the aggregate route restates the earlier question from
plain words, which re-derives it and can drop a filter (r4-625 lost its collaborative-cross
filter and answered 1,442). CC also never managed the retrieve call through api-read (4 of 4
fell back to nextseek-sample-search, which answered right each time). A count-only turn keeps no
UIDs, and r6-1231 gave up when nextseek-aggregate failed. These pin one order, the count-only
rule and the fallback, in the container CLAUDE.md and the plugin SKILL.md.
"""
from __future__ import annotations

from NessieAI import paths

CLAUDE_MD = paths.CC_RUNTIME_DIR / "container" / "CLAUDE.md"
SKILL_MD = paths.CC_PLUGIN_DIR / "skills" / "nextseek" / "SKILL.md"


def _section(text: str, heading: str) -> str:
    assert heading in text, heading
    return text.split(heading, 1)[1].split("\n## ", 1)[0]


def _flat(text: str) -> str:
    return " ".join(text.split())


def _follow_ups() -> str:
    return _flat(_section(CLAUDE_MD.read_text(encoding="utf-8"),
                          "## Follow-ups: start from the previous turn"))


def test_the_steps_come_in_one_order():
    follow = _follow_ups()
    order = _flat(follow.split("When the question needs a field the rows do not show", 1)[1])
    marks = ["1. `rows.json` / `rows.csv`", "2. `samples.csv`",
             "3. The stored Cypher with one more column", "4. `nextseek-sample-search --uid",
             "5. `nextseek-aggregate`, only when the stored result was capped"]
    at = [order.find(m) for m in marks]
    assert all(i >= 0 for i in at), dict(zip(marks, at))
    assert at == sorted(at)
    assert "adding <the field> to the RETURN and changing nothing else" in order


def test_samples_csv_is_described_with_the_other_files():
    follow = _follow_ups()
    assert "`samples.csv`: every stored property of the samples those rows name" in follow


def test_the_retrieve_and_aggregate_routes_no_longer_compete():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    follow = _follow_ups()
    assert "call `nextseek-api-read` with the retrieve endpoint" not in follow
    counts = _flat(_section(text, "## Counts and breakdowns"))
    assert "only when the rows do not hold the field the question groups by" not in counts
    assert "only when the stored result was capped" in counts
    assert "`samples.csv`" in counts


def test_a_count_only_turn_keeps_its_filter():
    follow = _follow_ups()
    assert "A count-only turn" in follow
    assert "change only the RETURN and keep every MATCH and WHERE" in follow
    assert "Never rewrite the question from plain words" in follow


def test_a_failed_step_falls_back_to_the_stored_cypher_on_the_graph():
    follow = _follow_ups()
    assert "make your second attempt `nextseek-graph` with the stored Cypher" in follow
    assert "before you give up" in follow


def test_the_scope_sentence_names_the_uid_lookup_it_now_sends_to():
    follow = _follow_ups()
    assert "`nextseek-sample-search` returns only the user's samples" in follow


def test_the_skill_sends_refinements_to_the_same_order():
    skill = _flat(SKILL_MD.read_text(encoding="utf-8"))
    assert "the earlier conditions restated plus the new one" not in skill
    assert "then `samples.csv`" in skill
    assert "keep every MATCH and WHERE" in skill
    stop = _flat(_section(SKILL_MD.read_text(encoding="utf-8"), "## Stop-after-2 rule (load-bearing)"))
    assert "when `nextseek-aggregate` fails or is not available on a follow-up" in stop
