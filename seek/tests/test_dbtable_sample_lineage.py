"""``extractParents``: the parent tokens the legacy upload route reads out of a sample's metadata.

The legacy batch-upload route is still live -- ``seek/urls.py`` -> ``seek.views.upload.sampleUploadAjax`` ->
``DBtable_sample.batchUpload`` -> ``seek/sample/upload.py``. It writes the sample row to MySQL and enqueues the
sample's graph sync; the graph itself is written later by the sync loop, which reads MySQL's parent tokens with
``nextseek_api.batch_upload.helpers.collect_parent_tokens``. The tests for the enqueue are
``nextseek_api/tests/test_graph_sync_hook_legacy.py``.

``extractParents`` is what this route used to hand its own Neo4j write, and it is still the public reading of a
sample's parent tokens on this side. Its value handling was a real defect and the fix is pinned here:

* ``_getRecordToJson`` writes EVERY attribute of the sample type into ``json_metadata``, not only the ones the
  uploader filled in. Any sample type carrying an unused ``*Parent`` attribute therefore ships a blank one on every
  row, as ``' '`` (``toString(None)``) or ``''``.
* The old code turned that into an ``''`` token, ``getSampleID('')`` found no record and returned ``None``, and the
  ``None`` detonated in the SQL built from it -- abandoning, inside a loop, every parent token ordered AFTER the
  blank one. Silent, partial lineage loss.

So the empty-token filter is the line that fixes a real and common failure;
``test_blank_parent_cell_does_not_abandon_later_parents`` is its regression. The ``isinstance`` guard is
defence-in-depth rather than a fix for a reachable path on this route: ``_getRecordToJson`` runs every value through
``toString`` first, so an integer from an Excel cell arrives here as ``"12345"``.

``collect_parent_tokens`` has carried both guards (and tests, in
``nextseek_api/batch_upload/tests/test_helpers.py``) all along; ``extractParents`` matches it for empty and
non-string values. It deliberately does NOT match it for key matching: see
``test_key_matching_stays_case_sensitive``.

Hermetic, like the rest of ``seek/tests`` -- no conftest, no database. ``DBtable_sample.__init__`` opens a Django
cursor (``dmac/dbconn_django.py``) so it cannot run without a database; every test here builds the instance with
``__new__`` instead, which is enough because the code under test is pure.
"""


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
        on every row (``_getRecordToJson`` emits every attribute, and
        ``toString(None)`` is ``' '``). The old code turned that into an ``''``
        token, which returned no sample id and then detonated in the SQL built
        from it -- abandoning every parent ordered AFTER the blank. Ordering
        matters, so this asserts on order.
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
