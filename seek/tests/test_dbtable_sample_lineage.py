"""Regression tests: the legacy upload route swallowed every lineage failure.

The legacy batch-upload route is still live -- ``seek/urls.py:87`` ->
``seek.views.sampleUploadAjax`` -> ``DBtable_sample.batchUpload`` -> here. It
writes the sample row to MySQL and its parent edges to Neo4j in two separate
steps, and it used to wrap the Neo4j step in a bare ``except: None``. A graph
write that blew up therefore left the sample in MySQL with no lineage while the
uploader was told "Batch sample uploading successful".

Two defects, one failure mode:

1. ``__storeSample`` and ``__batchUploadTest`` both discarded every exception
   from ``storeSampleNeo4j``.
2. ``extractParents`` emitted an empty token for any blank ``*Parent`` value,
   and the empty token detonated downstream.

Defect 2 is the one that fires on ordinary sheets, and the mechanism is worth
stating exactly because it is not the obvious one:

* ``__getRecordToJson`` writes EVERY attribute of the sample type into
  ``json_metadata``, not only the ones the uploader filled in. Any sample type
  carrying an unused ``*Parent`` attribute therefore ships a blank one on every
  row.
* A blank or absent cell becomes ``' '`` or ``''`` -- ``toString(None)`` returns
  a single space (``dmac/conversion.py``).
* The old ``extractParents`` turned that into an ``''`` token.
* ``getSampleID('')`` finds no record and returns ``None``.
* ``getConnectingRelationships`` interpolates that straight into SQL as
  ``aa.asset_id = None``, MySQL rejects it, ``__runQuery`` swallows the error
  and returns ``None``, and ``len(None)`` raises ``TypeError``.
* ``storeSampleNeo4j`` writes parents in a loop, so the ``TypeError`` abandoned
  every parent token ORDERED AFTER the blank one. Tokens already merged stayed.
  Silent, partial lineage loss.

So the empty-token filter is the line that fixes a real and common failure;
``test_blank_parent_cell_does_not_abandon_later_parents`` is its regression.

The ``isinstance`` guard is defence-in-depth rather than a fix for a reachable
path on THIS route: ``__getRecordToJson`` runs every value through ``toString``
first, so an integer from an Excel cell arrives here as ``"12345"``. It still
belongs -- ``storeSampleNeo4j`` is a public method and ``extractParents`` is
reachable from any caller with a raw dict -- but it is not what was breaking.

The modern route's equivalent helper,
``nextseek_api.batch_upload.helpers.collect_parent_tokens``, has carried both
guards (and tests, in ``nextseek_api/batch_upload/tests/test_helpers.py``) all
along; ``extractParents`` now matches it for empty and non-string values. It
deliberately does NOT match it for key matching: see
``test_key_matching_stays_case_sensitive``.

Hermetic, like the rest of ``seek/tests`` -- no conftest, no database.
``DBtable_sample.__init__`` opens a Django cursor (``dmac/dbconn_django.py:25``)
so it cannot run without a database; every test here builds the instance with
``__new__`` instead, which is enough because the code under test either is pure
(``extractParents``) or only touches collaborators these tests patch.
"""

import logging

import pytest


def _sample_table():
    """A DBtable_sample that never touched a database.

    ``__init__`` -> ``DBtable.__init__`` -> ``DBconnection('SEEK')`` ->
    ``DBconn_django()`` opens a cursor at construction time, which pytest-django
    refuses without the ``db`` fixture. None of the code under test reads the
    instance attributes ``__init__`` would have set.
    """
    from seek.dbtable_sample import DBtable_sample

    return DBtable_sample.__new__(DBtable_sample)


class TestExtractParents:
    """Value handling: matches ``collect_parent_tokens`` for empty/non-string."""

    def test_non_string_value_contributes_no_tokens(self):
        """An integer parent value used to raise AttributeError on .split."""
        assert _sample_table().extractParents({"Parent": 12345}) == []

    def test_float_value_contributes_no_tokens(self):
        assert _sample_table().extractParents({"Parent": 1.5}) == []

    def test_none_value_contributes_no_tokens(self):
        assert _sample_table().extractParents({"Parent": None}) == []

    def test_empty_string_value_contributes_no_tokens(self):
        """An empty value produced a bogus empty-UID parent lookup."""
        assert _sample_table().extractParents({"Parent": ""}) == []

    def test_whitespace_only_value_contributes_no_tokens(self):
        assert _sample_table().extractParents({"Parent": "   "}) == []

    def test_bad_value_does_not_destroy_the_other_parent_keys(self):
        """THE defect: one bad cell wiped every parent edge for the sample.

        ``.split`` raised inside the loop, so ``extractParents`` returned
        nothing at all -- and the bare ``except`` upstream hid it.
        """
        meta = {"Parent": 12345, "Treatment1Parent": "NHP-260225MIT-1"}
        assert _sample_table().extractParents(meta) == ["NHP-260225MIT-1"]

    def test_interior_empty_token_dropped(self):
        """"A;;B" used to yield an empty middle token."""
        assert _sample_table().extractParents({"Parent": "A;;B"}) == ["A", "B"]

    def test_blank_parent_cell_does_not_abandon_later_parents(self):
        """The production failure, in one line.

        A sample type with an unused ``*Parent`` attribute ships a blank value
        on every row (``__getRecordToJson`` emits every attribute, and
        ``toString(None)`` is ``' '``). The old code turned that into an ``''``
        token; ``getSampleID('')`` returned None, ``getConnectingRelationships``
        put ``aa.asset_id = None`` into SQL, ``__runQuery`` swallowed the MySQL
        error and returned None, and ``len(None)`` raised TypeError -- which the
        bare ``except`` discarded, abandoning every parent ordered AFTER the
        blank. Ordering matters, so this asserts on order.
        """
        meta = {"AntibodyParent": " ", "Parent": "NHP-260225MIT-1"}
        assert _sample_table().extractParents(meta) == ["NHP-260225MIT-1"]

    def test_only_semicolons_yields_no_tokens(self):
        assert _sample_table().extractParents({"Parent": ";;;"}) == []


class TestExtractParentsUnchangedBehaviour:
    """Characterisation: what this fix deliberately did NOT change.

    Key matching and token identity are out of scope; these guard against a
    later "while I'm here" edit importing the modern helper's semantics
    wholesale.
    """

    def test_semicolon_split_and_strip(self):
        assert _sample_table().extractParents({"Parent": " A ; B "}) == ["A", "B"]

    def test_substring_key_is_matched(self):
        meta = {"Treatment1Parent": "NHP-260225MIT-1"}
        assert _sample_table().extractParents(meta) == ["NHP-260225MIT-1"]

    def test_key_matching_stays_case_sensitive(self):
        """``"Parent" in k`` -- NARROWER than the modern ``"parent" in k.lower()``.

        A lowercase ``parent`` key is invisible to the legacy route and visible
        to the modern one, so the two routes disagree about lineage for the same
        sheet. Widening it is a behaviour change beyond this fix; this assertion
        pins today's behaviour so the divergence is a decision, not a drift.
        """
        assert _sample_table().extractParents({"parent": "NHP-260225MIT-1"}) == []

    def test_duplicates_are_not_deduplicated(self):
        """The modern helper dedupes; the legacy one does not. Left alone."""
        meta = {"Parent": "A", "Treatment1Parent": "A"}
        assert _sample_table().extractParents(meta) == ["A", "A"]

    def test_names_with_spaces_and_commas_are_not_split(self):
        meta = {"Parent": "UtEC - 2015010902;Doe, Jane sample"}
        assert _sample_table().extractParents(meta) == [
            "UtEC - 2015010902",
            "Doe, Jane sample",
        ]

    def test_non_parent_keys_ignored(self):
        meta = {"Name": "s1", "Protocol": "http://example.com"}
        assert _sample_table().extractParents(meta) == []


class TestStoreSampleNeo4jGuarded:
    """The replacement for ``try: ... except: None``."""

    def _call(self, table, sampleType="Mouse", record=None, uid="MOU-260101MIT-1"):
        # name-mangled: the method is private to DBtable_sample
        return table._DBtable_sample__storeSampleNeo4jGuarded(
            sampleType, record if record is not None else {}, uid
        )

    def test_success_reports_no_warning(self):
        table = _sample_table()
        calls = []
        table.storeSampleNeo4j = lambda st, rec: calls.append((st, rec))

        ok, msg = self._call(table, record={"uuid": "MOU-260101MIT-1"})

        assert ok is True
        assert msg == ""
        assert calls == [("Mouse", {"uuid": "MOU-260101MIT-1"})]

    def test_failure_does_not_propagate(self):
        """The row is already committed to MySQL; raising here aborts the batch."""
        table = _sample_table()

        def boom(sampleType, record):
            raise RuntimeError("neo4j unreachable")

        table.storeSampleNeo4j = boom

        ok, msg = self._call(table)  # must not raise

        assert ok is False

    def test_failure_message_names_the_code_uid_and_cause(self):
        from seek.dbtable_sample import SAMPLE_ERRORCODE

        table = _sample_table()

        def boom(sampleType, record):
            raise RuntimeError("neo4j unreachable")

        table.storeSampleNeo4j = boom

        ok, msg = self._call(table, uid="MOU-260101MIT-7")

        assert ok is False
        assert SAMPLE_ERRORCODE["603"] in msg
        assert "MOU-260101MIT-7" in msg
        assert "RuntimeError" in msg
        assert "neo4j unreachable" in msg

    def test_failure_is_logged_with_a_traceback(self, caplog):
        table = _sample_table()

        def boom(sampleType, record):
            raise RuntimeError("neo4j unreachable")

        table.storeSampleNeo4j = boom

        with caplog.at_level(logging.ERROR, logger="seek.dbtable_sample"):
            self._call(table, uid="MOU-260101MIT-7")

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errors) == 1
        assert errors[0].exc_info is not None, "no traceback captured"
        assert "MOU-260101MIT-7" in errors[0].getMessage()


def _store_sample_fixture(table, lineage_raises):
    """Patch out ``__storeSample``'s database collaborators, leave its logic.

    Only the six calls that need MySQL/SEEK are replaced. The branching under
    test -- "row stored, now write lineage" -- is the real code.
    """
    record_new = {
        "uuid": "MOU-260101MIT-1",
        "json_metadata": '{"Parent": "NHP-260225MIT-1"}',
    }
    table._DBtable_sample__verifyRequiredFields = lambda record, headers: ("", True)
    table._DBtable_sample__getRecord = lambda *a, **k: (record_new, True)
    table.storeOneRecord = lambda username, rec: ("Info: stored", 1, 42)
    table._DBtable_sample__updateSampleProject = lambda *a, **k: None
    table._DBtable_sample__updateSampleAssetsCreators = lambda *a, **k: None
    table._DBtable_sample__setSampleDatafileAssociation = lambda *a, **k: ("", True)

    def neo4j(sampleType, record):
        if lineage_raises:
            raise RuntimeError("neo4j unreachable")

    table.storeSampleNeo4j = neo4j
    return record_new


def _call_store_sample(table):
    return table._DBtable_sample__storeSample(
        {"username": "bob", "user_id": 3},
        "Mouse",
        {"UID": "MOU-260101MIT-1", "Name": "m1"},
        {"headers_required": [], "sampleType_id": 1, "headers": []},
        [],
        {"user_id": 3, "projectid": 2},
    )


class TestStoreSampleSurfacesLineageFailure:
    """``__storeSample`` must hand the failure back, not eat it."""

    def test_clean_write_reports_no_lineage_failure(self):
        from seek.dbtable_sample import SAMPLE_ERRORCODE

        table = _sample_table()
        _store_sample_fixture(table, lineage_raises=False)

        msg, status, uid, lineage_failed = _call_store_sample(table)

        assert lineage_failed is False
        assert SAMPLE_ERRORCODE["603"] not in msg
        assert status == 1
        assert uid == "MOU-260101MIT-1"

    def test_failed_write_is_flagged_and_described(self):
        from seek.dbtable_sample import SAMPLE_ERRORCODE

        table = _sample_table()
        _store_sample_fixture(table, lineage_raises=True)

        msg, status, uid, lineage_failed = _call_store_sample(table)

        assert lineage_failed is True
        assert SAMPLE_ERRORCODE["603"] in msg
        assert "neo4j unreachable" in msg

    def test_failed_write_still_admits_the_row_landed_in_mysql(self):
        """Honest in both directions: the sample IS stored, only lineage is not.

        Flipping ``status`` to 0 would tell the uploader the sample was not
        saved, drop its UID from the feedback sheet and break the parent-UID
        chain for later rows in the same sheet.
        """
        table = _sample_table()
        _store_sample_fixture(table, lineage_raises=True)

        msg, status, uid, lineage_failed = _call_store_sample(table)

        assert status == 1
        assert uid == "MOU-260101MIT-1"


class _FakeSeekDB:
    user_seek = {"username": "bob", "user_id": 3}
    creator = {"user_id": 3, "projectid": 2}


def _call_batch_upload_test(table, nrows=1):
    diclist = [
        {"Name": "m%d" % i, "Scientist": "Bob Smith"} for i in range(nrows)
    ]
    return table._DBtable_sample__batchUploadTest(
        _FakeSeekDB(),
        "Mouse",
        diclist,
        [],  # no feedback rows -> dici_feedback starts empty
        {"sampleType_id": 1, "headers_required": [], "headers": []},
        {"Sample Name": "Name"},
        [],
    )


class TestBatchUploadTestCountsLineageFailures:
    """The batch must not report a clean success when lineage was lost."""

    def _arrange(self, table, lineage_failed):
        table._DBtable_sample__verifySampleUID = lambda *a, **k: ("", 1)
        table._DBtable_sample__storeSample = lambda *a, **k: (
            "Info: stored",
            1,
            "MOU-260101MIT-1",
            lineage_failed,
        )

    def test_clean_batch_stays_a_success(self):
        table = _sample_table()
        self._arrange(table, lineage_failed=False)

        msg, status, diclist_new = _call_batch_upload_test(table)

        assert status is True
        assert "lineage" not in msg.lower()

    def test_lineage_failure_makes_the_batch_not_a_success(self):
        """``status`` drives "Batch sample uploading successful" in the view."""
        table = _sample_table()
        self._arrange(table, lineage_failed=True)

        msg, status, diclist_new = _call_batch_upload_test(table)

        assert status is False

    def test_lineage_failure_count_reaches_the_uploader(self):
        table = _sample_table()
        self._arrange(table, lineage_failed=True)

        msg, status, diclist_new = _call_batch_upload_test(table, nrows=3)

        assert "lineage" in msg.lower()
        # Specific to the batch-count sentence: a bare "3" also matches the
        # "out of in total 3 samples" summary, which is always present.
        assert "for 3 of them" in msg
        # And it must not tell the uploader to do the one thing that cannot work.
        assert "re-upload" not in msg.lower().replace("re-uploading will not", "")

    def test_row_is_still_returned_in_the_feedback_sheet(self):
        table = _sample_table()
        self._arrange(table, lineage_failed=True)

        msg, status, diclist_new = _call_batch_upload_test(table, nrows=2)

        assert len(diclist_new) == 2
        assert diclist_new[0]["Mouse::UID"] == "MOU-260101MIT-1"


class TestFeedbackDictGraphCallIsLoggedOncePerBatch:
    """The second swallowed call is structurally dead -- but no longer silent.

    ``__batchUploadTest`` calls ``storeSampleNeo4j(sampleType, dici_feedback)``.
    ``dici_feedback`` is keyed by spreadsheet headers plus ``<type>::UID``; it
    has no ``uuid`` and no ``json_metadata``, so ``storeSampleNeo4j`` raises
    ``KeyError('uuid')`` on its first statement for every row and has never
    written anything. The effective lineage write is the one in
    ``__storeSample``.

    It is therefore logged once per batch, not once per row, at WARNING rather
    than ERROR, and deliberately kept out of the uploader-facing count: counting
    it would report a lineage failure for every sample of every upload while the
    real write succeeded.

    Suppression is by CAUSE, not by location: only the known-dead ``KeyError``
    is routed to the quiet path. Any other exception here is a real graph
    failure and gets the full S603 treatment.
    """

    def test_feedback_shaped_dict_cannot_reach_the_graph(self):
        """Pins the premise above, in isolation.

        The stronger evidence is ``test_logged_once_per_batch_not_once_per_row``
        below, which leaves BOTH the real ``storeSampleNeo4j`` and the real
        feedback-dict construction in play, so the KeyError it counts is raised
        by production code rather than by a dict this test wrote.
        """
        feedback = {"Mouse::UID": "MOU-260101MIT-1", "Sample Name": "m1"}
        with pytest.raises(KeyError) as exc:
            _sample_table().storeSampleNeo4j("Mouse", feedback)
        assert "uuid" in str(exc.value)

    def test_logged_once_per_batch_not_once_per_row(self, caplog):
        table = _sample_table()
        table._DBtable_sample__verifySampleUID = lambda *a, **k: ("", 1)
        table._DBtable_sample__storeSample = lambda *a, **k: (
            "Info: stored",
            1,
            "MOU-260101MIT-1",
            False,
        )

        with caplog.at_level(logging.WARNING, logger="seek.dbtable_sample"):
            msg, status, diclist_new = _call_batch_upload_test(table, nrows=3)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        text = warnings[0].getMessage()
        assert "3" in text
        assert warnings[0].exc_info is not None, "no traceback captured"

    def test_dead_call_does_not_pollute_the_error_channel(self, caplog):
        """It fires on every upload; at ERROR it would train operators to ignore
        the level that carries the genuine lineage failure."""
        table = _sample_table()
        table._DBtable_sample__verifySampleUID = lambda *a, **k: ("", 1)
        table._DBtable_sample__storeSample = lambda *a, **k: (
            "Info: stored",
            1,
            "MOU-260101MIT-1",
            False,
        )

        with caplog.at_level(logging.WARNING, logger="seek.dbtable_sample"):
            _call_batch_upload_test(table, nrows=3)

        assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


class TestFeedbackCallStillSurfacesRealFailures:
    """Narrowing to KeyError must not become "suppress everything here"."""

    def _arrange(self, table, exc):
        table._DBtable_sample__verifySampleUID = lambda *a, **k: ("", 1)
        table._DBtable_sample__storeSample = lambda *a, **k: (
            "Info: stored",
            1,
            "MOU-260101MIT-1",
            False,
        )

        def boom(sampleType, record):
            raise exc

        table.storeSampleNeo4j = boom

    def test_non_keyerror_denies_the_batch_its_success(self):
        table = _sample_table()
        self._arrange(table, RuntimeError("neo4j unreachable"))

        msg, status, diclist_new = _call_batch_upload_test(table, nrows=2)

        assert status is False

    def test_non_keyerror_reaches_the_uploader_with_the_s603_code(self):
        from seek.dbtable_sample import SAMPLE_ERRORCODE

        table = _sample_table()
        self._arrange(table, RuntimeError("neo4j unreachable"))

        msg, status, diclist_new = _call_batch_upload_test(table, nrows=2)

        assert SAMPLE_ERRORCODE["603"] in msg
        assert "neo4j unreachable" in msg
        # The batch-count sentence specifically -- "lineage"/"2" alone also
        # match the per-sample S603 text and the "out of in total 2" summary,
        # so they do not prove the batch counter moved.
        assert "for 2 of them" in msg

    def test_non_keyerror_is_logged_at_error_with_a_traceback(self, caplog):
        table = _sample_table()
        self._arrange(table, RuntimeError("neo4j unreachable"))

        with caplog.at_level(logging.WARNING, logger="seek.dbtable_sample"):
            _call_batch_upload_test(table, nrows=2)

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errors) == 2, "one per affected row"
        assert all(r.exc_info is not None for r in errors)

    def test_it_does_not_reach_the_uploader_facing_count(self):
        """Every row fails this call; surfacing it would be a false alarm."""
        table = _sample_table()
        table._DBtable_sample__verifySampleUID = lambda *a, **k: ("", 1)
        table._DBtable_sample__storeSample = lambda *a, **k: (
            "Info: stored",
            1,
            "MOU-260101MIT-1",
            False,
        )

        msg, status, diclist_new = _call_batch_upload_test(table, nrows=3)

        assert status is True
        assert "lineage" not in msg.lower()
