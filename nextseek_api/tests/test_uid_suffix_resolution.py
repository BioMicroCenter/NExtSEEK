"""A UID resolves with or without its publication suffix, on every path (F14, ruling D2).

A published sample's UID carries `-PUB` in SEEK. Researchers write it either way, and so do
the UIDs they copy out of a paper or an earlier reply. The graph path has resolved both
spellings since `chat_nextseek.helpers.uid_check` was added, and names the one it found. Every
REST path resolved the string exactly as given, through a private `_resolve_uid_to_seek_id`
copied into each service, so a `-PUB` UID 404ed: thirteen questions in the 2026-09-10
production review hit the right endpoint and failed on the spelling alone, with no product
defect behind any of them.

Nothing here reaches a database: the resolver takes the caller's own lookup.
"""
from __future__ import annotations

import pytest

from nextseek_api.services.uid_suffix import PUB_SUFFIX, resolve_uid_with_suffix, uid_spellings


class TestSpellings:
    def test_a_bare_uid_also_tries_the_suffixed_one(self):
        assert uid_spellings("D.SEQ-240910LAU-1") == ["D.SEQ-240910LAU-1", "D.SEQ-240910LAU-1-PUB"]

    def test_a_suffixed_uid_also_tries_the_bare_one(self):
        assert uid_spellings("D.SEQ-240910LAU-1-PUB") == ["D.SEQ-240910LAU-1-PUB", "D.SEQ-240910LAU-1"]

    def test_what_the_user_wrote_is_always_tried_first(self):
        """An exact match must never be displaced by a guess."""
        for uid in ("A-1", "A-1-PUB"):
            assert uid_spellings(uid)[0] == uid

    def test_a_numeric_id_has_nothing_to_suffix(self):
        assert uid_spellings("4711") == ["4711"]

    def test_an_empty_uid_yields_nothing_to_try(self):
        assert uid_spellings("") == []
        assert uid_spellings(None) == []

    def test_the_suffix_is_recognised_whatever_its_case(self):
        assert uid_spellings("A-1-pub") == ["A-1-pub", "A-1"]

    def test_no_spelling_is_tried_twice(self):
        for uid in ("A-1", "A-1-PUB", "4711"):
            assert len(uid_spellings(uid)) == len(set(uid_spellings(uid))), uid


class TestResolution:
    def test_a_suffixed_uid_resolves_when_only_the_bare_one_exists(self):
        seen: list[str] = []

        def lookup(spelling):
            seen.append(spelling)
            return "99" if spelling == "PAT-230522GRI-7" else None

        found, spelling = resolve_uid_with_suffix("PAT-230522GRI-7-PUB", lookup)

        assert found == "99"
        assert spelling == "PAT-230522GRI-7", "the reply can name what it actually resolved"
        assert seen == ["PAT-230522GRI-7-PUB", "PAT-230522GRI-7"]

    def test_a_bare_uid_resolves_when_only_the_suffixed_one_exists(self):
        found, spelling = resolve_uid_with_suffix(
            "PAT-230522GRI-7", lambda s: "42" if s.endswith(PUB_SUFFIX) else None)
        assert (found, spelling) == ("42", "PAT-230522GRI-7" + PUB_SUFFIX)

    def test_an_exact_match_wins_and_stops_the_search(self):
        seen: list[str] = []

        def lookup(spelling):
            seen.append(spelling)
            return "7"

        found, spelling = resolve_uid_with_suffix("A-1", lookup)

        assert (found, spelling) == ("7", "A-1")
        assert seen == ["A-1"], "no alternative is tried once the given spelling resolves"

    def test_a_uid_that_exists_in_neither_spelling_is_not_found(self):
        assert resolve_uid_with_suffix("NOPE-1", lambda _s: None) == (None, None)

    def test_a_lookup_that_raises_is_treated_as_a_miss(self):
        """The per-service resolvers this replaces all swallowed their own exceptions."""
        def lookup(spelling):
            if spelling.endswith(PUB_SUFFIX):
                return "5"
            raise RuntimeError("SEEK is down for this one")

        assert resolve_uid_with_suffix("A-1", lookup) == ("5", "A-1" + PUB_SUFFIX)

    @pytest.mark.parametrize("empty", ["", None])
    def test_an_empty_uid_never_calls_the_lookup(self, empty):
        calls = []
        assert resolve_uid_with_suffix(empty, lambda s: calls.append(s)) == (None, None)
        assert calls == []
