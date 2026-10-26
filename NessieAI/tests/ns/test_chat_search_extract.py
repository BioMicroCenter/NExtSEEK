"""The session search's extractors (NessieAI/ns/chat_search_index.py): what a turn put in front of the user."""
import json

from NessieAI.ns.chat_search_index import bundle_result_uids, find_uids, transcript_steps, uid_type


def jsonl(*records) -> bytes:
    return "\n".join(json.dumps(r) for r in records).encode()


def assistant(*blocks):
    return {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}}


def user(*blocks):
    return {"type": "user", "message": {"role": "user", "content": list(blocks)}}


class TestFindUids:
    def test_sample_and_sop_ids_in_order_without_repeats(self):
        text = ("Found TIS-220831FLY-26, D.SEQ-230512FOR-288-PUB and A.GEX-220630FLY-2; TIS-220831FLY-26 again; "
                "protocol P.FLY-220630-V1_dissection.docx")
        assert find_uids(text) == ["TIS-220831FLY-26", "D.SEQ-230512FOR-288-PUB", "A.GEX-220630FLY-2",
                                   "P.FLY-220630-V1_dissection.docx"]

    def test_never_inside_a_longer_token(self):
        assert find_uids("xTIS-220831FLY-26 TIS-220831FLY-26x tis-220831fly-26") == []

    def test_type_is_the_part_before_the_first_dash(self):
        assert [uid_type(u) for u in ("TIS-220831FLY-26", "D.SEQ-230512FOR-288-PUB", "P.FLY-220630-V1_a")] == [
            "TIS", "D.SEQ", "P.FLY"]

    def test_non_text_is_empty(self):
        assert find_uids(None) == [] and find_uids(5) == []


class TestTranscriptSteps:
    def test_sorts_text_tool_calls_and_result_ids(self):
        raw = jsonl(
            user("find NDMA mice"),
            assistant({"type": "thinking", "thinking": ""},
                      {"type": "text", "text": "I will query the graph."},
                      {"type": "tool_use", "name": "Bash", "input": {"command": "nextseek-graph --q 'NDMA'"}}),
            user({"type": "tool_result", "content": [{"type": "text", "text": "MUS-220101ABC-1\nMUS-220101ABC-2"}]}),
            assistant({"type": "tool_use", "name": "Read", "input": {"file_path": "/work/out.csv"}},
                      {"type": "text", "text": "Two mice."}),
            user({"type": "tool_result", "content": "MUS-220101ABC-2 MUS-220101ABC-3"}),
        )
        text, calls, uids = transcript_steps(raw)
        assert text == "I will query the graph.\nTwo mice."
        assert calls == "Bash: nextseek-graph --q 'NDMA'\nRead: /work/out.csv"
        assert uids == ["MUS-220101ABC-1", "MUS-220101ABC-2", "MUS-220101ABC-3"]

    def test_other_tools_keep_their_input_as_json(self):
        raw = jsonl(assistant({"type": "tool_use", "name": "Skill", "input": {"skill": "geo"}}))
        assert transcript_steps(raw)[1] == 'Skill: {"skill": "geo"}'

    def test_secret_lines_dropped(self):
        raw = jsonl(
            assistant({"type": "text", "text": "ok line\nAuthorization: Basic abc\nfine"},
                      {"type": "tool_use", "name": "Bash",
                       "input": {"command": "curl -H 'Authorization: NextseekTurn xyz' http://x"}},
                      {"type": "tool_use", "name": "Bash", "input": {"command": "login password=hunter2"}},
                      {"type": "tool_use", "name": "Bash", "input": {"command": "get ?token=abc&x=1"}},
                      {"type": "tool_use", "name": "Bash", "input": {"command": "export API_KEY=zzz"}},
                      {"type": "tool_use", "name": "Bash", "input": {"command": "echo client_secret=q"}},
                      {"type": "tool_use", "name": "Bash", "input": {"command": "nextseek-graph --q x"}}),
        )
        text, calls, _ = transcript_steps(raw)
        assert text == "ok line\nfine"
        assert calls == "Bash: nextseek-graph --q x"
        for secret in ("abc", "xyz", "hunter2", "zzz", "client_secret"):
            assert secret not in text + calls

    def test_malformed_lines_and_shapes_are_skipped(self):
        raw = b"not json\n" + jsonl({"type": "assistant"}, {"message": {"content": "plain"}},
                                    assistant(None, {"type": "text", "text": 5}, {"type": "text", "text": "kept"}))
        assert transcript_steps(raw) == ("kept", "", [])

    def test_empty(self):
        assert transcript_steps(b"") == ("", "", [])


class TestBundleResultUids:
    def test_reads_the_result_keys_never_the_full_payload(self):
        bundle = {
            "graph_result": {"rows": [{"uid": "TIS-220831FLY-26"}, {"uid": "TIS-220831FLY-27"}]},
            "api_result_slim": [{"id": "MUS-220101ABC-1"}],
            "reporter_result": "see TIS-220831FLY-26",
            "api_result_full": {"rows": [{"uid": "NHP-220630FLY-9"}]},
            "user_query": "about NHP-220630FLY-8",
        }
        assert bundle_result_uids(bundle) == ["TIS-220831FLY-26", "TIS-220831FLY-27", "MUS-220101ABC-1"]

    def test_not_a_dict(self):
        assert bundle_result_uids(None) == [] and bundle_result_uids([1]) == []
