"""``manage.py scrub_stored_sample_properties``: two derived sample properties leave stored graph results, nothing else moves."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from NessieAI.cc.cc_engine import _write_scrub_manifest as write_scrub_manifest
from NessieAI.cc.cc_engine import transcript_is_verified_scrubbed
from NessieAI.cc.cc_summary import fingerprint
from NessieAI.cc.cc_transcript_store import compress, decompress
from nextseek_api.assistant.models_db import CCSessionTranscript, ChatSession, QueryTask
from nextseek_api.management.commands import scrub_stored_sample_properties as scrub

NODE = {"uid": "A-1", "title": "Sample A", "parent_titles": ["Parent P"], "parent_title_hashes": ["abc123"]}
CLEAN_NODE = {"uid": "A-1", "title": "Sample A"}
ROW = {"s": NODE}
CLEAN_ROW = {"s": CLEAN_NODE}
TABLE = {"type": "table", "columns": ["s", "s.parent_titles"], "rows": [[NODE, ["Parent P"]]], "total_rows": 1}
CLEAN_TABLE = {"type": "table", "columns": ["s"], "rows": [[CLEAN_NODE]], "total_rows": 1}


def _transcript(result: dict) -> str:
    """Two Claude Code transcript lines: a tool result holding the op's JSON output, then a line without it."""
    tool = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": json.dumps({"result": result})}]}}
    other = {"type": "assistant", "message": {"content": [{"type": "text", "text": "café   done"}]}}
    return (json.dumps(tool, separators=(",", ":")) + "\n"
            + json.dumps(other, separators=(",", ":"), ensure_ascii=False) + "\n")


def _csv(rows: list[list[str]]) -> str:
    buf = io.StringIO(newline="")
    csv.writer(buf).writerows(rows)
    return buf.getvalue()


ROWS_CSV = _csv([["s", "parent_titles"], [json.dumps(NODE), json.dumps(["Parent P"])]])
CLEAN_ROWS_CSV = _csv([["s"], [json.dumps(CLEAN_NODE)]])


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buf.getvalue()


def _clean(value):
    count = scrub.Count()
    return scrub.scrub_value(value, count), count


# ---------------------------------------------------------------------------------------------- the pure cleaners


def test_the_two_names_are_the_graph_scopes_hidden_sample_properties():
    assert scrub.NAMES == ("parent_title_hashes", "parent_titles")


def test_both_keys_leave_at_every_depth_and_nothing_else_does():
    value = {"rows": [{"s": dict(NODE, Treatment="x")}],
             "nested": [[{"PARENT_TITLES": 1, "keep": [1, 2.5, None, True, "text"]}]],
             "parent_titles": "top",
             "cypher": "MATCH (s) RETURN s.parent_titles"}
    cleaned, count = _clean(value)
    assert cleaned == {"rows": [{"s": dict(CLEAN_NODE, Treatment="x")}],
                       "nested": [[{"keep": [1, 2.5, None, True, "text"]}]],
                       "cypher": "MATCH (s) RETURN s.parent_titles"}
    assert (count.removed, count.left) == (4, 1)


def test_the_column_neo4j_names_for_a_returned_property_is_the_property():
    cleaned, count = _clean([{"s.parent_title_hashes": ["h"], "s.uid": "A-1"}])
    assert cleaned == [{"s.uid": "A-1"}]
    assert count.removed == 1


def test_a_clean_value_comes_back_as_the_same_object():
    value = {"rows": [CLEAN_ROW], "raw": json.dumps({"rows": [CLEAN_ROW]}), "n": 3}
    cleaned, count = _clean(value)
    assert cleaned is value
    assert (count.removed, count.left) == (0, 0)


def test_json_held_as_text_is_cleaned_and_written_back_in_its_own_layout():
    pretty = json.dumps({"rows": [ROW]}, indent=2) + "\n"
    compact = json.dumps({"rows": [ROW]}, separators=(",", ":"))
    cleaned, count = _clean({"pretty": pretty, "compact": compact})
    assert cleaned == {"pretty": json.dumps({"rows": [CLEAN_ROW]}, indent=2) + "\n",
                       "compact": json.dumps({"rows": [CLEAN_ROW]}, separators=(",", ":"))}
    assert count.removed == 4


def test_a_table_loses_the_column_header_and_its_cells():
    cleaned, count = _clean(TABLE)
    assert cleaned == CLEAN_TABLE
    assert count.removed == 4  # the header, its one cell, and the two keys inside the node cell


def test_a_table_whose_rows_do_not_line_up_is_left_and_counted():
    table = {"columns": ["uid", "parent_titles"], "rows": [["A-1"]]}
    cleaned, count = _clean(table)
    assert cleaned is table
    assert (count.removed, count.left) == (0, 1)


def test_cut_off_json_text_loses_every_whole_pair_and_counts_what_is_left():
    text = ('{"rows": [{"uid": "A-1", "parent_titles": ["P"], "n": 1}, '
            '{"parent_title_hashes": ["h"], "uid": "A-2"}, {"uid": "A-3", "parent_titles": ["Q')
    count = scrub.Count()
    cleaned = scrub.scrub_text(text, count)
    assert cleaned == '{"rows": [{"uid": "A-1", "n": 1}, {"uid": "A-2"}, {"uid": "A-3", "parent_titles": ["Q'
    assert (count.removed, count.left) == (2, 1)


def test_json_lines_keep_every_untouched_line_byte_for_byte():
    original = _transcript({"data": [ROW]})
    count = scrub.Count()
    cleaned = scrub.scrub_bytes(original.encode("utf-8"), "text", count).decode("utf-8")
    assert cleaned == _transcript({"data": [CLEAN_ROW]})
    assert cleaned.split("\n")[1] == original.split("\n")[1]
    assert count.removed == 2


def test_csv_loses_the_column_and_json_cells_are_cleaned():
    count = scrub.Count()
    cleaned = scrub.scrub_bytes(ROWS_CSV.encode(), "csv", count)
    assert cleaned.decode() == CLEAN_ROWS_CSV
    assert count.removed == 4  # the header, its one cell, and the two keys inside the JSON cell


def test_a_zip_member_is_cleaned_and_the_other_members_are_kept():
    notes = b"nothing to see"
    original = _zip({"rows.json": json.dumps({"rows": [ROW]}, indent=2).encode(), "notes.txt": notes})
    count = scrub.Count()
    cleaned = scrub.scrub_bytes(original, "zip", count)
    with zipfile.ZipFile(io.BytesIO(cleaned)) as archive:
        assert archive.namelist() == ["rows.json", "notes.txt"]
        assert json.loads(archive.read("rows.json")) == {"rows": [CLEAN_ROW]}
        assert archive.read("notes.txt") == notes
    assert count.removed == 2


def test_a_signed_thinking_block_is_never_edited_and_is_counted():
    block = {"type": "thinking", "thinking": 'row has "parent_titles": ["P"], so', "signature": "sig"}
    value = {"content": [block, {"type": "text", "text": json.dumps({"parent_titles": ["P"], "uid": "A-1"})}]}
    cleaned, count = _clean(value)
    assert cleaned["content"][0] is block
    assert json.loads(cleaned["content"][1]["text"]) == {"uid": "A-1"}
    assert (count.removed, count.left) == (1, 1)


def test_every_positional_sibling_of_the_header_loses_the_cell():
    table = {"columns": ["uid", "s.parent_titles"], "data": [["A-1", ["P"]]], "rows": [["A-2", ["Q"]]]}
    cleaned, count = _clean(table)
    assert cleaned == {"columns": ["uid"], "data": [["A-1"]], "rows": [["A-2"]]}


def test_text_is_read_as_json_lines_only_when_its_lines_are_json():
    text = '{"step": 1}\n{\n  "a": 1,\n  "parent_titles": ["P"]\n}'
    count = scrub.Count()
    cleaned = scrub.scrub_text(text, count)
    assert cleaned == '{"step": 1}\n{\n  "a": 1\n}'
    assert count.removed == 1


def test_a_csv_cell_longer_than_the_csv_default_limit_is_read():
    big = json.dumps(dict(NODE, notes="x" * 200_000))
    text = _csv([["s"], [big]])
    count = scrub.Count()
    cleaned = scrub.scrub_bytes(text.encode(), "csv", count).decode()
    assert json.loads(list(csv.reader(io.StringIO(cleaned, newline="")))[1][0]) == dict(CLEAN_NODE, notes="x" * 200_000)


def test_indented_json_with_crlf_keeps_crlf():
    original = json.dumps({"rows": [ROW]}, indent=2).replace("\n", "\r\n")
    count = scrub.Count()
    assert scrub.scrub_text(original, count) == json.dumps({"rows": [CLEAN_ROW]}, indent=2).replace("\n", "\r\n")


def test_json_that_cannot_be_written_back_strictly_is_left_and_counted():
    original = '{"n": 1e400, "parent_titles": ["P"]}'
    count = scrub.Count()
    assert scrub.scrub_text(original, count) is original
    assert (count.removed, count.left) == (0, 1)


def test_a_workbook_is_only_checked_even_inside_a_zip():
    workbook = _zip({"xl/sharedStrings.xml": b"<sst><si><t>parent_titles</t></si></sst>"})
    count = scrub.Count()
    assert scrub.scrub_bytes(workbook, "xlsx", count) is workbook
    assert (count.removed, count.left) == (0, 1)
    archive = _zip({"report.xlsx": workbook})
    count = scrub.Count()
    assert scrub.scrub_bytes(archive, "zip", count) is archive
    assert (count.removed, count.left) == (0, 1)


def test_a_file_without_the_names_is_returned_untouched():
    data = json.dumps({"rows": [CLEAN_ROW]}, indent=2).encode()
    count = scrub.Count()
    assert scrub.scrub_bytes(data, "text", count) is data


# ---------------------------------------------------------------------------------------------- the command


class ScrubStoredSampleProperties(TestCase):
    databases = {"default"}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scrub-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.outputs = self.tmp / "outputs"
        self.cc_root = self.tmp / "cc"
        self.backups = self.tmp / "backups"
        env = mock.patch.dict(os.environ, {"NEXTSEEK_OUTPUTS_DIR": str(self.outputs),
                                           "DMAC_USER_ROOT_MOUNT": str(self.cc_root)})
        env.start()
        self.addCleanup(env.stop)

        self.member = User.objects.create_user("member", password="p")
        self.superuser = User.objects.create_user("root-admin", password="p", is_superuser=True)
        self.member_session = self._session(self.member, "260922_100000_member")
        self.admin_session = self._session(self.superuser, "260922_110000_admin")
        self.clean_session = ChatSession.objects.create(
            user=self.member, title="kept",
            results_history=[{"id": 1, "graph_result": {"data": [CLEAN_ROW]}}])
        self.before = self._snapshot()

    # -- fixtures ---------------------------------------------------------------------------------------------

    def _session(self, user, run_name):
        log_dir = self.outputs / run_name / "files"
        rows_file = log_dir / "graph_result" / "graph_result_bundle_1.json"
        rows_file.parent.mkdir(parents=True)
        rows_file.write_text(json.dumps({"cypher": "MATCH (s) RETURN s", "rows": [ROW]}, indent=2), encoding="utf-8")
        debug_file = log_dir / "graph" / "graph_debug_1.json"
        debug_file.parent.mkdir()
        debug_file.write_text(json.dumps({"data_preview": [CLEAN_ROW]}, indent=2), encoding="utf-8")
        session = ChatSession.objects.create(
            user=user, title="t",
            results_history=[{"id": 1, "mode": "graph_query", "graph_result": {"ok": True, "data": [ROW]},
                              "files": [{"key": "graph_result", "path": str(rows_file), "kind": "graph_result"}],
                              "graph_debug_path": str(debug_file)}],
            last_debug={"graph_result": {"count": 1}, "raw": json.dumps({"rows": [ROW]}, indent=2)},
            extra_state={"log_dir": str(log_dir), "chat_log": [{"turn_id": 1, "bundle_id": 1}]},
        )
        QueryTask.objects.create(session=session, user=user, query="q", status="completed",
                                 progress=[{"event": "query_complete", "data": {"artifacts": [TABLE]}}],
                                 result={"reply": "r", "artifacts": [TABLE]})
        jsonl = _transcript({"data": [ROW]}).encode("utf-8")
        ChatSession.objects.filter(pk=session.pk).update(
            extra_state=dict(session.extra_state, summary_fingerprint=fingerprint(jsonl)))
        session.refresh_from_db()
        CCSessionTranscript.objects.create(chat_session=session, cc_session_id="cc-1", turn_id="t1",
                                           blob=compress(jsonl), uncompressed_size=len(jsonl))

        user_dir = self.cc_root / "2-proj" / user.username
        turn = user_dir / "_memory" / str(session.session_id) / "previous_turns" / "turn-01"
        turn.mkdir(parents=True)
        (turn / "rows.json").write_text(json.dumps({"rows": [ROW]}, indent=1, ensure_ascii=False) + "\n",
                                        encoding="utf-8")
        (turn / "rows.csv").write_bytes(ROWS_CSV.encode())
        (turn / "search_details.json").write_text(json.dumps({"graph": {"cypher": "MATCH (s) RETURN s"}}))
        artifacts = user_dir / "output" / "artifacts" / "run-1"
        artifacts.mkdir(parents=True)
        (artifacts / "result.json").write_text(json.dumps({"result": {"data": [ROW]}}))
        (artifacts / "artifacts.zip").write_bytes(_zip({"result.json": json.dumps({"data": [ROW]}).encode()}))
        project = user_dir / "cc-state" / str(session.session_id) / "projects" / "-home-user"
        (project / "cc-1" / "tool-results").mkdir(parents=True)
        (project / "cc-1.jsonl").write_bytes(jsonl)
        (project / "cc-1" / "tool-results" / "b1.txt").write_text(json.dumps({"result": {"data": [ROW]}}))
        (user_dir / "cc-state" / str(session.session_id) / "settings.json").write_text('{"model": "x"}')
        history = user_dir / "cc-state" / str(session.session_id) / "file-history" / "e1"
        history.mkdir(parents=True)
        (history / "rows.json").write_text(json.dumps({"rows": [ROW]}))
        write_scrub_manifest(user_dir / "cc-state" / str(session.session_id),
                             {"projects/-home-user/cc-1.jsonl": scrub.sha256(jsonl)})
        an_hour_ago = time.time() - 3600
        for path in (user_dir / "cc-state").rglob("*"):
            os.utime(path, (an_hour_ago, an_hour_ago))
        return session

    def _snapshot(self):
        db = {}
        for s in ChatSession.objects.order_by("pk"):
            db[("session", str(s.pk))] = (s.results_history, s.last_debug, s.extra_state, s.updated_at, s.title)
        for t in QueryTask.objects.order_by("pk"):
            db[("task", t.pk)] = (t.progress, t.result, t.updated_at)
        for r in CCSessionTranscript.objects.order_by("pk"):
            db[("transcript", r.pk)] = (bytes(r.blob), r.uncompressed_size)
        files = {}
        for path in sorted(self.tmp.rglob("*")):
            if path.is_file() and self.backups not in path.parents:
                files[str(path.relative_to(self.tmp))] = (path.read_bytes(), path.stat().st_mtime_ns)
        return db, files

    def _run(self, *args):
        out = StringIO()
        call_command("scrub_stored_sample_properties", *args, stdout=out)
        return out.getvalue()

    def _apply(self, *args):
        return self._run("--apply", "--backup-dir", str(self.backups), *args)

    @staticmethod
    def _counts(out, store, who):
        """(scanned, affected, keys, left in text) from the store's block of the report."""
        block = out.split(f"\n{store} (", 1)[1].split("\n\n", 1)[0]
        match = re.search(rf"^  {who}\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$", block, re.M)
        return tuple(int(n) for n in match.groups())

    def _names_in(self, value) -> bool:
        text = value.decode("utf-8", "replace") if isinstance(value, bytes) else json.dumps(value)
        return "parent_title" in text

    # -- tests ------------------------------------------------------------------------------------------------

    def test_a_dry_run_counts_both_owners_and_writes_nothing(self):
        out = self._run()
        self.assertEqual(self._snapshot(), self.before)
        self.assertIn("DRY RUN", out)
        self.assertEqual(self._counts(out, "sessions", "non-admin")[:2], (2, 1))
        self.assertEqual(self._counts(out, "sessions", "admin")[:2], (1, 1))
        for store, affected in (("tasks", 1), ("cc_transcripts", 1), ("ns_files", 1), ("cc_previous_turns", 2),
                                ("cc_artifacts", 2), ("cc_transcript_files", 3)):
            self.assertEqual(self._counts(out, store, "non-admin")[1], affected, store)
            self.assertEqual(self._counts(out, store, "admin")[1], affected, store)
        self.assertIn(str(self.member_session.pk), out)

    def test_apply_refuses_without_a_backup_dir(self):
        with self.assertRaises(CommandError):
            self._run("--apply")
        self.assertEqual(self._snapshot(), self.before)

    def test_the_backup_dir_may_not_sit_inside_a_scanned_root(self):
        with self.assertRaises(CommandError):
            self._run("--apply", "--backup-dir", str(self.outputs / "backup"))
        self.assertEqual(self._snapshot(), self.before)

    def test_apply_removes_the_two_keys_from_every_store_and_nothing_else(self):
        self._apply()
        db, files = self._snapshot()
        before_db, before_files = self.before

        session = ChatSession.objects.get(pk=self.member_session.pk)
        self.assertEqual(session.results_history[0]["graph_result"]["data"], [CLEAN_ROW])
        self.assertEqual(session.results_history[0]["files"], self.member_session.results_history[0]["files"])
        self.assertEqual(session.last_debug, {"graph_result": {"count": 1},
                                              "raw": json.dumps({"rows": [CLEAN_ROW]}, indent=2)})
        cleaned_jsonl = _transcript({"data": [CLEAN_ROW]}).encode("utf-8")
        self.assertEqual(session.extra_state,
                         dict(self.member_session.extra_state, summary_fingerprint=fingerprint(cleaned_jsonl)))
        self.assertEqual(session.updated_at, before_db[("session", str(session.pk))][3])

        task = QueryTask.objects.get(session=self.member_session)
        self.assertEqual(task.result, {"reply": "r", "artifacts": [CLEAN_TABLE]})
        self.assertEqual(task.progress, [{"event": "query_complete", "data": {"artifacts": [CLEAN_TABLE]}}])

        row = CCSessionTranscript.objects.get(chat_session=self.member_session)
        jsonl = decompress(bytes(row.blob))
        self.assertEqual(jsonl.decode("utf-8"), _transcript({"data": [CLEAN_ROW]}))
        self.assertEqual(row.uncompressed_size, len(jsonl))

        member_run = "outputs/260922_100000_member/files/"
        self.assertEqual(files[member_run + "graph_result/graph_result_bundle_1.json"][0].decode(),
                         json.dumps({"cypher": "MATCH (s) RETURN s", "rows": [CLEAN_ROW]}, indent=2))
        cc = f"cc/2-proj/member/_memory/{self.member_session.pk}/previous_turns/turn-01/"
        self.assertEqual(files[cc + "rows.json"][0].decode(),
                         json.dumps({"rows": [CLEAN_ROW]}, indent=1, ensure_ascii=False) + "\n")
        self.assertEqual(files[cc + "rows.csv"][0].decode(), CLEAN_ROWS_CSV)
        art = "cc/2-proj/member/output/artifacts/run-1/"
        self.assertEqual(json.loads(files[art + "result.json"][0]), {"result": {"data": [CLEAN_ROW]}})
        with zipfile.ZipFile(io.BytesIO(files[art + "artifacts.zip"][0])) as archive:
            self.assertEqual(json.loads(archive.read("result.json")), {"data": [CLEAN_ROW]})
        state = f"cc/2-proj/member/cc-state/{self.member_session.pk}/projects/-home-user/"
        self.assertEqual(files[state + "cc-1.jsonl"][0].decode(), _transcript({"data": [CLEAN_ROW]}))
        self.assertEqual(json.loads(files[state + "cc-1/tool-results/b1.txt"][0]), {"result": {"data": [CLEAN_ROW]}})
        self.assertTrue(transcript_is_verified_scrubbed(self.tmp / (state + "cc-1.jsonl"), cleaned_jsonl))
        history = f"cc/2-proj/member/cc-state/{self.member_session.pk}/file-history/e1/rows.json"
        self.assertEqual(json.loads(files[history][0]), {"rows": [CLEAN_ROW]})

        for name, (data, mtime) in files.items():
            self.assertEqual(mtime, before_files[name][1], f"{name} kept its modification time")
            member_owned = name.startswith(("cc/2-proj/member/", "outputs/260922_100000_member/"))
            if member_owned:
                self.assertFalse(self._names_in(data), name)
            else:
                self.assertEqual(data, before_files[name][0], f"{name} is byte-identical")
        member_keys = {("session", str(self.member_session.pk)),
                       ("task", QueryTask.objects.get(session=self.member_session).pk),
                       ("transcript", CCSessionTranscript.objects.get(chat_session=self.member_session).pk)}
        for key, value in before_db.items():
            if key not in member_keys:
                self.assertEqual(db[key], value, f"{key} is untouched")

    def test_the_backup_holds_every_original_before_the_first_change(self):
        at_first_write = []

        def stop(store, entry):
            if not at_first_write:
                [backup] = list(self.backups.iterdir())
                at_first_write.append((backup, [json.loads(line) for line in backup.read_text().splitlines()]))
            raise RuntimeError("stopped before any change")

        with mock.patch.object(scrub, "apply_entry", side_effect=stop):
            out = self._apply()
        self.assertEqual(self._snapshot(), self.before)
        self.assertRegex(out, r"sessions: 0 written, 0 changed since the scan \(left as they are; run again\), "
                              r"0 gone, 2 failed")
        backup, entries = at_first_write[0]
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(entries[0]["kind"], "header")
        found = sorted((e["store"], e.get("field") or Path(e["path"]).name) for e in entries[1:])
        self.assertEqual(found, sorted([
            ("sessions", "results_history"), ("sessions", "last_debug"), ("tasks", "result"), ("tasks", "progress"),
            ("cc_transcripts", "blob"), ("ns_files", "graph_result_bundle_1.json"),
            ("cc_previous_turns", "rows.json"), ("cc_previous_turns", "rows.csv"),
            ("cc_artifacts", "result.json"), ("cc_artifacts", "artifacts.zip"),
            ("cc_transcript_files", "cc-1.jsonl"), ("cc_transcript_files", "b1.txt"),
            ("cc_transcript_files", "rows.json"),
        ]))
        by_field = {(e["store"], e.get("field")): e for e in entries[1:]}
        self.assertEqual(by_field[("sessions", "results_history")]["value"], self.member_session.results_history)
        self.assertEqual(by_field[("sessions", "results_history")]["pk"], str(self.member_session.pk))
        for entry in entries[1:]:
            if entry["kind"] == "file":
                self.assertEqual(scrub.b64decode(entry["value_b64"]), Path(entry["path"]).read_bytes())

    def test_a_second_apply_finds_nothing(self):
        self._apply()
        out = self._apply()
        for store in scrub.STORE_ORDER:
            self.assertEqual(self._counts(out, store, "non-admin")[1], 0, store)
        self.assertIn("in scope: 0 records", out)

    def test_superusers_records_are_kept_by_default_and_included_on_request(self):
        self._apply()
        admin = ChatSession.objects.get(pk=self.admin_session.pk)
        self.assertEqual(admin.results_history, self.admin_session.results_history)
        self.assertTrue(self._names_in((self.cc_root / "2-proj/root-admin/output/artifacts/run-1/result.json").read_bytes()))

        self._apply("--include-admins")
        admin = ChatSession.objects.get(pk=self.admin_session.pk)
        self.assertEqual(admin.results_history[0]["graph_result"]["data"], [CLEAN_ROW])
        _, files = self._snapshot()
        for name, (data, _) in files.items():
            self.assertFalse(self._names_in(data), name)

    def test_restore_puts_every_original_back(self):
        self._apply()
        [backup] = list(self.backups.iterdir())
        scrubbed = self._snapshot()
        out = self._run("--restore", str(backup))
        self.assertEqual(self._snapshot(), scrubbed)
        self.assertIn("DRY RUN", out)
        self._run("--restore", str(backup), "--apply")
        self.assertEqual(self._snapshot(), self.before)

    def test_store_and_limit_narrow_a_trial(self):
        out = self._apply("--store", "sessions", "--limit", "1")
        self.assertNotIn("\ntasks (", out)
        session = ChatSession.objects.get(pk=self.member_session.pk)
        self.assertEqual(session.results_history[0]["graph_result"]["data"], [CLEAN_ROW])
        db, files = self._snapshot()
        self.assertEqual(files, self.before[1])
        task = QueryTask.objects.get(session=self.member_session)
        self.assertEqual(task.result["artifacts"], [TABLE])

    def test_a_record_changed_after_the_scan_is_left_for_the_next_run(self):
        real = scrub.apply_entry

        def racing(store, entry):
            if entry.get("field") == "last_debug":
                ChatSession.objects.filter(pk=self.member_session.pk).update(
                    last_debug={"new": True, "parent_titles": ["x"]})
            return real(store, entry)

        with mock.patch.object(scrub, "apply_entry", side_effect=racing):
            out = self._apply()
        session = ChatSession.objects.get(pk=self.member_session.pk)
        self.assertEqual(session.last_debug, {"new": True, "parent_titles": ["x"]})
        self.assertEqual(session.results_history[0]["graph_result"]["data"], [CLEAN_ROW])
        self.assertRegex(out, r"sessions: 1 written, 1 changed since the scan")

    def test_files_outside_the_artifact_roots_and_links_are_never_read_or_written(self):
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        outside = elsewhere / "graph_result_bundle_9.json"
        outside.write_text(json.dumps({"rows": [ROW]}))
        linked = elsewhere / "linked.json"
        linked.write_text(json.dumps({"rows": [ROW]}))
        (self.cc_root / "2-proj/member/output/artifacts/run-1/link.json").symlink_to(linked)
        (self.outputs / "260922_100000_member/files/link.json").symlink_to(linked)
        session = ChatSession.objects.get(pk=self.member_session.pk)
        session.results_history[0]["files"].append({"key": "extra", "path": str(outside)})
        ChatSession.objects.filter(pk=session.pk).update(results_history=session.results_history)
        before = (outside.read_bytes(), linked.read_bytes())

        out = self._apply()
        self.assertEqual((outside.read_bytes(), linked.read_bytes()), before)
        self.assertIn("outside_the_artifact_roots", out)
        self.assertTrue((self.cc_root / "2-proj/member/output/artifacts/run-1/link.json").is_symlink())

    def test_a_transcript_written_in_the_last_minutes_is_left_for_later(self):
        live = self.cc_root / f"2-proj/member/cc-state/{self.member_session.pk}/projects/-home-user/cc-1.jsonl"
        os.utime(live, None)
        before = live.read_bytes()
        out = self._apply()
        self.assertEqual(live.read_bytes(), before)
        self.assertIn("recently_modified 1", out)
        out = self._apply("--min-transcript-age-minutes", "0")
        self.assertFalse(self._names_in(live.read_bytes()))

    def test_one_record_that_cannot_be_written_does_not_stop_the_rest(self):
        real = scrub._replace_file

        def refuse_one(path, *args, **kwargs):
            if path.name == "result.json":
                raise PermissionError("read-only")
            return real(path, *args, **kwargs)

        with mock.patch.object(scrub, "_replace_file", side_effect=refuse_one):
            out = self._apply()
        self.assertRegex(out, r"cc_artifacts: 1 written, 0 changed since the scan \(left as they are; run again\), "
                              r"0 gone, 1 failed")
        self.assertTrue(self._names_in((self.cc_root / "2-proj/member/output/artifacts/run-1/result.json").read_bytes()))
        session = ChatSession.objects.get(pk=self.member_session.pk)
        self.assertEqual(session.results_history[0]["graph_result"]["data"], [CLEAN_ROW])

    def test_other_files_that_name_a_property_are_counted_and_left(self):
        notes = self.cc_root / "2-proj/member/output/artifacts/run-1/notes.md"
        notes.write_text("the rows had parent_titles")
        out = self._run("--store", "cc_artifacts")
        self.assertEqual(self._counts(out, "cc_artifacts", "non-admin"), (3, 2, 4, 1))
        self._apply("--store", "cc_artifacts")
        self.assertEqual(notes.read_text(), "the rows had parent_titles")

    def test_a_file_two_sessions_name_is_backed_up_and_written_once(self):
        shared = self.outputs / "260923_120000_shared/files/graph_result/graph_result_bundle_1.json"
        shared.parent.mkdir(parents=True)
        shared.write_text(json.dumps({"rows": [ROW]}))
        bundle = [{"id": 1, "files": [{"key": "graph_result", "path": str(shared)}]}]
        # Fixed ids: the superuser's session is read first, the member's second.
        ChatSession.objects.create(session_id=uuid.UUID(int=1), user=self.superuser, results_history=bundle)
        ChatSession.objects.create(session_id=uuid.UUID(int=2 ** 128 - 1), user=self.member, results_history=bundle)
        # By default the shared file is out of scope as the superuser's and read again as the member's.
        self.assertIn("in scope: 2 records", self._run("--store", "ns_files"))
        self.assertIn("in scope: 3 records", self._run("--store", "ns_files", "--include-admins"))
        out = self._apply("--store", "ns_files", "--include-admins")
        self.assertRegex(out, r"ns_files: 3 written, 0 changed since the scan")
        [backup] = list(self.backups.iterdir())
        paths = [json.loads(line).get("path") for line in backup.read_text().splitlines()]
        self.assertEqual(paths.count(str(shared)), 1)
        self.assertFalse(self._names_in(shared.read_bytes()))
