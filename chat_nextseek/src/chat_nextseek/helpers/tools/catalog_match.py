"""Fuzzy catalog matching for entity / parser shortlisting. Moved from helpers.py during the Phase 2 src/ restructure."""
from __future__ import annotations

import re


def _norm_text(text: str) -> str:
    """
    Normalize free text for matching by lowercasing, stripping non-alphanumerics, and collapsing spaces.
    Keeps comparisons stable across user input and catalog fields regardless of punctuation or capitalization.
    """
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokenize(text: str) -> set[str]:
    """
    Tokenize normalized text into a set with simple plural/ies reductions.
    Expands tokens to catch small inflection variants while keeping output deterministic.
    """
    tokens = set(_norm_text(text).split())
    expanded: set[str] = set()
    for t in tokens:
        expanded.add(t.rstrip("s"))
        if t.endswith("ies"):
            expanded.add(t[:-3] + "y")
    return {t for t in tokens | expanded if t}


def _doc_from_sampletype(st: dict) -> tuple[str, set[str]]:
    """
    Build a descriptive string and token set from a sampletype entry for fuzzy matching.
    Combines code, name, description, tags, and a cleaned code variant to improve overlap detection.
    """
    parts = [
        st.get("SampleType") or st.get("code") or "",
        st.get("Name") or st.get("name") or "",
        st.get("Description") or st.get("description") or "",
        st.get("Tags") or st.get("tags") or "",
    ]
    code_raw = st.get("SampleType") or st.get("code") or ""
    code_clean = re.sub(r"[^a-zA-Z0-9]+", "", str(code_raw))
    if code_clean and code_clean != code_raw:
        parts.append(code_clean)
    doc = " ".join(str(p) for p in parts if p)
    return doc, _tokenize(doc)


def _doc_from_assay(assay: dict) -> tuple[str, set[str]]:
    """
    Build a descriptive string and token set from an assay entry for matching.
    Uses name, description, and synonym-like fields so scoring can align user phrasing with catalog values.
    """
    parts = [
        assay.get("Name") or assay.get("name") or "",
        assay.get("Description") or assay.get("description") or "",
        assay.get("Tags") or assay.get("tags") or "",
        assay.get("Alternative Assay Names") or assay.get("alternative_assay_names") or "",
    ]
    doc = " ".join(str(p) for p in parts if p)
    return doc, _tokenize(doc)


def _score_pair(query_norm: str, doc_norm: str, overlap_pct: float, code_bonus: float) -> float:
    """
    Compute a blended similarity score using fuzzy matching, token overlap, and an optional code bonus.
    Falls back to difflib when rapidfuzz is unavailable to keep scoring stable across environments.
    """
    if not query_norm or not doc_norm:
        return overlap_pct + code_bonus
    try:
        from rapidfuzz import fuzz

        base = float(fuzz.token_set_ratio(query_norm, doc_norm))
    except Exception:
        # Fallback: basic ratio if rapidfuzz unavailable
        from difflib import SequenceMatcher

        base = SequenceMatcher(None, query_norm, doc_norm).ratio() * 100.0

    # Blend fuzzy similarity and token overlap; add small bonus for explicit code matches
    return base * 0.6 + overlap_pct * 0.4 + code_bonus


def shortlist_catalog(
    user_text: str,
    sampletypes: list[dict],
    assays: list[dict],
    k_st: int = 50,
    k_a: int = 50,
) -> tuple[list[dict], list[dict]]:
    """
    Return the top-k sampletypes and assays most similar to the user_text.
    Falls back gracefully if fuzzy matching lib is missing.
    """
    q_norm = _norm_text(user_text)
    q_tokens = _tokenize(user_text)
    if not q_norm:
        return (sampletypes[:k_st], assays[:k_a])

    def shortlist(items: list[dict], doc_fn, k: int, code_key: str | None = None) -> list[dict]:
        scored = []
        for item in items:
            doc, doc_tokens = doc_fn(item)
            doc_norm = _norm_text(doc)
            overlap = 0.0
            if q_tokens and doc_tokens:
                overlap = (len(q_tokens & doc_tokens) / max(len(q_tokens), 1)) * 100.0

            code_bonus = 0.0
            code_hit = False
            if code_key:
                code = (item.get(code_key) or "").lower()
                if code:
                    code_plain = code.replace(".", "")
                    q_norm_nospace = q_norm.replace(" ", "")
                    if code in q_norm or code_plain in q_norm_nospace:
                        code_bonus += 35.0
                        code_hit = True
                    if code in q_tokens or code_plain in q_tokens:
                        code_bonus += 15.0
                        code_hit = True

            score = _score_pair(q_norm, doc_norm, overlap, code_bonus)
            scored.append((code_hit, score, item))

        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [item for _, _, item in scored[:k]]

    return (
        shortlist(sampletypes or [], _doc_from_sampletype, k_st, code_key="SampleType"),
        shortlist(assays or [], _doc_from_assay, k_a),
    )
