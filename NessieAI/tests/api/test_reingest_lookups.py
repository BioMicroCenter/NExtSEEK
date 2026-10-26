import json
from unittest.mock import MagicMock, patch

import pytest

from nextseek_api.services import reingest_lookups

# The catalog rows behind known_sample_types/attributes_for come from
# context_catalog.load_sample_types(), which queries Sample_types_context.
# Every other loader test patches its DB boundary, _sample_type_rows, rather
# than hitting a real (and here unseeded) database -- see the docstring at
# nextseek_api/tests/test_context_catalog.py:91. Same rule here: no DB fixture.
_CATALOG_ROWS = [
    {
        "sample_type": "D.SEQ", "sampletype_id": 1, "name": "Sequencing Data",
        "description": "", "clade": "Raw", "tags": "",
        "required_metadata": "UID, File_PrimaryData",
        "standard_metadata": "Instrument",
        "possible_metadata_fields": "Notes",
        "parent_sampletypes": "", "child_sampletypes": "",
        "associated_assay_parents": "", "associated_assay_children": "",
    },
    {
        "sample_type": "A.GEX", "sampletype_id": 2, "name": "GEX Analysis",
        "description": "", "clade": "Analyzed", "tags": "",
        "required_metadata": "Checksum_PrimaryData, File_PrimaryData",
        "standard_metadata": "Pipeline_Version",
        "possible_metadata_fields": "Notes",
        "parent_sampletypes": "", "child_sampletypes": "",
        "associated_assay_parents": "", "associated_assay_children": "",
    },
    {
        "sample_type": "A.ALN", "sampletype_id": 3, "name": "Alignment Analysis",
        "description": "", "clade": "Analyzed", "tags": "",
        "required_metadata": "Checksum_PrimaryData, File_PrimaryData",
        "standard_metadata": "Pipeline_Version",
        "possible_metadata_fields": "Notes",
        "parent_sampletypes": "", "child_sampletypes": "",
        "associated_assay_parents": "", "associated_assay_children": "",
    },
    {
        "sample_type": "A.SCXP", "sampletype_id": 4, "name": "Single-Cell Analysis",
        "description": "", "clade": "Analyzed", "tags": "",
        "required_metadata": "Checksum_PrimaryData, File_PrimaryData",
        "standard_metadata": "Pipeline_Version",
        "possible_metadata_fields": "Notes",
        "parent_sampletypes": "", "child_sampletypes": "",
        "associated_assay_parents": "", "associated_assay_children": "",
    },
]


@pytest.fixture(autouse=True)
def _catalog_rows():
    """Stand in for the sample_types_context table on every test in this file."""
    with patch(
        "nextseek_api.services.context_catalog._sample_type_rows",
        return_value=_CATALOG_ROWS,
    ):
        yield


def test_known_sample_types_includes_the_reingest_targets():
    known = reingest_lookups.known_sample_types()
    assert {"D.SEQ", "A.GEX", "A.ALN", "A.SCXP"} <= known


def test_attributes_for_returns_titles_and_required_flags():
    attrs = reingest_lookups.attributes_for("A.GEX")
    assert attrs, "A.GEX should have attributes"
    assert {"title", "required"} <= set(attrs[0])
    assert "Checksum_PrimaryData" in {a["title"] for a in attrs}


def test_checksum_primary_data_is_required_on_the_analysis_types():
    for sample_type in ("A.GEX", "A.ALN", "A.SCXP"):
        required = {a["title"] for a in reingest_lookups.attributes_for(sample_type)
                    if a["required"]}
        assert "Checksum_PrimaryData" in required, sample_type


def test_attributes_for_an_unknown_type_is_empty_not_an_error():
    assert reingest_lookups.attributes_for("A.NOPE") == []


def test_attributes_for_strips_the_callers_sample_type():
    # Finding: the catalog value was stripped but the caller's argument wasn't,
    # so a sample type padded with whitespace silently looked unknown.
    assert reingest_lookups.attributes_for(" A.GEX ") == reingest_lookups.attributes_for("A.GEX")
    assert reingest_lookups.attributes_for(" A.GEX ") != []


class TestServerRequired:
    """attributes_for's `server_required` flag: sourced from SEEK's own
    `sample_attributes.required` when reachable, fail-safe (fall back to the
    catalog's `required`) when it is not -- see the function's docstring."""

    def test_seeks_own_flag_wins_when_reachable(self):
        # A.GEX's catalog entry declares BOTH Checksum_PrimaryData and
        # File_PrimaryData required -- SEEK disagrees for Checksum_PrimaryData
        # (verified against startup/seed/seek_production.sql.gz: required=0
        # on A.GEX). server_required must reflect SEEK's answer per title,
        # not just copy the catalog's.
        with patch("seek.models.Sample_types.objects") as sample_types, \
                patch("seek.models.Sample_attributes.objects") as sample_attrs:
            sample_types.filter.return_value.values_list.return_value = [2]
            sample_attrs.filter.return_value.values_list.return_value = [
                ("Checksum_PrimaryData", 0), ("File_PrimaryData", 1),
            ]
            attrs = {a["title"]: a for a in reingest_lookups.attributes_for("A.GEX")}

        assert attrs["Checksum_PrimaryData"]["required"] is True
        assert attrs["Checksum_PrimaryData"]["server_required"] is False
        assert attrs["File_PrimaryData"]["required"] is True
        assert attrs["File_PrimaryData"]["server_required"] is True

    def test_falls_back_to_catalog_required_when_seek_lookup_raises(self):
        # The fail-safe direction under test: SEEK's table is unreachable
        # (any exception), so server_required must NOT default to "not
        # required" -- it must fall back to the catalog's own `required`,
        # the stricter of the two possible defaults, so the gate stays HARD
        # rather than silently loosening because of an outage.
        with patch("seek.models.Sample_types.objects") as sample_types:
            sample_types.filter.side_effect = Exception("seek db unreachable")
            attrs = {a["title"]: a for a in reingest_lookups.attributes_for("A.GEX")}

        assert attrs["Checksum_PrimaryData"]["required"] is True
        assert attrs["Checksum_PrimaryData"]["server_required"] is True
        assert attrs["File_PrimaryData"]["required"] is True
        assert attrs["File_PrimaryData"]["server_required"] is True

    def test_falls_back_to_catalog_required_when_seek_has_no_rows_for_a_known_type(self):
        # SEEK resolves no Sample_types row at all for this title (an
        # instance where the type is unresolvable on the SEEK side even
        # though the catalog knows it) -- an outage signal, not "nothing is
        # required here", so the same fallback applies.
        with patch("seek.models.Sample_types.objects") as sample_types:
            sample_types.filter.return_value.values_list.return_value = []
            attrs = {a["title"]: a for a in reingest_lookups.attributes_for("A.GEX")}

        assert attrs["Checksum_PrimaryData"]["server_required"] is True
        assert attrs["File_PrimaryData"]["server_required"] is True

    def test_falls_back_per_title_when_seek_is_reachable_but_silent_on_it(self):
        # SEEK is reachable and answers for File_PrimaryData but has no row
        # at all for Checksum_PrimaryData on this type -- still "we don't
        # know" for that one title specifically, not "SEEK says False".
        with patch("seek.models.Sample_types.objects") as sample_types, \
                patch("seek.models.Sample_attributes.objects") as sample_attrs:
            sample_types.filter.return_value.values_list.return_value = [2]
            sample_attrs.filter.return_value.values_list.return_value = [
                ("File_PrimaryData", 1),
            ]
            attrs = {a["title"]: a for a in reingest_lookups.attributes_for("A.GEX")}

        assert attrs["Checksum_PrimaryData"]["server_required"] is True  # fallback
        assert attrs["File_PrimaryData"]["server_required"] is True  # SEEK's own answer


def test_attributes_for_strict_matches_the_lenient_result_on_a_healthy_catalog():
    """Both walk the same catalog when nothing is wrong; they only diverge on
    a failure (see test_context_catalog.py's TestLoadSampleTypesStrict)."""
    # Guard against a vacuous pass: if A.GEX were absent from this test
    # environment's sample_types_context, both sides would return [] and the
    # equality below would hold having proved nothing about the real walk.
    assert reingest_lookups.attributes_for("A.GEX")
    assert reingest_lookups.attributes_for_strict("A.GEX") == \
        reingest_lookups.attributes_for("A.GEX")


def test_attributes_for_strict_is_empty_for_a_genuinely_unknown_type():
    assert reingest_lookups.attributes_for_strict("A.NOPE") == []


def test_attributes_for_strict_raises_when_the_catalog_is_unreachable():
    with patch("nextseek_api.services.context_catalog._sample_type_rows",
              side_effect=RuntimeError("sample_types_context table unreachable")):
        with pytest.raises(RuntimeError):
            reingest_lookups.attributes_for_strict("A.GEX")


def _metadata_row(uid: str, **fields) -> tuple:
    return (uid, json.dumps(fields))


class TestUidsByPrimaryData:
    def test_returns_a_list_for_an_unmatched_path(self):
        with patch("seek.models.Sample_types.objects") as sample_types, \
                patch("seek.models.Samples.objects") as samples:
            sample_types.filter.return_value.values_list.return_value = [7]
            samples.filter.return_value.values_list.return_value = []
            assert reingest_lookups.uids_by_primary_data(
                "/net/cluster/fastq/not-a-real-file.fastq.gz") == []

    def test_returns_the_uid_for_a_path_that_matches(self):
        with patch("seek.models.Sample_types.objects") as sample_types, \
                patch("seek.models.Samples.objects") as samples:
            sample_types.filter.return_value.values_list.return_value = [7]
            samples.filter.return_value.values_list.return_value = [
                _metadata_row("uid-1", File_PrimaryData="/net/cluster/fastq/a.fastq.gz"),
            ]
            assert reingest_lookups.uids_by_primary_data(
                "/net/cluster/fastq/a.fastq.gz") == ["uid-1"]

    def test_a_shorter_filename_does_not_match_inside_a_longer_one(self):
        # Minor finding: "a.fastq.gz" must not match "aa.fastq.gz".
        with patch("seek.models.Sample_types.objects") as sample_types, \
                patch("seek.models.Samples.objects") as samples:
            sample_types.filter.return_value.values_list.return_value = [7]
            samples.filter.return_value.values_list.return_value = [
                _metadata_row("uid-1", File_PrimaryData="/net/cluster/fastq/aa.fastq.gz"),
            ]
            assert reingest_lookups.uids_by_primary_data("a.fastq.gz") == []

    def test_default_types_scopes_the_type_filter_to_d_seq_only(self):
        # The historical, sole scope of this lookup, preserved as the
        # default so every caller that never passes `types` (there were no
        # others before this parameter existed) keeps searching exactly
        # what it always searched.
        with patch("seek.models.Sample_types.objects") as sample_types, \
                patch("seek.models.Samples.objects") as samples:
            sample_types.filter.return_value.values_list.return_value = [7]
            samples.filter.return_value.values_list.return_value = []
            reingest_lookups.uids_by_primary_data("/net/cluster/fastq/a.fastq.gz")
            sample_types.filter.assert_called_once_with(title__in=["D.SEQ"])

    def test_a_declared_wider_type_finds_a_parent_a_d_seq_only_search_would_miss(self):
        # The scenario the fix exists for: a parent that is itself an
        # already-analysed A.ALN sample (fed as input to a downstream
        # pipeline, e.g. hlatyping's `bam` column), not raw D.SEQ. A
        # D.SEQ-only search -- this lookup's old, hardcoded scope -- can
        # never see it; scoping to the pipeline's own declared
        # accepts_parent_types does. The mock's `side_effect` inspects the
        # actual filter kwargs so the two calls below can genuinely differ,
        # rather than both reading from one fixed `return_value`.
        path = "/net/cluster/runs/aln/PATIENT1.markdup.sorted.bam"
        type_ids_by_title = {"D.SEQ": 1, "A.ALN": 2}

        def type_filter(**kwargs):
            titles = set(kwargs.get("title__in", ()))
            mock = MagicMock()
            mock.values_list.return_value = [
                type_ids_by_title[t] for t in titles if t in type_ids_by_title]
            return mock

        def sample_filter(**kwargs):
            type_ids = set(kwargs.get("sample_type_id__in", ()))
            mock = MagicMock()
            rows = []
            if type_ids_by_title["A.ALN"] in type_ids:
                rows.append(_metadata_row("A.ALN-PARENT-1", File_PrimaryData=path))
            mock.values_list.return_value = rows
            return mock

        with patch("seek.models.Sample_types.objects") as sample_types, \
                patch("seek.models.Samples.objects") as samples:
            sample_types.filter.side_effect = type_filter
            samples.filter.side_effect = sample_filter

            # The old, D.SEQ-only scope finds nothing -- the real parent is
            # an A.ALN sample, invisible to a D.SEQ-only search.
            assert reingest_lookups.uids_by_primary_data(path) == []
            # Declaring the wider type finds it.
            assert reingest_lookups.uids_by_primary_data(
                path, types=("D.SEQ", "A.ALN")) == ["A.ALN-PARENT-1"]


class TestSampleTypesForUids:
    def test_omits_a_uid_it_could_not_fetch(self):
        # The safety property under test, same shape as
        # TestNotesForUids.test_omits_a_uid_it_could_not_fetch: a mix of one
        # UID the database knows and one it does not must keep the resolved
        # title and drop the other -- not merely "the whole call returns
        # {}", which would pass even against a stub that always returns {}.
        with patch("seek.models.Samples.objects") as samples, \
                patch("seek.models.Sample_types.objects") as sample_types:
            samples.filter.return_value.values_list.return_value = [
                ("seek-good-uid", 7),
            ]
            sample_types.filter.return_value.values_list.return_value = [(7, "D.SEQ")]
            result = reingest_lookups.sample_types_for_uids(
                ["seek-good-uid", "seek-missing-uid"])
        assert result == {"seek-good-uid": "D.SEQ"}
        assert "seek-missing-uid" not in result

    def test_a_uid_whose_type_id_names_no_known_sample_type_is_omitted(self):
        # A Samples row exists and carries a sample_type_id, but that id
        # resolves to no row in Sample_types (a dangling reference) -- must
        # not be reported as some empty-string type, or a caller could
        # mistake that for a genuinely resolved (but blank) title.
        with patch("seek.models.Samples.objects") as samples, \
                patch("seek.models.Sample_types.objects") as sample_types:
            samples.filter.return_value.values_list.return_value = [
                ("seek-dangling-uid", 99),
            ]
            sample_types.filter.return_value.values_list.return_value = []
            result = reingest_lookups.sample_types_for_uids(["seek-dangling-uid"])
        assert result == {}

    def test_returns_the_real_type_for_an_already_analysed_parent(self):
        # The scenario this lookup exists for: a resolved parent that is
        # itself an already-analysed A.ALN sample, not raw D.SEQ -- mapper.py
        # must be told this, never left to assume D.SEQ.
        with patch("seek.models.Samples.objects") as samples, \
                patch("seek.models.Sample_types.objects") as sample_types:
            samples.filter.return_value.values_list.return_value = [
                ("A.ALN-PARENT-1", 2),
            ]
            sample_types.filter.return_value.values_list.return_value = [(2, "A.ALN")]
            result = reingest_lookups.sample_types_for_uids(["A.ALN-PARENT-1"])
        assert result == {"A.ALN-PARENT-1": "A.ALN"}

    def test_returns_empty_dict_when_the_fetch_itself_fails(self):
        with patch("seek.models.Samples.objects") as samples:
            samples.filter.side_effect = Exception("samples table unreachable")
            assert reingest_lookups.sample_types_for_uids(["D.SEQ-NOT-A-REAL-UID"]) == {}

    def test_returns_empty_dict_when_the_type_title_fetch_fails(self):
        # A failure in the SECOND query (resolving type ids to titles) must
        # omit everything too, never report a partial result built only from
        # sample_type_id numbers with no title behind them.
        with patch("seek.models.Samples.objects") as samples, \
                patch("seek.models.Sample_types.objects") as sample_types:
            samples.filter.return_value.values_list.return_value = [("uid-1", 7)]
            sample_types.filter.side_effect = Exception("sample_types table unreachable")
            assert reingest_lookups.sample_types_for_uids(["uid-1"]) == {}

    def test_of_an_empty_list_is_empty(self):
        assert reingest_lookups.sample_types_for_uids([]) == {}


class TestNotesForUids:
    def test_omits_a_uid_it_could_not_fetch(self):
        # The safety property under test: a mix of UIDs where one resolves and
        # one does not must keep the good one's Notes and drop the bad one --
        # not merely "the whole call returns {}", which would pass even if
        # notes_for_uids were rewritten to always return {}. The database
        # itself never raises here: "seek-missing-uid" is simply absent from
        # the query result, the same as a real non-existent UID would be.
        with patch("seek.models.Samples.objects") as samples:
            samples.filter.return_value.values_list.return_value = [
                _metadata_row("seek-good-uid", Notes="curator wrote this"),
            ]
            result = reingest_lookups.notes_for_uids(["seek-good-uid", "seek-missing-uid"])
        assert result == {"seek-good-uid": "curator wrote this"}
        assert "seek-missing-uid" not in result

    def test_returns_empty_dict_when_the_fetch_itself_fails(self):
        with patch("seek.models.Samples.objects") as samples:
            samples.filter.side_effect = Exception("samples table unreachable")
            assert reingest_lookups.notes_for_uids(["D.SEQ-NOT-A-REAL-UID"]) == {}

    def test_of_an_empty_list_is_empty(self):
        assert reingest_lookups.notes_for_uids([]) == {}


class TestExistingUidsForPrimaryFilesStrict:
    """Which new reingest rows are already samples: same type, same Parent, same file."""

    LINK = "/net/runs/r1/variant_calling/S1/S1.vcf.gz"

    def _lookup(self, rows, files, type_ids=(7,)):
        with patch("seek.models.Sample_types.objects") as sample_types, \
                patch("seek.models.Samples.objects") as samples:
            sample_types.filter.return_value.values_list.return_value = list(type_ids)
            samples.filter.return_value.filter.return_value.values_list.return_value = rows
            return reingest_lookups.existing_uids_for_primary_files_strict("A.VCF", files)

    def test_the_same_link_and_parent_is_already_there(self):
        rows = [_metadata_row("A.VCF-1", Link_PrimaryData=self.LINK, Parent="D.SEQ-1")]
        assert self._lookup(rows, [{"link": self.LINK, "checksum": "", "parent": "D.SEQ-1"}]) \
            == [["A.VCF-1"]]

    def test_the_same_checksum_and_parent_is_already_there(self):
        rows = [_metadata_row("A.VCF-1", Link_PrimaryData="/elsewhere/S1.vcf.gz",
                              Checksum_PrimaryData="abc", Parent="D.SEQ-1")]
        assert self._lookup(rows, [{"link": self.LINK, "checksum": "abc", "parent": "D.SEQ-1"}]) \
            == [["A.VCF-1"]]

    def test_the_same_file_under_another_parent_is_not(self):
        # A per-run output (one matrix for every sample) is shared; the parent tells them apart.
        rows = [_metadata_row("A.VCF-1", Link_PrimaryData=self.LINK, Parent="D.SEQ-2")]
        assert self._lookup(rows, [{"link": self.LINK, "checksum": "", "parent": "D.SEQ-1"}]) \
            == [[]]

    def test_a_link_inside_a_longer_link_is_not_the_same_file(self):
        rows = [_metadata_row("A.VCF-1", Link_PrimaryData=self.LINK + ".tbi", Parent="D.SEQ-1")]
        assert self._lookup(rows, [{"link": self.LINK, "checksum": "", "parent": "D.SEQ-1"}]) \
            == [[]]

    def test_an_empty_checksum_matches_nothing(self):
        rows = [_metadata_row("A.VCF-1", Checksum_PrimaryData="", Parent="D.SEQ-1")]
        assert self._lookup(rows, [{"link": "", "checksum": "", "parent": "D.SEQ-1"}]) == [[]]

    def test_parents_compare_as_a_set(self):
        rows = [_metadata_row("A.VCF-1", Link_PrimaryData=self.LINK, Parent="D.SEQ-2; D.SEQ-1")]
        assert self._lookup(rows, [{"link": self.LINK, "checksum": "",
                                    "parent": "D.SEQ-1;D.SEQ-2"}]) == [["A.VCF-1"]]

    def test_a_type_this_instance_lacks_holds_no_samples(self):
        assert self._lookup([], [{"link": self.LINK, "checksum": "", "parent": "D.SEQ-1"}],
                            type_ids=()) == [[]]

    def test_raises_when_the_fetch_fails(self):
        # An outage must never read as "nothing there yet": the upload would duplicate.
        with patch("seek.models.Sample_types.objects") as sample_types:
            sample_types.filter.side_effect = Exception("unreachable")
            with pytest.raises(RuntimeError):
                reingest_lookups.existing_uids_for_primary_files_strict(
                    "A.VCF", [{"link": self.LINK, "checksum": "", "parent": "D.SEQ-1"}])

    def test_raises_on_metadata_it_cannot_parse(self):
        with pytest.raises(RuntimeError):
            self._lookup([("A.VCF-1", "{not json")],
                         [{"link": self.LINK, "checksum": "", "parent": "D.SEQ-1"}])

    def test_of_no_files_asks_nothing(self):
        assert reingest_lookups.existing_uids_for_primary_files_strict("A.VCF", []) == []


class TestAttributeValuesForUidsStrict:
    def test_returns_only_the_uids_that_hold_a_value(self):
        with patch("seek.models.Samples.objects") as samples:
            samples.filter.return_value.values_list.return_value = [
                _metadata_row("uid-held", FooRate=0.42),
                _metadata_row("uid-blank", FooRate=""),
                _metadata_row("uid-absent", Other="x"),
            ]
            result = reingest_lookups.attribute_values_for_uids_strict(
                ["uid-held", "uid-blank", "uid-absent"], "FooRate")
        assert result == {"uid-held": 0.42}

    def test_raises_when_the_fetch_fails(self):
        # Unlike notes_for_uids, an outage must never read as "nothing held":
        # the caller would then let a place overwrite a curated value.
        with patch("seek.models.Samples.objects") as samples:
            samples.filter.side_effect = Exception("samples table unreachable")
            with pytest.raises(RuntimeError):
                reingest_lookups.attribute_values_for_uids_strict(["uid-1"], "FooRate")

    def test_raises_on_metadata_it_cannot_parse(self):
        with patch("seek.models.Samples.objects") as samples:
            samples.filter.return_value.values_list.return_value = [("uid-1", "{not json")]
            with pytest.raises(RuntimeError):
                reingest_lookups.attribute_values_for_uids_strict(["uid-1"], "FooRate")

    def test_of_an_empty_list_is_empty(self):
        assert reingest_lookups.attribute_values_for_uids_strict([], "FooRate") == {}


# --- Which database each raw-SQL lookup reads --------------------------------
# SEEK's tables (samples, assay_assets, assays, projects_samples) live in the
# SEEK schema, behind the `seek` alias. The `default` (dmac) schema holds an
# empty `samples` table of the same name, created by migration 0001 because the
# mirror models are managed, so an unqualified query there finds nothing and
# says so quietly: no Assay rows, and every upload refused as project-less.
# internal_assays and assays_internal_assays, on the other hand, are
# NExtSEEK's, on `default` -- so the assay lookup is one cross-schema query on
# `default`, SEEK's tables qualified by name, the way
# dmac/dbtable_assaysinternalassays.py already joins them.

class _Recorder:
    """Stands in for django.db.connections: records the alias and SQL."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __getitem__(self, alias):
        recorder = self

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=None):
                recorder.calls.append((alias, sql, list(params or [])))

            def fetchall(self):
                return recorder.rows

            def fetchone(self):
                return recorder.rows[0] if recorder.rows else None

        conn = MagicMock()
        conn.cursor.return_value = _Cursor()
        return conn


def _seek_settings(settings):
    settings.SEEK_DATABASE = "seek"
    settings.DATABASES = {**settings.DATABASES,
                          "seek": {**settings.DATABASES["default"], "NAME": "seek_production"}}


def test_project_ids_are_read_from_the_seek_connection(settings):
    _seek_settings(settings)
    recorder = _Recorder(rows=[(14,)])
    with patch("django.db.connections", recorder):
        got = reingest_lookups.project_ids_for_uids_strict(["D.SEQ-1"])

    assert got == [14]
    [(alias, _sql, params)] = recorder.calls
    assert alias == "seek"
    assert params == ["D.SEQ-1"]


class _Sequence(_Recorder):
    """A _Recorder that answers each execute with the next rows in turn."""

    def __init__(self, *results):
        super().__init__(rows=None)
        self.results = list(results)

    def __getitem__(self, alias):
        conn = super().__getitem__(alias)
        recorder = self
        cursor = conn.cursor.return_value

        def fetchall():
            return recorder.results.pop(0)

        cursor.fetchall = fetchall
        return conn


def test_study_assays_group_by_study_and_name_parents_in_no_study(settings):
    _seek_settings(settings)
    # (parent uid, study id, study title, matching assay id or None)
    recorder = _Recorder(rows=[
        ("D.SEQ-1", 51, "Study A", 315), ("D.SEQ-2", 51, "Study A", 315),
        ("D.SEQ-3", 25, "Study B", None),
    ])
    with patch("django.db.connections", recorder):
        plan = reingest_lookups.study_assays_for_parents_strict(
            ["D.SEQ-1", "D.SEQ-2", "D.SEQ-3", "D.SEQ-4"], "Gene Expression Analysis")
    assert plan.studies == {25: [], 51: [315]}
    assert plan.parents_by_study == {25: ["D.SEQ-3"], 51: ["D.SEQ-1", "D.SEQ-2"]}
    assert plan.parents_without_study == ["D.SEQ-4"]
    assert plan.study_titles == {25: "Study B", 51: "Study A"}
    [(alias, sql, params)] = recorder.calls
    assert alias == "default"
    assert "`seek_production`.studies AS st" in sql
    assert "LEFT JOIN (" in sql
    assert params == ["Gene Expression Analysis", "D.SEQ-1", "D.SEQ-2", "D.SEQ-3", "D.SEQ-4"]


def test_study_assays_keep_two_matches_in_one_study(settings):
    _seek_settings(settings)
    recorder = _Recorder(rows=[("D.SEQ-1", 17, "S", 40), ("D.SEQ-1", 17, "S", 41)])
    with patch("django.db.connections", recorder):
        plan = reingest_lookups.study_assays_for_parents_strict(["D.SEQ-1"], "Gene Expression Analysis")
    assert plan.studies == {17: [40, 41]}


def test_study_assays_with_no_parents_ask_nothing():
    plan = reingest_lookups.study_assays_for_parents_strict([], "Genome Alignment")
    assert plan == reingest_lookups.StudyAssayPlan({}, {}, [], {})


def test_study_assays_raise_on_an_outage(settings):
    _seek_settings(settings)
    broken = MagicMock()
    broken.__getitem__.side_effect = OSError("down")
    with patch("django.db.connections", broken):
        with pytest.raises(RuntimeError):
            reingest_lookups.study_assays_for_parents_strict(["D.SEQ-1"], "Genome Alignment")


def test_house_assay_title_resolves_an_alternative_name():
    recorder = _Sequence(
        [("Variant Call Filtering", "Variant Calling Analysis, Variant Calling")],
        [("Variant Call Filtering",), ("Genome Alignment",)])
    with patch("django.db.connections", recorder):
        assert reingest_lookups.house_assay_title_strict("Variant Calling Analysis") == \
            "Variant Call Filtering"


def test_house_assay_title_is_none_when_no_title_matches():
    recorder = _Sequence([("Long-Read Sequencing", None)], [("Long Read Sequencing",)])
    with patch("django.db.connections", recorder):
        assert reingest_lookups.house_assay_title_strict("Long-Read Sequencing") is None


def test_house_assay_title_refuses_two_matching_titles():
    recorder = _Sequence(
        [("Single Cell Expression Analysis", "Single Cell Expression Matrix Analysis")],
        [("Single Cell Expression Analysis",), ("Single Cell Expression Matrix Analysis",)])
    with patch("django.db.connections", recorder):
        with pytest.raises(ValueError):
            reingest_lookups.house_assay_title_strict("Single Cell Expression Analysis")


# The study-assay lookups upload-reingest reads before it creates or adopts an assay.

def _down():
    broken = MagicMock()
    broken.__getitem__.side_effect = OSError("down")
    return broken


def test_assay_ids_in_study_are_the_tagged_assays_of_the_house_assay(settings):
    _seek_settings(settings)
    recorder = _Recorder(rows=[(315,), (468,)])
    with patch("django.db.connections", recorder):
        assert reingest_lookups.assay_ids_in_study_strict("51", "Genome Alignment") == [315, 468]
    [(alias, sql, params)] = recorder.calls
    assert alias == "default"
    assert "`seek_production`.assays AS a" in sql
    assert "JOIN assays_internal_assays AS j ON j.assay_id = a.id" in sql
    assert "ia.internal_assay_title = %s" in sql
    assert params == [51, "Genome Alignment"]


def test_assay_ids_in_study_raise_on_an_outage(settings):
    _seek_settings(settings)
    with patch("django.db.connections", _down()):
        with pytest.raises(RuntimeError):
            reingest_lookups.assay_ids_in_study_strict(51, "Genome Alignment")


def test_internal_assay_id_is_the_lowest_id_of_that_exact_title():
    recorder = _Recorder(rows=[(34,)])
    with patch("django.db.connections", recorder):
        assert reingest_lookups.internal_assay_id_strict("Genome Alignment") == 34
    [(alias, sql, params)] = recorder.calls
    assert alias == "default"
    assert "FROM internal_assays WHERE internal_assay_title = %s" in sql
    assert params == ["Genome Alignment"]


def test_internal_assay_id_is_none_when_no_house_assay_has_the_title():
    with patch("django.db.connections", _Recorder(rows=[(None,)])):
        assert reingest_lookups.internal_assay_id_strict("No Such Assay") is None


def test_internal_assay_id_raises_on_an_outage():
    with patch("django.db.connections", _down()):
        with pytest.raises(RuntimeError):
            reingest_lookups.internal_assay_id_strict("Genome Alignment")


def test_untagged_assays_in_study_match_the_title_exactly_and_have_no_house_tag(settings):
    _seek_settings(settings)
    title = "Genome Alignment - Data Linked"
    # The database compares titles without regard to case or trailing blanks; the lookup does not.
    recorder = _Recorder(rows=[(470, title), (471, title.lower()), (472, title + " "), (473, title)])
    with patch("django.db.connections", recorder):
        assert reingest_lookups.untagged_assay_ids_in_study_strict("51", title) == [470, 473]
    [(alias, sql, params)] = recorder.calls
    assert alias == "default"
    assert "`seek_production`.assays AS a" in sql
    assert "LEFT JOIN assays_internal_assays AS j ON j.assay_id = a.id" in sql
    assert "j.assay_id IS NULL" in sql
    assert params == [51, title]


def test_untagged_assays_in_study_raise_on_an_outage(settings):
    _seek_settings(settings)
    with patch("django.db.connections", _down()):
        with pytest.raises(RuntimeError):
            reingest_lookups.untagged_assay_ids_in_study_strict(51, "Genome Alignment - Data Linked")
