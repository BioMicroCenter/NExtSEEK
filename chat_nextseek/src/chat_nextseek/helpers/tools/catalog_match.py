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
    k_a: int = 75,
    *,
    sampletype_index=None,  # SemanticIndex | None
    assay_index=None,       # SemanticIndex | None
    ratio: float = 0.7,
    min_k: int = 10,
    max_k: int = 80,
) -> tuple[list[dict], list[dict], dict]:
    """Return shortlisted (sampletypes, assays, diagnostics) for an entity prompt.

    Three paths:
      1. No semantic indexes provided / unready → legacy rapidfuzz-only top-K
         (k_st=50, k_a=75 by default). `diagnostics.enabled = False`.
      2. Semantic indexes ready → hybrid: lexical (rapidfuzz) + semantic
         (cosine) + code-hit, fused with RRF, dynamic-cutoff by
         (ratio, min_k, max_k). Code-hit items always included.
      3. Any unexpected failure inside the hybrid path → fall through to
         legacy lexical path; `diagnostics.fallback_reason` records why.
    """
    diagnostics: dict = {
        "sampletype_codes": [],
        "sampletype_ranks": {},
        "assay_codes": [],
        "assay_ranks": {},
        "enabled": False,
        "fallback_reason": None,
    }

    # Path 1/3: no/unready indexes → legacy
    st_ready = sampletype_index is not None and getattr(sampletype_index, "ready", False)
    a_ready  = assay_index is not None and getattr(assay_index, "ready", False)
    if not (st_ready or a_ready):
        if sampletype_index is not None and not st_ready:
            diagnostics["fallback_reason"] = getattr(sampletype_index, "fail_reason", "sampletype index not ready")
        elif assay_index is not None and not a_ready:
            diagnostics["fallback_reason"] = getattr(assay_index, "fail_reason", "assay index not ready")
        st_short, a_short = _legacy_shortlist(user_text, sampletypes, assays, k_st, k_a)
        diagnostics["sampletype_codes"] = [x.get("SampleType") for x in st_short if x.get("SampleType")]
        diagnostics["assay_codes"] = [x.get("Name") for x in a_short if x.get("Name")]
        return st_short, a_short, diagnostics

    try:
        if st_ready:
            st_short, st_ranks = _hybrid_shortlist(
                user_text, sampletypes, sampletype_index,
                id_field="SampleType", lex_doc_fn=_doc_from_sampletype, code_key="SampleType",
                ratio=ratio, min_k=min_k, max_k=max_k,
            )
        else:
            st_short = _legacy_shortlist_one(user_text, sampletypes, k_st, _doc_from_sampletype, code_key="SampleType")
            st_ranks = {}
        if a_ready:
            a_short, a_ranks = _hybrid_shortlist(
                user_text, assays, assay_index,
                id_field="Name", lex_doc_fn=_doc_from_assay, code_key=None,
                ratio=ratio, min_k=min_k, max_k=max_k,
            )
        else:
            a_short = _legacy_shortlist_one(user_text, assays, k_a, _doc_from_assay, code_key=None)
            a_ranks = {}
        diagnostics["sampletype_codes"] = [x.get("SampleType") for x in st_short if x.get("SampleType")]
        diagnostics["sampletype_ranks"] = st_ranks
        diagnostics["assay_codes"] = [x.get("Name") for x in a_short if x.get("Name")]
        diagnostics["assay_ranks"] = a_ranks
        diagnostics["enabled"] = True
        return st_short, a_short, diagnostics
    except Exception as exc:  # noqa: BLE001
        diagnostics["fallback_reason"] = f"hybrid path failure: {type(exc).__name__}: {exc}"
        st_short, a_short = _legacy_shortlist(user_text, sampletypes, assays, k_st, k_a)
        diagnostics["sampletype_codes"] = [x.get("SampleType") for x in st_short if x.get("SampleType")]
        diagnostics["assay_codes"] = [x.get("Name") for x in a_short if x.get("Name")]
        return st_short, a_short, diagnostics


def _legacy_shortlist(
    user_text: str, sampletypes: list[dict], assays: list[dict], k_st: int, k_a: int,
) -> tuple[list[dict], list[dict]]:
    """Original two-catalog rapidfuzz-only shortlister, preserved for fallback."""
    q_norm = _norm_text(user_text)
    if not q_norm:
        return (sampletypes[:k_st], assays[:k_a])
    return (
        _legacy_shortlist_one(user_text, sampletypes or [], k_st, _doc_from_sampletype, code_key="SampleType"),
        _legacy_shortlist_one(user_text, assays or [], k_a, _doc_from_assay, code_key=None),
    )


def _legacy_shortlist_one(
    user_text: str, items: list[dict], k: int, doc_fn, code_key: str | None,
) -> list[dict]:
    q_norm = _norm_text(user_text)
    q_tokens = _tokenize(user_text)
    if not q_norm:
        return items[:k]
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
                    code_bonus += 35.0; code_hit = True
                if code in q_tokens or code_plain in q_tokens:
                    code_bonus += 15.0; code_hit = True
        score = _score_pair(q_norm, doc_norm, overlap, code_bonus)
        scored.append((code_hit, score, item))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [item for _, _, item in scored[:k]]


def _hybrid_shortlist(
    user_text: str,
    catalog: list[dict],
    sem_index,            # SemanticIndex
    *,
    id_field: str,
    lex_doc_fn,
    code_key: str | None,
    ratio: float, min_k: int, max_k: int,
) -> tuple[list[dict], dict]:
    """Run lexical + semantic + code-hit, fuse with RRF, dynamic-cutoff.

    Returns (shortlisted_items, ranks_dict) where ranks_dict is keyed by
    the item's id_field value.
    """
    from chat_nextseek.helpers.tools.catalog_semantic import fuse_rrf, dynamic_cutoff  # noqa: PLC0415

    q_norm = _norm_text(user_text)
    q_tokens = _tokenize(user_text)

    # The semantic index may be built from a richer catalog than `catalog`
    # (e.g. FULL assays for embedding, MIN passed in here for LLM consumption).
    # Map id_field → MIN entry so every returned item comes from `catalog`,
    # never from sem_index._items_by_id.
    catalog_by_id: dict[str, dict] = {item.get(id_field, ""): item for item in catalog}

    def _to_min(item: dict) -> dict:
        return catalog_by_id.get(item.get(id_field, ""), item)

    # Lexical ranking — full catalog, sorted desc by score, take top max_k
    lex_scored: list[tuple[dict, float]] = []
    for item in catalog:
        doc, doc_tokens = lex_doc_fn(item)
        doc_norm = _norm_text(doc)
        overlap = 0.0
        if q_tokens and doc_tokens:
            overlap = (len(q_tokens & doc_tokens) / max(len(q_tokens), 1)) * 100.0
        code_bonus = 0.0
        if code_key:
            code = (item.get(code_key) or "").lower()
            if code:
                code_plain = code.replace(".", "")
                q_norm_nospace = q_norm.replace(" ", "")
                if code in q_norm or code_plain in q_norm_nospace:
                    code_bonus += 35.0
                if code in q_tokens or code_plain in q_tokens:
                    code_bonus += 15.0
        score = _score_pair(q_norm, doc_norm, overlap, code_bonus)
        lex_scored.append((item, score))
    lex_scored.sort(key=lambda x: -x[1])
    lex_ranked: list[tuple[dict, float, int]] = [
        (item, score, rank + 1) for rank, (item, score) in enumerate(lex_scored[:max_k])
    ]

    # Semantic ranking
    sem_ranked: list[tuple[dict, float, int]] = sem_index.query(user_text, top_n=max_k)

    # Code-hit set (unconditional inclusion)
    code_hits: list[dict] = []
    code_hit_ids: set[str] = set()
    if code_key:
        for item in catalog:
            code = (item.get(code_key) or "").lower()
            if not code:
                continue
            code_plain = code.replace(".", "")
            q_norm_nospace = q_norm.replace(" ", "")
            if code in q_norm or code_plain in q_norm_nospace or code in q_tokens or code_plain in q_tokens:
                code_hits.append(item)
                code_hit_ids.add(item.get(id_field, ""))

    # RRF fuse lex + sem
    fused = fuse_rrf(lex_ranked, sem_ranked, id_fn=lambda x: x.get(id_field, ""), k=60)
    fused_kept = dynamic_cutoff(fused, ratio=ratio, min_k=min_k, max_k=max_k)

    # Union: code-hits first (preserve catalog order), then fused minus duplicates.
    # Remap every item to its MIN counterpart so the caller never sees objects
    # that originated from the semantic index's (possibly richer) catalog.
    seen: set[str] = set()
    result: list[dict] = []
    for item in code_hits:
        item = _to_min(item)
        key = item.get(id_field, "")
        if key in seen:
            continue
        result.append(item); seen.add(key)
    for item in fused_kept:
        item = _to_min(item)
        key = item.get(id_field, "")
        if key in seen:
            continue
        result.append(item); seen.add(key)

    # Build ranks dict for diagnostics
    lex_ranks = {item.get(id_field, ""): rank for item, _, rank in lex_ranked}
    sem_ranks = {item.get(id_field, ""): rank for item, _, rank in sem_ranked}
    fused_ranks = {item.get(id_field, ""): rank + 1 for rank, (item, _) in enumerate(fused)}
    ranks: dict = {}
    for item in result:
        key = item.get(id_field, "")
        ranks[key] = {
            "lex":     lex_ranks.get(key),
            "sem":     sem_ranks.get(key),
            "fused":   fused_ranks.get(key),
            "code_hit": key in code_hit_ids,
        }
    return result, ranks
