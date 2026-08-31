"""Resolution gates, tested against a fake SQLAlchemy connection.

The house pattern for SQL-layer tests in this repo is a MagicMock connection
with asserted calls (see batch_upload/tests/test_associations.py). Here we use
a small scripted fake instead, because the resolver issues several distinct
queries and the tests are about which ROWS come back, not which SQL is sent.
"""

from unittest.mock import MagicMock

from nextseek_api.assay_registration.resolver import resolve
from nextseek_api.assay_registration.schemas import RegistrationRow


class FakeConn:
    """Answers each query by matching its SQL, never by call position.

    A positional queue looks simpler and is wrong here: resolve() issues a
    VARIABLE number of queries, because every helper early-returns on an empty
    input list and sample_ids_for_uids is skipped entirely when no uid is
    unique. One skipped query then shifts every later answer by one slot, and
    the failure looks like a resolver bug rather than a fixture bug.
    """

    def __init__(self, uid_counts, sample_ids, sample_projects,
                 assay_projects, title_assays):
        self._by_marker = [
            ("COUNT(*) FROM", uid_counts),
            ("SELECT uuid, id FROM", sample_ids),
            ("projects_samples", sample_projects),
            ("internal_assays", title_assays),   # checked before plain `assays`
            ("investigations_projects", assay_projects),
        ]
        self.calls = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        for marker, rows in self._by_marker:
            if marker in sql:
                result = MagicMock()
                result.fetchall.return_value = rows
                return result
        raise AssertionError(f"unexpected query in test fake:\n{sql}")


def _conn(uid_counts, sample_ids, sample_projects, assay_projects, title_assays):
    return FakeConn(uid_counts, sample_ids, sample_projects,
                    assay_projects, title_assays)


class TestUidGate:
    def test_a_uid_matching_two_rows_is_skipped_not_resolved(self):
        """The chunk-06 failure mode.

        __retrieveSampleByUID returns a record only when len(records) == 1, so
        two matching rows resolve to None indistinguishably from zero, and the
        None reaches `if sample_id>0:` at dbtable_sample.py:1564 and raises
        TypeError, 500ing the whole batch. The gate must ask "does exactly one
        row have this uid", never "does one exist".
        """
        conn = _conn(
            uid_counts=[("DUP", 2)],
            sample_ids=[],
            sample_projects=[],
            assay_projects=[],
            title_assays=[],
        )
        rows = [RegistrationRow(sample_uid="DUP", assay_id=351)]
        [resolved] = resolve(rows, conn)
        assert resolved.sample_id is None
        assert resolved.error.code == "sample_uid_not_unique"
        assert "2" in resolved.error.message
        assert resolved.error.submitted_identifier == "DUP"

    def test_a_missing_uid_is_reported_distinctly(self):
        conn = _conn([], [], [], [], [])
        rows = [RegistrationRow(sample_uid="GONE", assay_id=351)]
        [resolved] = resolve(rows, conn)
        assert resolved.error.code == "sample_uid_not_found"

    def test_a_duplicate_uid_does_not_stop_the_other_rows(self):
        """Partial with honest receipts, at the gate that used to 500 the batch."""
        conn = _conn(
            uid_counts=[("GOOD", 1), ("DUP", 2)],
            sample_ids=[("GOOD", 100)],
            sample_projects=[(100, 3)],
            assay_projects=[(351, 3)],
            title_assays=[],
        )
        rows = [RegistrationRow(sample_uid="GOOD", assay_id=351),
                RegistrationRow(sample_uid="DUP", assay_id=351)]
        good, dup = resolve(rows, conn)
        assert good.error is None and good.assay_id == 351
        assert dup.error.code == "sample_uid_not_unique"


class TestProjectGate:
    def test_assay_id_in_another_project_is_rejected(self):
        """The 578-row class from the 2026-08-26 audit.

        SEEK assay ids are per project. Writing a cross-project membership is
        unrecoverable: the sample joins a project it does not belong to.
        """
        conn = _conn(
            uid_counts=[("S1", 1)],
            sample_ids=[("S1", 100)],
            sample_projects=[(100, 3)],
            assay_projects=[(76, 7)],
            title_assays=[],
        )
        rows = [RegistrationRow(sample_uid="S1", assay_id=76)]
        [resolved] = resolve(rows, conn)
        assert resolved.error.code == "assay_project_mismatch"
        assert "7" in resolved.error.message and "3" in resolved.error.message

    def test_an_unknown_assay_id_is_reported_distinctly(self):
        """Locks `assay_not_found` apart from `assay_project_mismatch`.

        Without this test, deleting the `if not assay_prj:` guard leaves all
        the other tests green: `projects & set()` is empty, control falls
        through, and the row reports assay_project_mismatch saying the assay
        "belongs to project(s) []". That is exactly the collapse the spec
        forbids, and nothing else would catch it.
        """
        conn = _conn(
            uid_counts=[("S1", 1)],
            sample_ids=[("S1", 100)],
            sample_projects=[(100, 3)],
            assay_projects=[],
            title_assays=[],
        )
        rows = [RegistrationRow(sample_uid="S1", assay_id=999)]
        [resolved] = resolve(rows, conn)
        assert resolved.error.code == "assay_not_found"
        assert resolved.error.submitted_identifier == "999"

    def test_assay_id_in_the_samples_project_resolves(self):
        conn = _conn(
            uid_counts=[("S1", 1)],
            sample_ids=[("S1", 100)],
            sample_projects=[(100, 3)],
            assay_projects=[(131, 3)],
            title_assays=[],
        )
        rows = [RegistrationRow(sample_uid="S1", assay_id=131)]
        [resolved] = resolve(rows, conn)
        assert resolved.error is None
        assert (resolved.sample_id, resolved.assay_id, resolved.project_id) == (100, 131, 3)

    def test_a_sample_in_no_project_is_skipped(self):
        conn = _conn(
            uid_counts=[("S1", 1)],
            sample_ids=[("S1", 100)],
            sample_projects=[],
            assay_projects=[(131, 3)],
            title_assays=[],
        )
        rows = [RegistrationRow(sample_uid="S1", assay_id=131)]
        [resolved] = resolve(rows, conn)
        assert resolved.error.code == "sample_has_no_project"


class TestTitleResolution:
    def test_a_title_resolves_to_the_assay_in_the_samples_project(self):
        conn = _conn(
            uid_counts=[("S1", 1)],
            sample_ids=[("S1", 100)],
            sample_projects=[(100, 3)],
            assay_projects=[],
            title_assays=[("imaging", 76, 7, "Imaging"), ("imaging", 131, 3, "Imaging")],
        )
        rows = [RegistrationRow(sample_uid="S1", assay="Imaging")]
        [resolved] = resolve(rows, conn)
        assert resolved.error is None
        assert resolved.assay_id == 131, "must pick the sample's own project's assay"

    def test_an_ambiguous_title_lists_the_candidates(self):
        """47 (internal assay, project) pairs are ambiguous on the reference
        data, so the caller's only recourse is to retry with an explicit
        assay_id. The message must therefore name the candidates."""
        conn = _conn(
            uid_counts=[("S1", 1)],
            sample_ids=[("S1", 100)],
            sample_projects=[(100, 1)],
            assay_projects=[],
            title_assays=[
                ("antibody functional profiling", 64, 1, "Antibody Functional Profiling"),
                ("antibody functional profiling", 160, 1, "Antibody Functional Profiling"),
            ],
        )
        rows = [RegistrationRow(sample_uid="S1", assay="Antibody Functional Profiling")]
        [resolved] = resolve(rows, conn)
        assert resolved.error.code == "assay_ambiguous_in_project"
        assert "64" in resolved.error.message
        assert "160" in resolved.error.message

    def test_a_title_with_no_assay_in_that_project_is_reported_distinctly(self):
        conn = _conn(
            uid_counts=[("S1", 1)],
            sample_ids=[("S1", 100)],
            sample_projects=[(100, 3)],
            assay_projects=[],
            title_assays=[("imaging", 76, 7, "Imaging")],
        )
        rows = [RegistrationRow(sample_uid="S1", assay="Imaging")]
        [resolved] = resolve(rows, conn)
        assert resolved.error.code == "assay_not_in_sample_project"

    def test_an_unknown_title_is_reported_distinctly(self):
        conn = _conn(
            uid_counts=[("S1", 1)],
            sample_ids=[("S1", 100)],
            sample_projects=[(100, 3)],
            assay_projects=[],
            title_assays=[],
        )
        rows = [RegistrationRow(sample_uid="S1", assay="Nonexistent Assay")]
        [resolved] = resolve(rows, conn)
        assert resolved.error.code == "internal_assay_not_found"

    def test_title_matching_is_case_insensitive(self):
        conn = _conn(
            uid_counts=[("S1", 1)],
            sample_ids=[("S1", 100)],
            sample_projects=[(100, 3)],
            assay_projects=[],
            title_assays=[("imaging", 131, 3, "Imaging")],
        )
        rows = [RegistrationRow(sample_uid="S1", assay="  IMAGING ")]
        [resolved] = resolve(rows, conn)
        assert resolved.error is None
        assert resolved.assay_id == 131


class TestBatching:
    def test_resolution_is_a_fixed_number_of_queries_regardless_of_row_count(self):
        """Defect 4: validate up front with set queries, never per row.

        The bound below is "at most 5 for a submission that fits in one CHUNK",
        not an absolute. 500 uids fit inside CHUNK=1000, so the helpers issue one
        statement each. A correct implementation handed 1500 rows would issue
        more, because chunking is per statement — do not read a higher count at
        larger sizes as a regression.

        Note this exercises the numeric-id form only. The title path's freedom
        from per-row queries rests on the structural fact that neither
        `_resolve_by_id` nor `_resolve_by_title` accepts a connection at all.
        """
        conn = _conn(
            uid_counts=[("S%d" % i, 1) for i in range(500)],
            sample_ids=[("S%d" % i, 100 + i) for i in range(500)],
            sample_projects=[(100 + i, 3) for i in range(500)],
            assay_projects=[(131, 3)],
            title_assays=[],
        )
        rows = [RegistrationRow(sample_uid="S%d" % i, assay_id=131) for i in range(500)]
        resolved = resolve(rows, conn)
        assert len(resolved) == 500
        assert all(r.error is None for r in resolved)
        assert len(conn.calls) <= 5, (
            "resolution must be a fixed set of batch queries, not one per row"
        )
