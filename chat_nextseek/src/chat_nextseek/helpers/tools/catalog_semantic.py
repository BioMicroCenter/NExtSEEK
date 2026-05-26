"""Semantic catalog retrieval: SemanticIndex + RRF fusion + dynamic cutoff.

Used by `shortlist_catalog` to supplement the existing lexical rapidfuzz pass
with semantic embedding-based retrieval and graceful fallback.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

_LOG = logging.getLogger(__name__)

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _catalog_hash(catalog: list[dict]) -> str:
    canonical = json.dumps(catalog, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def dynamic_cutoff(
    scored: list[tuple[Any, float]],
    *,
    ratio: float = 0.7,
    min_k: int = 10,
    max_k: int = 80,
) -> list[Any]:
    """Cut a sorted-desc scored list to a dynamic top-K.

    Keeps every item with `score >= ratio * top_score`, then enforces
    `min_k` (always at least N items if available) and `max_k` (never
    more than N). Inputs MUST be pre-sorted by descending score.

    With `top_score == 0` the ratio rule is meaningless; fall through
    to `min_k`.
    """
    if not scored:
        return []
    top_score = scored[0][1]
    if top_score > 0:
        threshold = ratio * top_score
        kept = [item for item, score in scored if score >= threshold]
    else:
        kept = []
    if len(kept) < min_k:
        kept = [item for item, _ in scored[:min_k]]
    if len(kept) > max_k:
        kept = kept[:max_k]
    return kept


def fuse_rrf(
    *ranked_lists: list[tuple[Any, float, int]],
    id_fn: Callable[[Any], str],
    k: int = 60,
) -> list[tuple[Any, float]]:
    """Reciprocal Rank Fusion across any number of ranked lists.

    Each input is a list of (item, score, rank_1_indexed). Items are
    keyed by `id_fn(item)` so the same logical item across rankers
    accumulates fused score. Output is sorted descending by fused
    score; only the FIRST observed item-object for each id is kept
    in the output.

    `k=60` is the canonical RRF smoothing constant (Cormack et al. 2009):
    high enough that rank-1 in one ranker does not dominate rank-2
    in both rankers.
    """
    scores: dict[str, float] = {}
    items: dict[str, Any] = {}
    for ranking in ranked_lists:
        for item, _score, rank in ranking:
            key = id_fn(item)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            items.setdefault(key, item)
    ordered_keys = sorted(scores.keys(), key=lambda key: -scores[key])
    return [(items[key], scores[key]) for key in ordered_keys]


def _join_nonempty(parts: list[str]) -> str:
    return " | ".join(p for p in parts if p)


def _stringify(value) -> str:
    """Coerce list/scalar catalog fields to a single embeddable string."""
    if not value:
        return ""
    if isinstance(value, list):
        return ", ".join(str(x) for x in value if x)
    return str(value)


def doc_sampletype(st: dict) -> str:
    """Embeddable text for a sampletype catalog item.

    Reads fields from the FULL sampletypes catalog (sampletypes_db.json).
    Adds Clade + Parent/Child sampletypes + Associated Assay Parents/Children
    for stronger semantic ranking (the assay lists help generic queries like
    "PBMCs sequenced via single cell" surface TIS by associating tissues with
    sequencing assays). Excludes:
      - Required/Standard Metadata: field NAMES not values; ~60 tokens of noise.
      - Possible Metadata Fields: exceeds 600 tokens for some items, past the
        512-token model cap.
    """
    return _join_nonempty([
        st.get("SampleType") or "",
        st.get("Name") or "",
        st.get("Clade") or "",
        st.get("Tags") or "",
        _stringify(st.get("Parent_SampleTypes")),
        _stringify(st.get("Child_SampleTypes")),
        _stringify(st.get("Associated Assay Parents")),
        _stringify(st.get("Associated Assay Children")),
        st.get("Description") or "",
    ])


def doc_assay(assay: dict) -> str:
    """Embeddable text for an assay catalog item.

    Reads fields from the FULL assays catalog (assays_db.json). Honors both
    PascalCase and snake_case alternative-names keys (catalog has historically
    used both). Excludes Protocols_UIDs (raw IDs, zero semantic signal) and
    Protocols_Phrases (low-signal meta-instructions in observed examples).
    """
    alt = assay.get("Alternative Assay Names") or assay.get("alternative_assay_names") or ""
    return _join_nonempty([
        assay.get("Name") or "",
        _stringify(alt),
        assay.get("Tags") or "",
        _stringify(assay.get("Critical Attributes")),
        assay.get("AssociatedRepository") or "",
        assay.get("Parent Clade Type") or "",
        assay.get("Child Clade Type") or "",
        _stringify(assay.get("Required Parent Sample Types")),
        _stringify(assay.get("Optional Parent Sample Types")),
        _stringify(assay.get("Children Sample Types")),
        assay.get("Description") or "",
    ])


def doc_endpoint(ep: dict) -> str:
    """Embeddable text for an API endpoint catalog item.

    Concatenates path + category + description + intent_patterns +
    example_intents + llm_hint. The list-valued intent_patterns and
    example_intents (joined by space) are the highest-signal fields
    for matching free-text user queries to endpoints.
    """
    def _list_or_str(value) -> str:
        if isinstance(value, list):
            return " ".join(str(v) for v in value)
        return str(value) if value else ""

    return _join_nonempty([
        ep.get("path") or "",
        ep.get("category") or "",
        ep.get("description") or "",
        _list_or_str(ep.get("intent_patterns")),
        _list_or_str(ep.get("example_intents")),
        ep.get("llm_hint") or "",
    ])


class SemanticIndex:
    """Cached cosine-ranked retriever over a catalog of dicts.

    Catalog items are embedded once at construction via `encoder`
    (or a lazy-loaded sentence-transformers model if `encoder is None`).
    The embedding matrix is persisted to `<cache_dir>/<name>.embeddings.npz`
    keyed by sha256 of the catalog JSON; mismatches trigger a rebuild.
    """

    def __init__(
        self,
        catalog: list[dict],
        name: str,
        doc_fn: Callable[[dict], str],
        id_fn: Callable[[dict], str],
        cache_dir: Path,
        model_name: str = DEFAULT_MODEL,
        encoder: Optional[Callable[[list[str]], np.ndarray]] = None,
    ):
        self._catalog = list(catalog)
        self._name = name
        self._doc_fn = doc_fn
        self._id_fn = id_fn
        self._model_name = model_name
        self._cache_path = Path(cache_dir) / f"{name}.embeddings.npz"
        self._embeddings: np.ndarray | None = None
        self._item_ids: list[str] = []
        self._items_by_id: dict[str, dict] = {}
        self._ready: bool = False
        self._encoder = encoder
        self._fail_reason: str | None = None
        try:
            self._initialize()
        except Exception as exc:  # noqa: BLE001
            self._fail_reason = f"{type(exc).__name__}: {exc}"
            _LOG.warning("SemanticIndex[%s] initialization failed: %s", name, self._fail_reason)
            self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def fail_reason(self) -> str | None:
        return self._fail_reason

    def query(self, text: str, top_n: int = 80) -> list[tuple[dict, float, int]]:
        """Return top-N items by cosine similarity to `text`.

        Returns a list of (item, score, rank_1_indexed). If the index
        is not ready (load failure) returns an empty list; callers
        should fall back to the lexical-only path.
        """
        if not self._ready or self._embeddings is None or self._embeddings.size == 0:
            return []
        if self._encoder is None:
            self._encoder = self._load_default_encoder()
        encoder = self._encoder
        try:
            q = encoder([text])
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("SemanticIndex[%s] query encoding failed: %s", self._name, exc)
            return []
        q = np.asarray(q, dtype=np.float32)
        q = self._l2_normalize(q)
        sims = (self._embeddings @ q[0])  # cosine because both sides are L2-normalized
        top_n_eff = min(top_n, sims.shape[0])
        top_idx = np.argpartition(-sims, top_n_eff - 1)[:top_n_eff]
        top_idx = top_idx[np.argsort(-sims[top_idx])]
        out: list[tuple[dict, float, int]] = []
        for rank0, i in enumerate(top_idx):
            item_id = self._item_ids[int(i)]
            item = self._items_by_id.get(item_id)
            if item is None:
                continue
            out.append((item, float(sims[int(i)]), rank0 + 1))
        return out

    def _initialize(self) -> None:
        target_hash = _catalog_hash(self._catalog)
        if self._cache_path.exists():
            try:
                data = np.load(self._cache_path, allow_pickle=False)
                cached_hash = str(data["catalog_hash"])
                cached_model = str(data["model_name"])
                if cached_hash == target_hash and cached_model == self._model_name:
                    self._embeddings = data["embeddings"]
                    self._item_ids = list(data["item_ids"].tolist())
                    self._finalize_id_lookup()
                    self._ready = True
                    return
            except Exception as exc:  # noqa: BLE001
                _LOG.warning(
                    "SemanticIndex[%s] cache read failed (%s); rebuilding.",
                    self._name, exc,
                )

        docs = [self._doc_fn(item) for item in self._catalog]
        ids = [self._id_fn(item) for item in self._catalog]
        if not docs:
            self._embeddings = np.zeros((0, 0), dtype=np.float32)
            self._item_ids = []
            self._finalize_id_lookup()
            self._ready = True
            return

        encoder = self._encoder if self._encoder is not None else self._load_default_encoder()
        embeddings = encoder(docs)
        embeddings = np.asarray(embeddings, dtype=np.float32)
        embeddings = self._l2_normalize(embeddings)
        self._embeddings = embeddings
        self._item_ids = ids
        self._finalize_id_lookup()

        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        # NOTE: item_ids saved as fixed-width unicode (NOT dtype=object) so
        # the cache can be loaded with allow_pickle=False — numpy serializes
        # object arrays via pickle, which we refuse to load for security.
        np.savez(
            self._cache_path,
            embeddings=embeddings,
            item_ids=np.array(ids, dtype=str),
            catalog_hash=np.array(target_hash),
            model_name=np.array(self._model_name),
        )
        self._ready = True

    def _finalize_id_lookup(self) -> None:
        self._items_by_id = {
            self._id_fn(item): item for item in self._catalog
        }

    def _load_default_encoder(self) -> Callable[[list[str]], np.ndarray]:
        """Lazy-load the real sentence-transformers model."""
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        model = SentenceTransformer(self._model_name)
        return lambda texts: model.encode(texts, convert_to_numpy=True, show_progress_bar=False)

    @staticmethod
    def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
        if matrix.size == 0:
            return matrix
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return matrix / norms
