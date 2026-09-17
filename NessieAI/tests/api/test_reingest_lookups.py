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
