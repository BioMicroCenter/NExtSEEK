"""get_schema: fetch nf-core schemas at a pinned tag, normalise, and cache."""
import json
from pathlib import Path

import pytest
import requests

from chat_nextseek.seqera import nfcore_schema
from chat_nextseek.seqera.nfcore_schema import (
    SchemaFetchError,
    clear_cache,
    doc_urls,
    get_pipeline_docs,
    get_schema,
    normalise,
    schema_urls,
)

FIXTURES = Path(__file__).parent / "fixtures" / "nfcore"


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_cache()
    yield
    clear_cache()


def _fixture_fetcher(calls=None):
    """A fetcher that serves the committed fixtures and records its calls."""
    def fetch(url: str) -> bytes:
        if calls is not None:
            calls.append(url)
        name = url.rsplit("/nf-core/", 1)[1]
        pipeline, _revision, *rest = name.split("/")
        suffix = rest[-1]  # "nextflow_schema.json" or "schema_input.json"
        path = FIXTURES / f"{pipeline}.{suffix}"
        if not path.exists():
            raise FileNotFoundError(url)
        return path.read_bytes()
    return fetch


def test_urls_use_the_bare_tag_with_no_v_prefix():
    nf, inp = schema_urls("rnaseq", "3.18.0")
    assert nf == "https://raw.githubusercontent.com/nf-core/rnaseq/3.18.0/nextflow_schema.json"
    assert inp == "https://raw.githubusercontent.com/nf-core/rnaseq/3.18.0/assets/schema_input.json"
    assert "/v3.18.0/" not in nf


def test_normalise_drops_fa_icon_everywhere():
    src = {"a": {"fa_icon": "fas fa-dna", "keep": 1},
           "b": [{"fa_icon": "x", "keep": 2}]}
    assert normalise(src) == {"a": {"keep": 1}, "b": [{"keep": 2}]}


def test_normalise_is_lossless_apart_from_fa_icon():
    """The only thing allowed to disappear is fa_icon. Everything a scientist or
    a model could act on — descriptions, help_text, defaults, enums — survives."""
    raw = json.loads((FIXTURES / "rnaseq.nextflow_schema.json").read_text())
    out = normalise(raw)

    def _collect(obj, wanted, found):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == wanted:
                    found.append(json.dumps(v, sort_keys=True))
                _collect(v, wanted, found)
        elif isinstance(obj, list):
            for v in obj:
                _collect(v, wanted, found)
        return found

    for key in ("description", "help_text", "default", "enum", "type", "pattern"):
        assert _collect(out, key, []) == _collect(raw, key, []), key

    assert _collect(raw, "fa_icon", []), "fixture should contain fa_icon to begin with"
    assert _collect(out, "fa_icon", []) == []


def test_get_schema_returns_both_documents():
    out = get_schema("rnaseq", "3.18.0", fetcher=_fixture_fetcher())
    assert "nextflow_schema" in out and "input_schema" in out
    assert out["nextflow_schema"]["$schema"]
    assert "fa_icon" not in json.dumps(out)


def test_get_schema_caches_by_pipeline_and_revision():
    calls = []
    fetch = _fixture_fetcher(calls)
    get_schema("rnaseq", "3.18.0", fetcher=fetch)
    get_schema("rnaseq", "3.18.0", fetcher=fetch)
    assert len(calls) == 2  # two documents, fetched once, not four


def test_get_schema_raises_on_fetch_failure():
    def boom(url):
        raise TimeoutError("read timeout")
    with pytest.raises(SchemaFetchError, match="read timeout"):
        get_schema("rnaseq", "3.18.0", fetcher=boom)


def test_get_schema_raises_on_unparseable_body():
    with pytest.raises(SchemaFetchError, match="not valid JSON"):
        get_schema("rnaseq", "3.18.0", fetcher=lambda url: b"<html>404</html>")


def _http_404(url: str) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = 404
    return requests.HTTPError(f"404 Client Error for {url}", response=resp)


def test_a_404_on_schema_input_is_not_fatal_and_nextflow_schema_still_comes_back():
    def fetch(url):
        if url.endswith("assets/schema_input.json"):
            raise _http_404(url)
        return (FIXTURES / "rnaseq.nextflow_schema.json").read_bytes()

    out = get_schema("rnaseq", "3.18.0", fetcher=fetch)
    assert "nextflow_schema" in out
    assert "input_schema" not in out


def test_a_404_on_nextflow_schema_is_still_fatal():
    def fetch(url):
        raise _http_404(url)

    with pytest.raises(SchemaFetchError):
        get_schema("rnaseq", "3.18.0", fetcher=fetch)


def test_a_non_404_error_on_schema_input_still_raises():
    def fetch(url):
        if url.endswith("assets/schema_input.json"):
            resp = requests.Response()
            resp.status_code = 500
            raise requests.HTTPError("500 Server Error", response=resp)
        return (FIXTURES / "rnaseq.nextflow_schema.json").read_bytes()

    with pytest.raises(SchemaFetchError):
        get_schema("rnaseq", "3.18.0", fetcher=fetch)


def test_a_failed_fetch_is_cached_and_not_retried():
    calls = []

    def boom(url):
        calls.append(url)
        raise TimeoutError("read timeout")

    with pytest.raises(SchemaFetchError, match="read timeout"):
        get_schema("rnaseq", "3.18.0", fetcher=boom)
    assert len(calls) == 1

    # Second call: must re-raise the cached failure without touching the
    # fetcher again — a stall must not cost the full timeout twice.
    with pytest.raises(SchemaFetchError, match="read timeout"):
        get_schema("rnaseq", "3.18.0", fetcher=boom)
    assert len(calls) == 1


def test_clear_cache_restores_fetching_after_a_failure():
    calls = []

    def boom(url):
        calls.append(url)
        raise TimeoutError("read timeout")

    with pytest.raises(SchemaFetchError):
        get_schema("rnaseq", "3.18.0", fetcher=boom)
    assert len(calls) == 1

    clear_cache()

    out = get_schema("rnaseq", "3.18.0", fetcher=_fixture_fetcher())
    assert "nextflow_schema" in out


def test_a_cached_failure_is_still_reraised_just_under_the_ttl(monkeypatch):
    fake_now = [1_000.0]
    monkeypatch.setattr(nfcore_schema, "_now", lambda: fake_now[0])
    calls = []

    def boom(url):
        calls.append(url)
        raise TimeoutError("read timeout")

    with pytest.raises(SchemaFetchError, match="read timeout"):
        get_schema("rnaseq", "3.18.0", fetcher=boom)
    assert len(calls) == 1

    fake_now[0] += nfcore_schema._FAILURE_TTL_SECONDS - 1
    with pytest.raises(SchemaFetchError, match="read timeout"):
        get_schema("rnaseq", "3.18.0", fetcher=boom)
    assert len(calls) == 1  # still cached, not refetched


def test_a_cached_failure_expires_after_the_ttl_and_self_heals(monkeypatch):
    fake_now = [1_000.0]
    monkeypatch.setattr(nfcore_schema, "_now", lambda: fake_now[0])
    calls = []

    def boom(url):
        calls.append(url)
        raise TimeoutError("read timeout")

    with pytest.raises(SchemaFetchError, match="read timeout"):
        get_schema("rnaseq", "3.18.0", fetcher=boom)
    assert len(calls) == 1

    fake_now[0] += nfcore_schema._FAILURE_TTL_SECONDS + 1
    out = get_schema("rnaseq", "3.18.0", fetcher=_fixture_fetcher())
    assert "nextflow_schema" in out


def test_a_cached_docs_failure_expires_after_the_ttl_and_self_heals(monkeypatch):
    fake_now = [1_000.0]
    monkeypatch.setattr(nfcore_schema, "_now", lambda: fake_now[0])
    calls = []

    def boom(url):
        calls.append(url)
        raise TimeoutError("read timeout")

    with pytest.raises(SchemaFetchError, match="read timeout"):
        get_pipeline_docs("rnaseq", "3.18.0", fetcher=boom)
    assert len(calls) == 1

    fake_now[0] += nfcore_schema._FAILURE_TTL_SECONDS - 1
    with pytest.raises(SchemaFetchError, match="read timeout"):
        get_pipeline_docs("rnaseq", "3.18.0", fetcher=boom)
    assert len(calls) == 1  # still cached, not refetched

    fake_now[0] += 2
    out = get_pipeline_docs("rnaseq", "3.18.0", fetcher=_docs_fixture_fetcher())
    assert set(out) == {"readme", "usage", "output"}


def test_all_six_pinned_pipelines_parse():
    for pipeline, revision in [
        ("rnaseq", "3.18.0"), ("scrnaseq", "2.7.1"), ("smrnaseq", "2.4.1"),
        ("hlatyping", "2.2.0"), ("rnafusion", "4.1.3"), ("rnasplice", "1.0.4"),
    ]:
        out = get_schema(pipeline, revision, fetcher=_fixture_fetcher())
        assert out["nextflow_schema"], pipeline
        assert out["input_schema"], pipeline


# --- get_pipeline_docs ------------------------------------------------------

_DOC_LABELS = {"README.md": "readme", "usage.md": "usage", "output.md": "output"}


def _docs_fixture_fetcher(calls=None, missing=()):
    """A fetcher that serves the committed doc fixtures and records its calls.
    `missing` is a set of labels ('readme', 'usage', 'output') to simulate as
    an HTTP 404 instead of serving the fixture."""
    def fetch(url: str) -> bytes:
        if calls is not None:
            calls.append(url)
        name = url.rsplit("/nf-core/", 1)[1]
        pipeline, _revision, *rest = name.split("/")
        suffix = rest[-1]  # "README.md", "usage.md", or "output.md"
        label = _DOC_LABELS[suffix]
        if label in missing:
            resp = requests.Response()
            resp.status_code = 404
            raise requests.HTTPError(f"404 Client Error for {url}", response=resp)
        path = FIXTURES / f"{pipeline}.{label}.md"
        if not path.exists():
            raise FileNotFoundError(url)
        return path.read_bytes()
    return fetch


def test_doc_urls_point_at_readme_and_docs_files_with_no_v_prefix():
    readme, usage, output = doc_urls("rnaseq", "3.18.0")
    assert readme == "https://raw.githubusercontent.com/nf-core/rnaseq/3.18.0/README.md"
    assert usage == "https://raw.githubusercontent.com/nf-core/rnaseq/3.18.0/docs/usage.md"
    assert output == "https://raw.githubusercontent.com/nf-core/rnaseq/3.18.0/docs/output.md"
    assert "/v3.18.0/" not in readme


def test_get_pipeline_docs_returns_all_three_documents_with_real_content():
    out = get_pipeline_docs("rnaseq", "3.18.0", fetcher=_docs_fixture_fetcher())
    assert set(out) == {"readme", "usage", "output"}
    assert "nf-core/rnaseq" in out["readme"]
    assert len(out["usage"]) > 1000
    assert len(out["output"]) > 1000


def test_a_404_on_one_document_is_not_fatal_and_the_others_still_come_back():
    out = get_pipeline_docs("rnaseq", "3.18.0", fetcher=_docs_fixture_fetcher(missing={"usage"}))
    assert set(out) == {"readme", "output"}
    assert "usage" not in out


def test_all_three_documents_missing_yields_an_empty_but_valid_result():
    out = get_pipeline_docs(
        "rnaseq", "3.18.0",
        fetcher=_docs_fixture_fetcher(missing={"readme", "usage", "output"}),
    )
    assert out == {}


def test_a_network_error_fetching_docs_still_raises_schema_fetch_error():
    def boom(url):
        raise TimeoutError("read timeout")
    with pytest.raises(SchemaFetchError, match="read timeout"):
        get_pipeline_docs("rnaseq", "3.18.0", fetcher=boom)


def test_a_non_404_http_error_fetching_docs_still_raises_schema_fetch_error():
    def boom(url):
        resp = requests.Response()
        resp.status_code = 500
        raise requests.HTTPError("500 Server Error", response=resp)
    with pytest.raises(SchemaFetchError):
        get_pipeline_docs("rnaseq", "3.18.0", fetcher=boom)


def test_pipeline_docs_are_cached_by_pipeline_and_revision():
    calls = []
    fetch = _docs_fixture_fetcher(calls)
    get_pipeline_docs("rnaseq", "3.18.0", fetcher=fetch)
    get_pipeline_docs("rnaseq", "3.18.0", fetcher=fetch)
    assert len(calls) == 3  # three documents, fetched once, not six


def test_a_failed_docs_fetch_is_cached_and_not_retried():
    calls = []

    def boom(url):
        calls.append(url)
        raise TimeoutError("read timeout")

    with pytest.raises(SchemaFetchError, match="read timeout"):
        get_pipeline_docs("rnaseq", "3.18.0", fetcher=boom)
    assert len(calls) == 1

    with pytest.raises(SchemaFetchError, match="read timeout"):
        get_pipeline_docs("rnaseq", "3.18.0", fetcher=boom)
    assert len(calls) == 1


def test_clear_cache_clears_pipeline_docs_too():
    calls = []
    fetch = _docs_fixture_fetcher(calls)
    get_pipeline_docs("rnaseq", "3.18.0", fetcher=fetch)
    assert len(calls) == 3

    clear_cache()

    get_pipeline_docs("rnaseq", "3.18.0", fetcher=fetch)
    assert len(calls) == 6


def test_clear_cache_restores_docs_fetching_after_a_failure():
    calls = []

    def boom(url):
        calls.append(url)
        raise TimeoutError("read timeout")

    with pytest.raises(SchemaFetchError):
        get_pipeline_docs("rnaseq", "3.18.0", fetcher=boom)
    assert len(calls) == 1

    clear_cache()

    out = get_pipeline_docs("rnaseq", "3.18.0", fetcher=_docs_fixture_fetcher())
    assert set(out) == {"readme", "usage", "output"}


def test_all_six_pinned_pipelines_docs_parse():
    for pipeline, revision in [
        ("rnaseq", "3.18.0"), ("scrnaseq", "2.7.1"), ("smrnaseq", "2.4.1"),
        ("hlatyping", "2.2.0"), ("rnafusion", "4.1.3"), ("rnasplice", "1.0.4"),
    ]:
        out = get_pipeline_docs(pipeline, revision, fetcher=_docs_fixture_fetcher())
        assert set(out) == {"readme", "usage", "output"}, pipeline


def test_warm_cache_fetches_every_rich_pipeline():
    from chat_nextseek.pipeline.selection_context import RICH_PIPELINES
    from chat_nextseek.seqera import nfcore_schema

    fetched = []
    failures = nfcore_schema.warm_cache(
        schema_getter=lambda p, r: fetched.append((p, r)) or {"nextflow_schema": {}})
    assert failures == {}
    assert sorted(p for p, _ in fetched) == sorted(RICH_PIPELINES)


def test_warm_cache_reports_failures_without_raising():
    from chat_nextseek.seqera import nfcore_schema
    from chat_nextseek.seqera.nfcore_schema import SchemaFetchError

    def flaky(pipeline, revision):
        if pipeline == "rnaseq":
            raise SchemaFetchError("read timeout")
        return {"nextflow_schema": {}}

    failures = nfcore_schema.warm_cache(schema_getter=flaky)
    assert list(failures) == ["rnaseq"]
    assert "read timeout" in failures["rnaseq"]


def test_warm_cache_survives_an_unexpected_exception():
    from chat_nextseek.seqera import nfcore_schema

    def explode(pipeline, revision):
        raise OSError("no route to host")

    failures = nfcore_schema.warm_cache(schema_getter=explode)
    assert len(failures) >= 1


def test_warm_cache_accepts_explicit_pairs():
    from chat_nextseek.seqera import nfcore_schema

    fetched = []
    nfcore_schema.warm_cache(pairs=[("rnaseq", "3.18.0")],
                             schema_getter=lambda p, r: fetched.append((p, r)) or {})
    assert fetched == [("rnaseq", "3.18.0")]
