"""Tests for the DAG stage."""
import pytest

from nextseek_api.batch_upload.dag import (
    build_relationships,
    compute_directions,
    detect_cycles,
    extract_parents,
)
from nextseek_api.batch_upload.models import InputRowModel


class TestExtractParents:
    def test_single_parent(self):
        meta = '{"Parent":"NHP-220630FLY-1-PUB"}'
        result = extract_parents(meta)
        assert result == frozenset({"NHP-220630FLY-1-PUB"})

    def test_multiple_parents(self):
        meta = '{"Parent":"NHP-220630FLY-1-PUB;NHP-220630FLY-2-PUB"}'
        result = extract_parents(meta)
        assert len(result) == 2

    def test_no_parent_field(self):
        meta = '{"Name":"test"}'
        assert extract_parents(meta) == frozenset()

    def test_empty_parent(self):
        meta = '{"Parent":""}'
        assert extract_parents(meta) == frozenset()

    def test_invalid_json(self):
        assert extract_parents("not json") == frozenset()

    def test_invalid_uid_filtered(self):
        meta = '{"Parent":"invalid-uid"}'
        assert extract_parents(meta) == frozenset()

    def test_cache_returns_same(self):
        meta = '{"Parent":"NHP-220630FLY-1-PUB"}'
        r1 = extract_parents(meta)
        r2 = extract_parents(meta)
        assert r1 is r2  # same frozen set from cache


class TestExtractParentsRegexVariants:
    """Test the fixed regex: optional -PUB suffix, 2-5 char lab abbreviation."""

    def test_non_pub_uid(self):
        extract_parents.cache_clear()
        meta = '{"Parent":"NHP-260225MIT-6"}'
        assert "NHP-260225MIT-6" in extract_parents(meta)

    def test_pub_uid(self):
        extract_parents.cache_clear()
        meta = '{"Parent":"NHP-260225MIT-6-PUB"}'
        assert "NHP-260225MIT-6-PUB" in extract_parents(meta)

    def test_pub_with_number(self):
        extract_parents.cache_clear()
        meta = '{"Parent":"NHP-260225MIT-6-PUB2"}'
        assert "NHP-260225MIT-6-PUB2" in extract_parents(meta)

    def test_short_lab_abbrev(self):
        extract_parents.cache_clear()
        meta = '{"Parent":"NHP-260225MI-1"}'
        assert "NHP-260225MI-1" in extract_parents(meta)

    def test_long_lab_abbrev(self):
        extract_parents.cache_clear()
        meta = '{"Parent":"NHP-260225MITLL-1"}'
        assert "NHP-260225MITLL-1" in extract_parents(meta)

    def test_dotted_prefix_no_pub(self):
        extract_parents.cache_clear()
        meta = '{"Parent":"D.IMG-260225MIT-3"}'
        assert "D.IMG-260225MIT-3" in extract_parents(meta)

    def test_two_letter_ab_prefix(self):
        extract_parents.cache_clear()
        meta = '{"Parent":"AB-230327BOO-3"}'
        assert extract_parents(meta) == frozenset({"AB-230327BOO-3"})

    def test_m_dot_prefix_lmm(self):
        extract_parents.cache_clear()
        meta = '{"Parent":"M.LMM-231208ALT-1"}'
        assert extract_parents(meta) == frozenset({"M.LMM-231208ALT-1"})


class TestExtractParentsNameWithSpaces:
    """Names with spaces must not be fragmented by the split regex."""

    def test_name_with_spaces_not_split(self):
        """Parent name with spaces is not a valid UID, so result is empty — but NOT split into fragments."""
        extract_parents.cache_clear()
        meta = '{"Parent":"UtEC - 2015010902"}'
        assert extract_parents(meta) == frozenset()

    def test_name_with_spaces_alongside_uid(self):
        """A UID semicolon-separated from a name: only the UID survives filtering."""
        extract_parents.cache_clear()
        meta = '{"Parent":"NHP-260225MIT-1;UtEC - 2015010902"}'
        result = extract_parents(meta)
        assert result == frozenset({"NHP-260225MIT-1"})


class TestExtractParentsVariantKeys:
    """Test that extract_parents reads ALL parent-containing keys, not just 'Parent'."""

    def test_treatment1parent_key(self):
        extract_parents.cache_clear()
        meta = '{"Treatment1Parent":"NHP-260225MIT-1"}'
        assert extract_parents(meta) == frozenset({"NHP-260225MIT-1"})

    def test_antibody_parent_key(self):
        extract_parents.cache_clear()
        meta = '{"AntibodyParent":"ABP-230327BOO-3"}'
        assert extract_parents(meta) == frozenset({"ABP-230327BOO-3"})

    def test_parent_m_key(self):
        extract_parents.cache_clear()
        meta = '{"ParentM":"NHP-260225MIT-1"}'
        assert extract_parents(meta) == frozenset({"NHP-260225MIT-1"})

    def test_multiple_variant_keys_merged(self):
        extract_parents.cache_clear()
        meta = '{"Parent":"NHP-260225MIT-1","Treatment1Parent":"NHP-260225MIT-2"}'
        result = extract_parents(meta)
        assert result == frozenset({"NHP-260225MIT-1", "NHP-260225MIT-2"})

    def test_variant_key_with_invalid_uid_filtered(self):
        extract_parents.cache_clear()
        meta = '{"Treatment1Parent":"not-a-valid-uid"}'
        assert extract_parents(meta) == frozenset()

    def test_variant_key_semicolon_values(self):
        extract_parents.cache_clear()
        meta = '{"Treatment1Parent":"NHP-260225MIT-1;NHP-260225MIT-2"}'
        result = extract_parents(meta)
        assert result == frozenset({"NHP-260225MIT-1", "NHP-260225MIT-2"})

    def test_dedup_across_parent_and_variant(self):
        extract_parents.cache_clear()
        meta = '{"Parent":"NHP-260225MIT-1","Treatment1Parent":"NHP-260225MIT-1"}'
        result = extract_parents(meta)
        assert result == frozenset({"NHP-260225MIT-1"})


class TestBuildRelationships:
    def _make_row(self, uid, parent_meta="{}"):
        return InputRowModel(
            UID=uid, SampleType="T", json_metadata=parent_meta, assay_ids=[]
        )

    def test_basic(self):
        rows = [
            self._make_row("NHP-220630FLY-2-PUB", '{"Parent":"NHP-220630FLY-1-PUB"}'),
            self._make_row("NHP-220630FLY-1-PUB"),
        ]
        parents_of, children_of, edges = build_relationships(rows)
        assert "NHP-220630FLY-1-PUB" in parents_of.get("NHP-220630FLY-2-PUB", set())
        assert ("NHP-220630FLY-1-PUB", "NHP-220630FLY-2-PUB") in edges

    def test_no_parents(self):
        rows = [self._make_row("NHP-220630FLY-1-PUB")]
        parents_of, children_of, edges = build_relationships(rows)
        assert len(edges) == 0


class TestComputeDirections:
    def _make_row(self, uid, assay_ids, parent_meta="{}"):
        return InputRowModel(
            UID=uid, SampleType="T", json_metadata=parent_meta, assay_ids=assay_ids
        )

    def test_source_direction(self):
        """Child has assay, parent doesn't -> direction 1 (parent/input)."""
        rows = [
            self._make_row("NHP-220630FLY-1-PUB", []),  # parent, no assays
            self._make_row("NHP-220630FLY-2-PUB", [100], '{"Parent":"NHP-220630FLY-1-PUB"}'),
        ]
        dc = compute_directions(rows)
        assert dc.direction_by_pair.get(("NHP-220630FLY-2-PUB", 100)) == 1

    def test_target_direction(self):
        """Both have same assay -> direction 2 (child/output)."""
        rows = [
            self._make_row("NHP-220630FLY-1-PUB", [100]),  # parent has assay
            self._make_row("NHP-220630FLY-2-PUB", [100], '{"Parent":"NHP-220630FLY-1-PUB"}'),
        ]
        dc = compute_directions(rows)
        assert dc.direction_by_pair.get(("NHP-220630FLY-2-PUB", 100)) == 2

    def test_empty_rows(self):
        dc = compute_directions([])
        assert dc.direction_by_pair == {}


class TestDetectCycles:
    def test_no_cycle(self):
        edges = {("A", "B"), ("B", "C")}
        assert detect_cycles(edges) == []

    def test_simple_cycle(self):
        edges = {("A", "B"), ("B", "A")}
        cycles = detect_cycles(edges)
        assert len(cycles) >= 1

    def test_longer_cycle(self):
        edges = {("A", "B"), ("B", "C"), ("C", "A")}
        cycles = detect_cycles(edges)
        assert len(cycles) >= 1
