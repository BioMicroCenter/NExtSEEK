"""The parser's lists of known investigation and project titles, built from the box's own graph.

The titles differ by box, so the prompt and schema carry placeholders and this fills them when the parser is called.
``graph_catalog.get_vocabulary`` is cached, so no extra graph read happens per question and none at boot. When the
graph cannot be read the lists simply name no titles.

The lab words people type are aliases for the titles a box really has. An alias resolves only to titles that exist on
this box, never to another box's title.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

INVESTIGATIONS = "{{KNOWN_INVESTIGATION_TITLES}}"
PROJECTS = "{{KNOWN_PROJECT_TITLES}}"

#: lab word -> the titles it can mean, per level. The word itself also counts when a box has it as a title.
ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "Griffith": {"investigation": ("Endometriosis",), "project": ("CGR-Endo",)},
    "Impact": {"investigation": ("Impactb Investigation",), "project": ("IMPAcTb",)},
    "SRP": {"investigation": ("MIT_SRP",), "project": ("MIT_SRP",)},
    "Shoulders": {"investigation": ("Collagen Study",), "project": ("Shoulders Independent Projects",)},
}


def resolve_aliases(titles, level: str) -> dict[str, list[str]]:
    """Lab word -> the titles of ``level`` this box has for it (words with none are left out)."""
    have = {t.lower(): t for t in titles}
    out: dict[str, list[str]] = {}
    for word, levels in ALIASES.items():
        found = [have[c.lower()] for c in (word, *levels.get(level, ())) if c.lower() in have]
        if found:
            out[word] = list(dict.fromkeys(found))
    return out


def render(titles, level: str) -> str:
    """"A, B, C. People also say: Griffith for Endometriosis." or "none read from this graph"."""
    titles = sorted({t for t in titles if isinstance(t, str) and t.strip()})
    if not titles:
        return "none could be read from this instance's graph"
    text = ", ".join(titles)
    said = [f"{w} for {' or '.join(others)}" for w, ts in resolve_aliases(titles, level).items()
            if (others := [t for t in ts if t != w])]
    return text + (f". People also say: {'; '.join(said)}" if said else "")


def fill(config, text: str, escape=lambda s: s) -> str:
    """``text`` with the two placeholders replaced from this box's vocabulary (no placeholder, no read)."""
    if not isinstance(text, str) or (INVESTIGATIONS not in text and PROJECTS not in text):
        return text
    try:
        from . import graph_catalog
        vocab = graph_catalog.get_vocabulary(config)
        inv, proj = vocab.investigation_titles, vocab.project_titles
    except Exception as exc:  # the graph is down or the caller has no scope: name no titles
        log.warning("known titles: vocabulary unavailable (%s)", exc)
        inv, proj = (), ()
    return (text.replace(INVESTIGATIONS, escape(render(inv, "investigation")))
            .replace(PROJECTS, escape(render(proj, "project"))))


def schema_json(config, data) -> str:
    """``json.dumps(data, indent=2)`` with the placeholders filled (escaped for JSON)."""
    return fill(config, json.dumps(data, indent=2), lambda s: json.dumps(s)[1:-1])
