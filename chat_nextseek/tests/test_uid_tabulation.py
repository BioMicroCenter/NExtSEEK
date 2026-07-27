"""UID tabulation must handle the real UID shape, suffix and all.

Observed 2026-07-24: "How many samples did MetNet upload in 2024" returned
`unparsable_uids: 3924` out of 3924 rows and an EMPTY years_table, so the answer
was "3,924 uploaded samples. The available summary does not contain a specific
breakdown for the year 2024." The year filter had not been dropped by the
parser — every UID simply failed to parse, because production UIDs carry a
`-PUB` / `-PUB1` publication suffix that the regex did not allow.
"""
from __future__ import annotations

from chat_nextseek.reports.runners import _tabulate_sample_uuids

REAL_UIDS = [
    "DNA-240612KAM-9-PUB",
    "DNA-240612KAM-8-PUB",
    "NHP-220913SED-15-PUB1",
    "A.ADCD-250312ALT-1-PUB",
    "TIS-220914SED-29-PUB1",
    "MUS-200901ENG-23",          # no suffix: must still parse
]


def test_published_uids_are_parsable():
    out = _tabulate_sample_uuids(REAL_UIDS)
    assert out["unparsable_uids"] == 0


def test_years_are_tabulated_from_published_uids():
    out = _tabulate_sample_uuids(REAL_UIDS)
    # 24: the two DNA-2406..; 22: NHP-2209.. + TIS-2209..; 25: A.ADCD-2503..; 20: MUS-2009..
    assert out["years_table"] == {"22": 2, "24": 2, "25": 1, "20": 1}


def test_sampletype_and_lab_survive_the_suffix():
    out = _tabulate_sample_uuids(["DNA-240612KAM-9-PUB", "A.ADCD-250312ALT-1-PUB"])
    assert out["sampletypes_table"] == {"A.ADCD": 1, "DNA": 1}
    assert out["labs_table"] == {"ALT": 1, "KAM": 1}


def test_genuinely_malformed_uids_are_still_counted():
    out = _tabulate_sample_uuids(["not-a-uid", "DNA-240612KAM-9-PUB"])
    assert out["unparsable_uids"] == 1
    assert out["years_table"] == {"24": 1}
