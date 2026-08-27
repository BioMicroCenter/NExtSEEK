"""Fetch nf-core pipeline schemas and prose docs at a pinned tag.

nf-core's website is generated from files in each pipeline repo. Two carry
the parameter schema: `nextflow_schema.json` (every parameter with type,
default, enum, description and help_text) and `assets/schema_input.json`
(the samplesheet columns) — fetched by `get_schema`. Three more are the
pipeline's prose documentation, rendered as the website's Introduction,
Usage and Output tabs: `README.md`, `docs/usage.md` and `docs/output.md` —
fetched by `get_pipeline_docs`. (The website's "Results" tab has no repo
file — it's an external S3 listing of megatest output — so there is no
fourth document.)

Normalisation drops `fa_icon` — a UI icon name with no bearing on anything —
and nothing else. Measured across the six pinned RNA pipelines, dropping it and
re-serialising compact takes the payload from 219KB to 127KB with no content
removed. The prose docs are markdown, not JSON, so they are fetched verbatim
with no normalisation step.

nf-core tags carry no `v` prefix: `3.18.0` resolves, `v3.18.0` 404s.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable

import requests

RAW_BASE = "https://raw.githubusercontent.com/nf-core"
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 20

#: Injectable clock so failure-TTL expiry is testable without sleeping. Tests
#: monkeypatch this module attribute (not time.monotonic directly) to advance
#: "now" instantly.
_now = time.monotonic

#: How long a cached failure is honored before the next call retries it. Two
#: forces in tension: a network stall on a 6-pipeline x 2-document fetch
#: costs up to 300 seconds, and an *uncached* failure pays that again on the
#: very next call — that's why failures are cached at all. But caching a
#: failure forever turns one transient blip into a permanent one for the
#: life of a long-running gunicorn worker, with no way back short of a
#: restart. A 300s TTL means a genuine outage still only costs the stall
#: once per five minutes, while a blip self-heals on the next request once
#: the window has passed.
_FAILURE_TTL_SECONDS = 300

_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
#: Failures are cached too — see _FAILURE_TTL_SECONDS above. Stored as
#: (exception to re-raise, expiry time from _now()), not swallowed.
_FAILURE_CACHE: dict[tuple[str, str], tuple["SchemaFetchError", float]] = {}

#: Same caching strategy as above, kept in separate dicts so schema and doc
#: fetches don't collide or clear each other.
_DOCS_CACHE: dict[tuple[str, str], dict[str, str]] = {}
_DOCS_FAILURE_CACHE: dict[tuple[str, str], tuple["SchemaFetchError", float]] = {}


class SchemaFetchError(Exception):
    """A pipeline's schema could not be fetched or parsed."""


def _cached_failure(cache: dict[tuple[str, str], tuple["SchemaFetchError", float]], key: tuple[str, str]) -> "SchemaFetchError | None":
    """Return the still-live cached failure for `key`, if any. Expired
    entries are evicted so a later successful fetch can repopulate the slot."""
    entry = cache.get(key)
    if entry is None:
        return None
    error, expires_at = entry
    if _now() >= expires_at:
        del cache[key]
        return None
    return error


def _store_failure(cache: dict[tuple[str, str], tuple["SchemaFetchError", float]], key: tuple[str, str], error: "SchemaFetchError") -> None:
    cache[key] = (error, _now() + _FAILURE_TTL_SECONDS)


def schema_urls(pipeline: str, revision: str) -> tuple[str, str]:
    """Return (nextflow_schema_url, schema_input_url) for a pinned tag."""
    root = f"{RAW_BASE}/{pipeline}/{revision}"
    return f"{root}/nextflow_schema.json", f"{root}/assets/schema_input.json"


def normalise(obj: Any) -> Any:
    """Recursively drop `fa_icon` keys. Nothing else is removed."""
    if isinstance(obj, dict):
        return {k: normalise(v) for k, v in obj.items() if k != "fa_icon"}
    if isinstance(obj, list):
        return [normalise(item) for item in obj]
    return obj


def _default_fetcher(url: str) -> bytes:
    resp = requests.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    resp.raise_for_status()
    return resp.content


def get_schema(
    pipeline: str,
    revision: str,
    *,
    fetcher: Callable[[str], bytes] | None = None,
) -> dict[str, Any]:
    """Fetch and normalise both schema documents for a pinned pipeline version.

    Not every nf-core pipeline ships `assets/schema_input.json` (the
    samplesheet-column schema) — a pipeline without one is still perfectly
    usable, it just has no documented samplesheet columns. So a 404 on that
    document is NOT fatal: the `input_schema` key is simply omitted from the
    returned dict (same absence convention as get_pipeline_docs uses for a
    missing document), and `nextflow_schema` is still returned.
    `nextflow_schema.json` is the core document, though — a 404 on it stays
    fatal, since a pipeline without one is not usable at all. Any non-404
    failure on either document — network error, timeout, a non-404 HTTP
    status, unparseable JSON — raises SchemaFetchError, unchanged.

    Cached by (pipeline, revision); git tags are immutable so a successful
    fetch never needs re-fetching. Raises SchemaFetchError on any fatal
    network or parse failure.

    Failures are cached too, for up to _FAILURE_TTL_SECONDS: once a
    (pipeline, revision) fetch has failed, a later call within the TTL
    re-raises the stored error instead of paying for the network stall
    again. After the TTL, the next call retries. Call clear_cache() to force
    an immediate retry.
    """
    key = (pipeline, revision)
    if key in _CACHE:
        return _CACHE[key]
    cached_failure = _cached_failure(_FAILURE_CACHE, key)
    if cached_failure is not None:
        raise cached_failure

    fetch = fetcher or _default_fetcher
    nf_url, input_url = schema_urls(pipeline, revision)

    documents = {}
    for label, url in (("nextflow_schema", nf_url), ("input_schema", input_url)):
        try:
            raw = fetch(url)
        except Exception as exc:
            if label == "input_schema" and _is_not_found(exc):
                continue
            error = SchemaFetchError(f"{pipeline}@{revision} {label}: {exc}")
            _store_failure(_FAILURE_CACHE, key, error)
            raise error from exc
        try:
            documents[label] = normalise(json.loads(raw))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            error = SchemaFetchError(
                f"{pipeline}@{revision} {label}: body is not valid JSON ({exc})"
            )
            _store_failure(_FAILURE_CACHE, key, error)
            raise error from exc

    _CACHE[key] = documents
    return documents


def doc_urls(pipeline: str, revision: str) -> tuple[str, str, str]:
    """Return (readme_url, usage_url, output_url) for a pinned tag.

    These are the three files nf-core's website renders as its Introduction,
    Usage and Output tabs respectively. The website's "Results" tab has no
    repo file — it's an external S3 listing of megatest output — so there is
    no fourth URL here.
    """
    root = f"{RAW_BASE}/{pipeline}/{revision}"
    return f"{root}/README.md", f"{root}/docs/usage.md", f"{root}/docs/output.md"


def _is_not_found(exc: Exception) -> bool:
    """True if `exc` is an HTTP 404 raised by `_default_fetcher`'s
    `raise_for_status()`. Injected fetchers signal 404 the same way: raise
    `requests.HTTPError(response=<a Response with status_code 404>)`."""
    response = getattr(exc, "response", None)
    return isinstance(exc, requests.HTTPError) and response is not None and response.status_code == 404


def get_pipeline_docs(
    pipeline: str,
    revision: str,
    *,
    fetcher: Callable[[str], bytes] | None = None,
) -> dict[str, str]:
    """Fetch the three prose documents (readme, usage, output) for a pinned
    pipeline version, verbatim as UTF-8 text. These are markdown, not JSON —
    there is no parsing or normalisation to do.

    Not every nf-core pipeline ships all three documents. A 404 on any one of
    them is NOT fatal: that key is simply omitted from the returned dict and
    the other documents are still fetched and returned. Any other failure —
    network error, timeout, a non-404 HTTP status, undecodable bytes — raises
    SchemaFetchError, exactly as get_schema does.

    Cached by (pipeline, revision), same strategy as get_schema including
    negative caching of hard failures with the same _FAILURE_TTL_SECONDS
    expiry. clear_cache() clears this cache too.
    """
    key = (pipeline, revision)
    if key in _DOCS_CACHE:
        return _DOCS_CACHE[key]
    cached_failure = _cached_failure(_DOCS_FAILURE_CACHE, key)
    if cached_failure is not None:
        raise cached_failure

    fetch = fetcher or _default_fetcher
    readme_url, usage_url, output_url = doc_urls(pipeline, revision)

    documents: dict[str, str] = {}
    for label, url in (("readme", readme_url), ("usage", usage_url), ("output", output_url)):
        try:
            raw = fetch(url)
        except Exception as exc:
            if _is_not_found(exc):
                continue
            error = SchemaFetchError(f"{pipeline}@{revision} {label}: {exc}")
            _store_failure(_DOCS_FAILURE_CACHE, key, error)
            raise error from exc
        try:
            documents[label] = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            error = SchemaFetchError(
                f"{pipeline}@{revision} {label}: body is not valid UTF-8 ({exc})"
            )
            _store_failure(_DOCS_FAILURE_CACHE, key, error)
            raise error from exc

    _DOCS_CACHE[key] = documents
    return documents


def clear_cache() -> None:
    """Drop every cached schema and doc, including cached failures. Tests use
    this; production does not need it."""
    _CACHE.clear()
    _FAILURE_CACHE.clear()
    _DOCS_CACHE.clear()
    _DOCS_FAILURE_CACHE.clear()


def warm_cache(pairs=None, *, schema_getter=None) -> dict[str, str]:
    """Prefetch pinned schemas so no user turn pays the cold-cache cost.

    The cache is per gunicorn worker and in memory, so the first selection
    after a restart otherwise pays up to twelve HTTPS round-trips inside a
    user's turn. Called on app ready, on a background thread.

    `pairs` is an iterable of (pipeline, revision); the default is every
    pipeline the selection payload fetches, at the revision the atlas pins.
    Returns {pipeline: error} for whatever failed — never raises, because a
    warm-up failure must not affect booting, and an unfetched schema simply
    degrades to the behaviour that existed before this function.
    """
    if pairs is None:
        from chat_nextseek.pipeline.selection_context import RICH_PIPELINES
        from chat_nextseek.seqera.nfcore_atlas import load_atlas

        try:
            atlas_pipelines = (load_atlas().get("pipelines") or {})
        except Exception as exc:  # noqa: BLE001
            return {"__atlas__": f"{type(exc).__name__}: {exc}"}
        pairs = [(key, atlas_pipelines[key]["revision"])
                 for key in RICH_PIPELINES if key in atlas_pipelines]

    get = schema_getter or get_schema
    failures: dict[str, str] = {}
    for pipeline, revision in pairs:
        try:
            get(pipeline, revision)
        except Exception as exc:  # noqa: BLE001
            failures[pipeline] = str(exc)
    return failures
