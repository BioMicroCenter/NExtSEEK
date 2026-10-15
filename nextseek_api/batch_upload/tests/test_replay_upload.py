"""A user re-uploading a half-done sheet inserts nothing twice (the replay route of the durable upload queue)."""
import json
import re

from nextseek_api.batch_upload.identity import extract_identity, hash_identity
from nextseek_api.batch_upload.models import InputRowModel
from nextseek_api.batch_upload.tests.test_orchestrator_levels import FakeResultProxy
from nextseek_api.batch_upload.tests.test_orchestrator_levels import (  # noqa: F401
    FakeDB,
    _isolate_prefetch_caches,
    _make_row,
    _run_orchestrator,
)

A, B, C = "NHP-260101TST-1", "NHP-260101TST-2", "NHP-260101TST-3"
CHILD = "NHP-260101TST-4"


def test_replaying_a_half_done_upload_inserts_nothing_twice():
    # Root rows (A, B, C) and a child of A: the child is level 1.
    rows = [_make_row(A), _make_row(B), _make_row(C), _make_row(CHILD, parent_uid=A)]
    db = FakeDB()

    # First run stops once level 0 is in (the worker died before level 1).
    _run_orchestrator(rows, db, should_stop=lambda: A in db.inserted_samples)
    assert set(db.inserted_samples) == {A, B, C}

    # The user uploads the same sheet again.
    second = _run_orchestrator(rows, db)

    assert set(db.inserted_samples) == {A, B, C, CHILD}
    every_insert = [uid for call in db.insert_sample_calls for uid in call]
    assert sorted(every_insert) == sorted({A, B, C, CHILD}), "an identity was inserted twice"
    assert second["totals"]["success"] == 1
    assert second["totals"]["skipped"] == 3
    assert second["totals"]["failed"] == 0


class NameFakeDB(FakeDB):
    """FakeDB plus the samples.name_identity column the NAME_CHECK reads (a generated column in MySQL)."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.name_index = {}  # hash -> (uuid, id)

    def route(self, sql_text, params):
        sql = sql_text.strip()
        if "name_identity IN" in sql:
            rows = [(u, i, h) for h, (u, i) in self.name_index.items() if h in params.values()]
            return FakeResultProxy(rows=rows)
        before = set(self.inserted_samples)
        out = super().route(sql_text, params)
        if "INSERT INTO samples" in sql:
            for k, uid in params.items():
                m = re.fullmatch(r"uuid_(\d+)", k)
                if m and uid in self.inserted_samples and uid not in before:
                    meta = json.loads(params[f"json_metadata_{m.group(1)}"] or "{}")
                    ident = extract_identity(meta, uid=uid)
                    if ident:
                        self.name_index[hash_identity(ident)] = (uid, self.inserted_samples[uid])
        return out


def _name_row(name):
    return InputRowModel(UID=None, SampleType="NHP_blood", json_metadata=json.dumps({"Name": name}), assay_ids=[])


def test_replaying_a_half_done_upload_does_not_insert_a_name_only_row_twice(monkeypatch):
    counters = {}

    def fake_reserve(prefix, n, floor):  # the committed counter, in memory
        first = max(counters.get(prefix, 0), floor) + 1
        counters[prefix] = first + n - 1
        return first

    monkeypatch.setattr("nextseek_api.batch_upload.uid_gen.reserve_uid_indexes", fake_reserve)
    def sheet():  # fresh row objects per run, as a re-upload re-reads the file: no UIDs minted by run 1
        return [_make_row(A), _name_row("mouse one"), _name_row("mouse two"), _make_row(CHILD, parent_uid=A)]

    db = NameFakeDB(sample_attributes={1: {"Parent", "Name", "UID"}})

    _run_orchestrator(sheet(), db, should_stop=lambda: len(db.inserted_samples) >= 3)
    assert len(db.name_index) == 2, "the first run saved both Name-only rows"

    second = _run_orchestrator(sheet(), db)

    every_insert = [uid for call in db.insert_sample_calls for uid in call]
    assert len(every_insert) == len(set(every_insert)), "an identity was inserted twice"
    assert len(every_insert) == 4, "A, the child and the two Name-only rows are inserted once each"
    assert len(db.name_index) == 2
    assert second["totals"]["failed"] == 0
