"""P3 (#28 review): an empty semantic endpoint shortlist falls back to the full catalog, never "[]"."""
from __future__ import annotations

import json
from types import SimpleNamespace

from chat_nextseek.agents import parser as parser_mod

CATALOG = [{"path": "/nextseek_api/a/"}, {"path": "/nextseek_api/b/"}, {"path": "/nextseek_api/c/"}]


def _config(scored, ready=True):
    index = SimpleNamespace(ready=ready, query=lambda text, top_n: scored)
    return SimpleNamespace(ENDPOINT_INDEX=index, MIN_API_ENDPOINTS=CATALOG, SEMANTIC_MIN_K=1)


def test_an_empty_shortlist_falls_back_to_the_full_catalog():
    out = parser_mod._endpoints_for_prompt(_config([]), "how many samples")
    assert out == json.dumps(CATALOG, indent=2)


def test_a_non_empty_shortlist_is_unchanged():
    scored = [(CATALOG[1], 0.9, None), (CATALOG[0], 0.2, None)]
    assert json.loads(parser_mod._endpoints_for_prompt(_config(scored), "b things")) == [CATALOG[1]]


def test_an_index_that_is_not_ready_sends_the_full_catalog():
    assert parser_mod._endpoints_for_prompt(_config([], ready=False), "q") == json.dumps(CATALOG, indent=2)
