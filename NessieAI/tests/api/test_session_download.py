"""GET /nextseek_api/assistant/sessions/{sid}/download/: one chat as one zip.

The zip holds the transcript and every turn's files. Those files live under two
roots with two different guards, and one session can hold both:

* NS turns register files in their bundle (``files``, ``raw_result_path``,
  ``report_saved_files``). They are served only from inside the outputs roots,
  through ``_safe_artifact_path``.
* Container-CC turns publish into ``<owner's CC tree>/output/artifacts/<run id>/``.
  That tree is the project folder the turns ran in, which the session saves as
  ``extra_state['cc_project_dirname']``, plus the owner's username: no SEEK call,
  so neither a later project rename nor the caller's own login can move it. Each
  file is guarded by ``resolve_artifact_path``.

The response is streamed: a bundle's files run to megabytes, and the ASGI server
this app runs under would otherwise read a synchronous iterator into one list
before sending a byte.

Nothing here writes under ``BASE_DIR``: the test lane mounts the tree read-only, so
the NS root comes from ``NEXTSEEK_OUTPUTS_DIR`` and the CC tree from
``DMAC_USER_ROOT_MOUNT``, both pointed at a temporary directory.
"""
import io
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import openpyxl
from django.contrib.auth.models import User
from django.test import AsyncClient, TestCase
from rest_framework.test import APIClient

from NessieAI.cc.cc_provision import ProjectIdentity
from nextseek_api.assistant.models_db import ChatSession
from nextseek_api.assistant.session_export import CHUNK_BYTES

CC_RUN = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
PROJECT = ProjectIdentity(id="7", title="Test Lab", slug="test-lab")
# What SEEK would say the owner's project is today: renamed since the CC turns ran.
RENAMED = ProjectIdentity(id="7", title="Test Lab Renamed", slug="test-lab-renamed")
NO_SUCH_SESSION = "2f1e0d3c-4b5a-6978-8796-a5b4c3d2e1f0"


def _url(sid):
    return f"/nextseek_api/assistant/sessions/{sid}/download/"


def _ns_entry(n, bundle_id, query="how many mice", reply="There are 3."):
    return {"turn_id": n, "user_query": query, "assistant_reply": reply,
            "mode": "new_search", "ts": "2026-09-18T10:00:00Z", "bundle_id": bundle_id}


def _cc_entry(n, query="plot the counts", reply="Done, see the plot."):
    return {"turn_id": n, "user_query": query, "assistant_reply": reply, "mode": "cc",
            "ts": "2026-09-18T10:05:00Z", "cc_run_id": CC_RUN,
            "artifacts": [{"artifact_type": "file", "key": f"{CC_RUN}/artifacts.zip",
                           "label": "artifacts.zip", "file_format": "zip"}]}


class _DownloadBase(TestCase):
    databases = {"default"}

    def setUp(self):
        self.owner = User.objects.create_user("owner", password="pw")
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        patcher = patch(
            "nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.outputs = self.tmp / "outputs"
        self.cc_mount = self.tmp / "cc-users"
        self.outputs.mkdir()
        self.cc_mount.mkdir()
        env = patch.dict(os.environ, {
            "NEXTSEEK_OUTPUTS_DIR": str(self.outputs),
            "DMAC_USER_ROOT_MOUNT": str(self.cc_mount),
        })
        env.start()
        self.addCleanup(env.stop)
        # A tripwire: the export must never ask SEEK where the CC tree is. If it
        # did, it would be told the renamed folder and find nothing there.
        resolver = patch("NessieAI.cc.cc_provision.resolve_user_project",
                         return_value=RENAMED)
        self.resolve = resolver.start()
        self.addCleanup(resolver.stop)

    # -- fixtures on disk -------------------------------------------------
    def ns_file(self, name, content, subdir="run1"):
        path = self.outputs / subdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        return str(path)

    def cc_file(self, rel, content, user="owner"):
        path = (self.cc_mount / PROJECT.dirname / user / "output" / "artifacts"
                / CC_RUN / rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        return path

    def session(self, *, chat_log=None, bundles=None, user=None, title="Mouse counts",
                cc_dirname=None):
        """``cc_dirname`` is the project folder the CC turns ran in, saved by the turn."""
        extra = {"chat_log": chat_log} if chat_log else {}
        if cc_dirname is not None:
            extra["cc_project_dirname"] = cc_dirname
        return ChatSession.objects.create(
            user=user or self.owner, title=title,
            results_history=bundles or [], extra_state=extra,
        )

    # -- reading the response ---------------------------------------------
    def download(self, cs):
        resp = self.client.get(_url(cs.session_id))
        self.assertEqual(resp.status_code, 200, getattr(resp, "content", b"")[:300])
        return resp

    def unzip(self, resp):
        return zipfile.ZipFile(io.BytesIO(b"".join(resp.streaming_content)))

    def manifest(self, zf):
        return json.loads(zf.read("manifest.json"))


class DownloadGateTests(_DownloadBase):

    def test_unauthenticated_is_refused(self):
        cs = self.session()
        resp = APIClient().get(_url(cs.session_id))
        self.assertIn(resp.status_code, (401, 403))

    def test_staff_who_does_not_own_the_session_is_403(self):
        """is_staff is set on every SEEK login, so it must not open anyone's chat."""
        staff = User.objects.create_user("staff", password="pw")
        staff.is_staff = True
        staff.save()
        cs = self.session()
        client = APIClient()
        client.force_authenticate(user=staff)
        self.assertEqual(client.get(_url(cs.session_id)).status_code, 403)

    def test_unknown_session_is_the_api_404_not_the_catch_all_page(self):
        """An unrouted path here also answers 404, as HTML from Mezzanine's
        catch-all, so the status alone would pass without the route existing."""
        resp = self.client.get(_url(NO_SUCH_SESSION))
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["errors"][0]["title"], "Not found")


class DownloadContentTests(_DownloadBase):

    def test_a_zip_attachment_carrying_the_transcript_and_the_turn_file(self):
        path = self.ns_file("api_result_bundle_1.json", '{"rows": [{"uid": "TIS-1"}]}')
        cs = self.session(
            chat_log=[_ns_entry(1, 1)],
            bundles=[{"id": 1, "mode": "new_search", "user_query": "how many mice",
                      "files": [{"key": "api_result", "label": "Full API result JSON",
                                 "path": path, "filename": "api_result_bundle_1.json",
                                 "mime": "application/json", "kind": "api"}]}],
        )

        resp = self.download(cs)

        self.assertEqual(resp["Content-Type"], "application/zip")
        self.assertIn("attachment;", resp["Content-Disposition"])
        self.assertIn(".zip", resp["Content-Disposition"])
        zf = self.unzip(resp)
        self.assertEqual(zf.read("turn-01/api_result_bundle_1.json"),
                         b'{"rows": [{"uid": "TIS-1"}]}')
        md = zf.read("transcript.md").decode()
        self.assertIn("how many mice", md)
        self.assertIn("There are 3.", md)
        self.assertIn("turn-01/", md)
        transcript = json.loads(zf.read("transcript.json"))
        self.assertEqual(transcript["session_id"], str(cs.session_id))
        self.assertEqual(transcript["turns"][0]["user_query"], "how many mice")
        self.assertEqual(transcript["turns"][0]["folder"], "turn-01")

    def test_the_transcript_numbers_turns_as_the_chat_ui_does(self):
        """A non-answer turn is hidden on screen (PD-6), so it takes no number here."""
        path = self.ns_file("api_result_bundle_1.json", "{}")
        cs = self.session(
            chat_log=[
                {"turn_id": 1, "user_query": "hello?", "mode": "unrelated",
                 "status": "error"},
                _ns_entry(2, 1),
            ],
            bundles=[{"id": 1, "mode": "new_search",
                      "files": [{"key": "api_result", "path": path,
                                 "filename": "api_result_bundle_1.json", "kind": "api"}]}],
        )

        zf = self.unzip(self.download(cs))

        self.assertIn("turn-01/api_result_bundle_1.json", zf.namelist())
        self.assertNotIn("hello?", zf.read("transcript.md").decode())

    def test_the_manifest_is_last_and_lists_every_file(self):
        path = self.ns_file("a.json", "{}")
        cs = self.session(
            chat_log=[_ns_entry(1, 1)],
            bundles=[{"id": 1, "mode": "new_search",
                      "files": [{"key": "api_result", "path": path, "filename": "a.json",
                                 "kind": "api"}]}],
        )

        zf = self.unzip(self.download(cs))

        self.assertEqual(zf.namelist()[-1], "manifest.json")
        listed = {f["name"] for f in self.manifest(zf)["files"]}
        self.assertEqual(listed, set(zf.namelist()) - {"manifest.json"})

    def test_a_path_outside_the_artifact_roots_is_left_out_and_not_disclosed(self):
        cs = self.session(
            chat_log=[_ns_entry(1, 1)],
            bundles=[{"id": 1, "mode": "new_search",
                      "files": [{"key": "api_result", "path": "/etc/passwd",
                                 "filename": "passwd", "kind": "api"}]}],
        )

        zf = self.unzip(self.download(cs))

        self.assertFalse(any(n.endswith("passwd") for n in zf.namelist()))
        skipped = self.manifest(zf)["skipped"]
        self.assertEqual([s["reason"] for s in skipped], ["outside_artifact_root"])
        self.assertNotIn("/etc/passwd", zf.read("manifest.json").decode())

    def test_a_file_gone_from_disk_is_reported_missing(self):
        path = self.ns_file("gone.json", "{}")
        os.unlink(path)
        cs = self.session(
            chat_log=[_ns_entry(1, 1)],
            bundles=[{"id": 1, "mode": "new_search",
                      "files": [{"key": "api_result", "path": path, "filename": "gone.json",
                                 "kind": "api"}]}],
        )

        zf = self.unzip(self.download(cs))

        self.assertEqual([s["reason"] for s in self.manifest(zf)["skipped"]],
                         ["missing_on_disk"])

    def test_a_file_removed_after_the_response_started_is_a_manifest_line(self):
        """Paths are checked before the first byte; a file pruned while the zip is
        being sent must still leave a complete zip, not a broken stream."""
        path = self.ns_file("pruned.json", "{}")
        cs = self.session(
            chat_log=[_ns_entry(1, 1)],
            bundles=[{"id": 1, "mode": "new_search",
                      "files": [{"key": "api_result", "path": path,
                                 "filename": "pruned.json", "kind": "api"}]}],
        )

        resp = self.download(cs)
        os.unlink(path)
        zf = self.unzip(resp)

        self.assertNotIn("turn-01/pruned.json", zf.namelist())
        self.assertEqual([s["reason"] for s in self.manifest(zf)["skipped"]],
                         ["read_failed"])

    def test_internal_debug_files_are_left_out(self):
        """The UI hides graph and memory files; so does the zip."""
        path = self.ns_file("graph_debug_1.json", "{}")
        cs = self.session(
            chat_log=[_ns_entry(1, 1)],
            bundles=[{"id": 1, "mode": "graph_query",
                      "files": [{"key": "graph_debug", "path": path,
                                 "filename": "graph_debug_1.json", "kind": "graph"}]}],
        )

        zf = self.unzip(self.download(cs))

        self.assertNotIn("turn-01/graph_debug_1.json", zf.namelist())

    def test_one_file_listed_twice_is_zipped_once(self):
        path = self.ns_file("api_result_bundle_1.json", "{}")
        cs = self.session(
            chat_log=[_ns_entry(1, 1)],
            bundles=[{"id": 1, "mode": "new_search", "raw_result_path": path,
                      "files": [{"key": "api_result", "path": path,
                                 "filename": "api_result_bundle_1.json", "kind": "api"}]}],
        )

        zf = self.unzip(self.download(cs))

        self.assertEqual(
            [n for n in zf.namelist() if n.startswith("turn-01/")],
            ["turn-01/api_result_bundle_1.json"],
        )

    def test_every_saved_file_of_an_op_bundle_lands_in_its_own_folder(self):
        """Granular-op bundles have no chat_log entry and only report_saved_files."""
        first = self.ns_file("geo_1.xlsx", b"PK-one", subdir="granular/a")
        second = self.ns_file("geo_2.xlsx", b"PK-two", subdir="granular/a")
        cs = self.session(
            chat_log=[_ns_entry(1, 1)],
            bundles=[
                {"id": 1, "mode": "new_search"},
                {"id": 2, "mode": "generate-submission",
                 "report_saved_files": {"geo_seq_workbooks": [first, second]}},
            ],
        )

        zf = self.unzip(self.download(cs))

        self.assertEqual(zf.read("bundle-2/geo_1.xlsx"), b"PK-one")
        self.assertEqual(zf.read("bundle-2/geo_2.xlsx"), b"PK-two")

    def test_report_tables_become_one_workbook(self):
        cs = self.session(
            chat_log=[_ns_entry(1, 1, query="report on project 2")],
            bundles=[{"id": 1, "mode": "reporter",
                      "report_writer_output": {"report": {
                          "samples": [{"uid": "S-1", "organism": "mouse"}]}}}],
        )

        zf = self.unzip(self.download(cs))

        wb = openpyxl.load_workbook(io.BytesIO(zf.read("turn-01/report_1.xlsx")))
        self.assertEqual(wb.sheetnames, ["Samples"])
        self.assertEqual(wb["Samples"]["A2"].value, "S-1")

    def test_a_session_with_no_turns_still_downloads(self):
        zf = self.unzip(self.download(self.session()))
        self.assertEqual(zf.namelist(), ["transcript.md", "transcript.json", "manifest.json"])


class DownloadContainerCCTests(_DownloadBase):

    def test_cc_files_come_from_the_folder_the_turns_used_without_the_turn_zip(self):
        """The owner's SEEK project has been renamed since the turn ran; the files
        are still where the turn put them, and that is where they are read from."""
        self.cc_file("summary.csv", "a,b\n1,2\n")
        self.cc_file("plots/counts.png", b"\x89PNG")
        self.cc_file("artifacts.zip", b"PK-bundled-copy")
        cs = self.session(chat_log=[_cc_entry(1)], cc_dirname=PROJECT.dirname)

        zf = self.unzip(self.download(cs))

        self.assertEqual(zf.read("turn-01/summary.csv"), b"a,b\n1,2\n")
        self.assertEqual(zf.read("turn-01/plots/counts.png"), b"\x89PNG")
        self.assertNotIn("turn-01/artifacts.zip", zf.namelist())
        self.assertEqual(self.manifest(zf)["skipped"], [])
        self.resolve.assert_not_called()

    def test_a_turn_whose_only_file_is_named_artifacts_zip_keeps_it(self):
        self.cc_file("artifacts.zip", b"PK-the-deliverable")
        cs = self.session(chat_log=[_cc_entry(1)], cc_dirname=PROJECT.dirname)

        zf = self.unzip(self.download(cs))

        self.assertEqual(zf.read("turn-01/artifacts.zip"), b"PK-the-deliverable")

    def test_a_symlink_out_of_the_cc_tree_is_not_followed(self):
        secret = self.tmp / "secret.txt"
        secret.write_text("do not ship")
        real = self.cc_file("real.csv", "x\n")
        (real.parent / "leak.txt").symlink_to(secret)
        cs = self.session(chat_log=[_cc_entry(1)], cc_dirname=PROJECT.dirname)

        zf = self.unzip(self.download(cs))

        self.assertIn("turn-01/real.csv", zf.namelist())
        self.assertNotIn("turn-01/leak.txt", zf.namelist())
        self.assertNotIn(b"do not ship", b"".join(zf.read(n) for n in zf.namelist()))

    def test_one_session_holding_both_roots_ships_both(self):
        path = self.ns_file("api_result_bundle_1.json", '{"n": 3}')
        self.cc_file("summary.csv", "a\n")
        cs = self.session(
            chat_log=[_ns_entry(1, 1), _cc_entry(2)],
            bundles=[{"id": 1, "mode": "new_search",
                      "files": [{"key": "api_result", "path": path,
                                 "filename": "api_result_bundle_1.json", "kind": "api"}]}],
            cc_dirname=PROJECT.dirname,
        )

        zf = self.unzip(self.download(cs))

        self.assertEqual(zf.read("turn-01/api_result_bundle_1.json"), b'{"n": 3}')
        self.assertEqual(zf.read("turn-02/summary.csv"), b"a\n")

    def test_a_cc_turn_with_no_saved_folder_is_listed_and_the_rest_still_ships(self):
        """Nothing records where the turn ran, so its files are named as skipped
        rather than looked for in whatever project SEEK names today."""
        self.cc_file("summary.csv", "a\n")
        path = self.ns_file("api_result_bundle_1.json", "{}")
        cs = self.session(
            chat_log=[_ns_entry(1, 1), _cc_entry(2)],
            bundles=[{"id": 1, "mode": "new_search",
                      "files": [{"key": "api_result", "path": path,
                                 "filename": "api_result_bundle_1.json", "kind": "api"}]}],
        )

        zf = self.unzip(self.download(cs))

        self.assertIn("turn-01/api_result_bundle_1.json", zf.namelist())
        self.assertEqual([(s["folder"], s["reason"]) for s in self.manifest(zf)["skipped"]],
                         [("turn-02", "cc_tree_unresolved")])
        self.resolve.assert_not_called()

    def test_a_saved_folder_that_is_not_one_plain_segment_is_refused(self):
        """The saved folder name goes through the CC layout's own segment check,
        so a value that climbs out of the users mount names nothing."""
        escaped = (self.tmp / PROJECT.dirname / "owner" / "output" / "artifacts" / CC_RUN
                   / "summary.csv")
        escaped.parent.mkdir(parents=True)
        escaped.write_text("outside the users mount")
        cs = self.session(chat_log=[_cc_entry(1)], cc_dirname=f"../{PROJECT.dirname}")

        zf = self.unzip(self.download(cs))

        self.assertNotIn("turn-01/summary.csv", zf.namelist())
        self.assertEqual([s["reason"] for s in self.manifest(zf)["skipped"]],
                         ["cc_tree_unresolved"])

    def test_an_ns_only_session_never_asks_seek_for_the_cc_tree(self):
        path = self.ns_file("a.json", "{}")
        cs = self.session(
            chat_log=[_ns_entry(1, 1)],
            bundles=[{"id": 1, "mode": "new_search",
                      "files": [{"key": "api_result", "path": path, "filename": "a.json",
                                 "kind": "api"}]}],
        )

        self.unzip(self.download(cs))

        self.resolve.assert_not_called()

    def test_a_superuser_gets_another_users_cc_files_from_the_owners_tree(self):
        """The tree is the owner's, named by the session, so an operator's download
        carries it too, and never reads the operator's own tree instead."""
        root = User.objects.create_superuser("root", "root@example.com", "pw")
        self.client.force_authenticate(user=root)
        path = self.ns_file("api_result_bundle_1.json", "{}")
        self.cc_file("summary.csv", "owner's\n")
        self.cc_file("decoy.csv", "operator's own\n", user="root")
        cs = self.session(
            chat_log=[_ns_entry(1, 1), _cc_entry(2)],
            bundles=[{"id": 1, "mode": "new_search",
                      "files": [{"key": "api_result", "path": path,
                                 "filename": "api_result_bundle_1.json", "kind": "api"}]}],
            cc_dirname=PROJECT.dirname,
        )

        zf = self.unzip(self.download(cs))

        self.assertIn("turn-01/api_result_bundle_1.json", zf.namelist())
        self.assertEqual(zf.read("turn-02/summary.csv"), b"owner's\n")
        self.assertNotIn("turn-02/decoy.csv", zf.namelist())
        self.assertEqual(self.manifest(zf)["skipped"], [])
        self.resolve.assert_not_called()


class _CountingFile:
    """A file opened for reading whose ``read`` calls add to a counter."""

    def __init__(self, fh, counter):
        self._fh = fh
        self._counter = counter

    def read(self, size=-1):
        data = self._fh.read(size)
        self._counter.read += len(data)
        return data

    def __getattr__(self, name):
        return getattr(self._fh, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._fh.close()


class _ReadCounter:
    """How many bytes of ``target`` have been read so far, through ``Path.open``.

    A test ends by checking that the counter saw the whole file, so a reader that
    stopped going through ``Path.open`` fails loudly instead of counting nothing.
    """

    def __init__(self, target: Path):
        self.target = target.resolve()
        self.read = 0

    def patch(self):
        counter, real_open = self, Path.open

        def counting_open(path, *args, **kwargs):
            fh = real_open(path, *args, **kwargs)
            return _CountingFile(fh, counter) if Path(path).resolve() == counter.target else fh

        return patch.object(Path, "open", counting_open)


#: How far reading the file may run ahead of what has been sent: one read step
#: plus what the compressor holds back. Holding a whole file, or the whole zip,
#: overshoots it by megabytes.
READ_AHEAD_BOUND = 3 * CHUNK_BYTES


class DownloadStreamingTests(_DownloadBase):

    def _big_session(self):
        payload = os.urandom(1_500_000)  # incompressible, so the zip is as big
        path = self.ns_file("big.bin", payload)
        self.big_path = Path(path)
        cs = self.session(
            chat_log=[_ns_entry(1, 1)],
            bundles=[{"id": 1, "mode": "new_search",
                      "files": [{"key": "big", "path": path, "filename": "big.bin",
                                 "kind": "api"}]}],
        )
        return cs, payload

    def test_the_zip_leaves_in_pieces_under_wsgi(self):
        cs, payload = self._big_session()

        resp = self.download(cs)

        self.assertTrue(resp.streaming)
        chunks = list(resp.streaming_content)
        self.assertGreater(len(chunks), 4)
        self.assertLess(max(len(c) for c in chunks), 512 * 1024)
        zf = zipfile.ZipFile(io.BytesIO(b"".join(chunks)))
        self.assertEqual(zf.read("turn-01/big.bin"), payload)

    async def test_the_zip_is_an_async_stream_under_asgi(self):
        """Under ASGI Django reads a synchronous iterator into one list before
        sending anything, which would hold the whole zip in memory."""
        cs, payload = await self._abig_session()
        client = AsyncClient()
        await client.aforce_login(self.owner)

        resp = await client.get(_url(cs.session_id))

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.streaming)
        self.assertTrue(resp.is_async)
        chunks = [c async for c in resp.streaming_content]
        self.assertGreater(len(chunks), 4)
        zf = zipfile.ZipFile(io.BytesIO(b"".join(chunks)))
        self.assertEqual(zf.read("turn-01/big.bin"), payload)

    async def _abig_session(self):
        from asgiref.sync import sync_to_async
        return await sync_to_async(self._big_session)()

    # The two tests above count and size the pieces, which a server that builds the
    # whole zip and then slices it also passes. These two watch the file itself:
    # when the first piece leaves, the big file has not been read to its end, and
    # at no point has reading it run far ahead of what has been sent.

    def assert_read_keeps_pace(self, counter, n, sent, size):
        if n == 0:
            self.assertLess(counter.read, size,
                            "the whole file was read before the first piece left")
        self.assertLessEqual(counter.read - sent, READ_AHEAD_BOUND,
                             f"{counter.read} bytes read with only {sent} sent")

    def test_the_file_is_read_only_as_the_zip_is_sent_under_wsgi(self):
        cs, payload = self._big_session()
        counter = _ReadCounter(self.big_path)

        with counter.patch():
            resp = self.download(cs)
            sent = 0
            for n, piece in enumerate(resp.streaming_content):
                sent += len(piece)
                self.assert_read_keeps_pace(counter, n, sent, len(payload))

        self.assertEqual(counter.read, len(payload))

    async def test_the_file_is_read_only_as_the_zip_is_sent_under_asgi(self):
        cs, payload = await self._abig_session()
        client = AsyncClient()
        await client.aforce_login(self.owner)
        counter = _ReadCounter(self.big_path)

        with counter.patch():
            resp = await client.get(_url(cs.session_id))
            self.assertTrue(resp.is_async)
            sent, n = 0, 0
            async for piece in resp.streaming_content:
                sent += len(piece)
                self.assert_read_keeps_pace(counter, n, sent, len(payload))
                n += 1

        self.assertEqual(counter.read, len(payload))
