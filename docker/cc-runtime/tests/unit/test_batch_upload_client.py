from __future__ import annotations

import ast
import base64
import json
import pathlib
import sys
from collections.abc import Callable

import httpx
import pytest

sys.path.insert(0, str(pathlib.Path("build_context/plugins/nextseek/bin")))
import _batch_upload_client as buc

FIXTURES = pathlib.Path("tests/unit/fixtures/ns")


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> buc.BatchUploadClient:
    return buc.BatchUploadClient(
        base_url="http://ns.test",
        auth=("u", "p"),
        transport=httpx.MockTransport(handler),
    )


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


def test_sample_type_attributes_reads_retrieve_endpoint():
    def handler(request):
        assert request.method == "GET"
        assert str(request.url).endswith("/nextseek_api/sample_types/MUS/")
        return httpx.Response(200, json={"data": {"attributes": {
            "sample_attributes": [
                {"title": "Name", "required": True, "sample_attribute_type": {"base_type": "String"}},
                {"title": "Sex", "required": False, "sample_attribute_type": {"base_type": "String"}},
            ]
        }}})

    attrs = _client(handler).sample_type_attributes("MUS")
    assert [item["title"] for item in attrs["attributes"]] == ["Name", "Sex"]
    assert attrs["attributes"][0]["sample_attribute_type"]["base_type"] == "String"


def test_list_sample_types_reads_index_endpoint():
    def handler(request):
        assert request.method == "GET"
        assert str(request.url).endswith("/nextseek_api/sample_types/")
        return httpx.Response(200, json={"data": [{"id": "12", "attributes": {"title": "MUS"}}]})

    assert _client(handler).list_sample_types()[0]["attributes"]["title"] == "MUS"


def test_validate_file_posts_multipart_checks_form_field(tmp_path):
    workbook = tmp_path / "payload.xlsx"
    workbook.write_bytes(b"xlsx")
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        body = request.content
        seen["body"] = body
        return httpx.Response(200, json={"valid": True, "checks_run": ["structure", "name_check", "dag"]})

    result = _client(handler).validate_file(workbook, project_id=1, checks="structure,name_check,dag")
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/nextseek_api/batch-upload/validate/")
    assert b'name="checks"' in seen["body"]
    assert b"structure,name_check,dag" in seen["body"]
    assert b'name="file"' in seen["body"]
    assert result["valid"] is True


def test_search_samples_by_uid_filters_parent_false_positive_and_parses_titles():
    fixture = _fixture("advanced_search_uid.json")
    seen = {}

    def handler(request):
        seen["payload"] = json.loads(request.content)
        assert request.method == "POST"
        assert str(request.url).split("?")[0].endswith("/nextseek_api/samples/advanced_search/")
        return httpx.Response(200, json=fixture)

    rows = _client(handler).search_samples_by_uid(["D.IMG-230913ENG-1757-PUB"])
    assert seen["payload"] == {
        "filter_searchText": ["D.IMG-230913ENG-1757-PUB"],
        "searchText_logic": "OR",
        "filter_matchType": "EXACT",
    }
    assert "attribute" not in seen["payload"]
    assert [row["json_metadata"]["UID"] for row in rows] == ["D.IMG-230913ENG-1757-PUB"]
    assert rows[0]["numeric_seek_id"] == 324503
    assert rows[0]["assay_titles"] == [
        "Comet Chip - Data Linked",
        "Comet Chip Analysis - Data Attached",
    ]


def test_assay_samples_pages_to_completion():
    calls: list[str] = []

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={"data": {"relationships": {"samples": {
                    "data": [{"id": "1", "type": "samples"}],
                    "links": {"next": "/nextseek_api/assays/351/?page=2"},
                }}}},
            )
        return httpx.Response(
            200,
            json={"data": {"relationships": {"samples": {"data": [{"id": "324503", "type": "samples"}]}}}},
        )

    # The live endpoint currently returns the relationship in one body; this test still
    # proves every returned relationship row is consumed and type-normalized.
    samples = _client(handler).assay_samples({351})
    assert samples[351] == {"1", "324503"}
    assert len(calls) == 2


def test_assay_samples_rejects_non_list_relationship_and_stops_on_empty_next():
    def bad_handler(request):
        return httpx.Response(
            200,
            json={"data": {"relationships": {"samples": {
                "data": {"id": "not-a-list"},
            }}}},
        )

    with pytest.raises(ValueError, match="relationship list"):
        _client(bad_handler).assay_samples({351})

    calls = 0

    def empty_next_handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"data": {"relationships": {"samples": {
                "data": [],
                "links": {"next": "/nextseek_api/assays/351/?page=2"},
            }}}},
        )

    assert _client(empty_next_handler).assay_samples({351}) == {351: set()}
    assert calls == 1


def test_search_samples_by_uid_chunks_input(monkeypatch):
    monkeypatch.setattr(buc, "_UID_CHUNK_SIZE", 2)
    calls: list[list[str]] = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload["filter_searchText"])
        rows = [
            {"id": idx, "json_metadata": {"UID": uid}, "assays": ""}
            for idx, uid in enumerate(payload["filter_searchText"], start=1)
        ]
        return httpx.Response(200, json={"total": len(rows), "rows": rows})

    rows = _client(handler).search_samples_by_uid(["u1", "u2", "u3"])
    assert calls == [["u1", "u2"], ["u3"]]
    assert [row["json_metadata"]["UID"] for row in rows] == ["u1", "u2", "u3"]


def test_search_samples_by_uid_pages_and_handles_string_metadata(monkeypatch):
    monkeypatch.setattr(buc, "_PAGE_SIZE", 1)
    pages: list[int] = []

    def handler(request):
        pages.append(int(request.url.params["page"]))
        if request.url.params["page"] == "1":
            return httpx.Response(
                200,
                json={
                    "total": 2,
                    "rows": [
                        {
                            "id": 7,
                            "json_metadata": '{"UID":"u1"}',
                            "assays": "Assay A, Assay B",
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "total": 2,
                "rows": [
                    {
                        "id": 8,
                        "json_metadata": {"UID": "u2"},
                        "assays": "",
                    }
                ],
            },
        )

    rows = _client(handler).search_samples_by_uid(["u1", "u2"])
    assert pages == [1, 2]
    assert rows[0]["json_metadata"] == {"UID": "u1"}
    assert rows[0]["assay_titles"] == ["Assay A", "Assay B"]
    assert [row["numeric_seek_id"] for row in rows] == [7, 8]


def test_search_samples_by_uid_longest_match_parses_comma_titles():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "total": 1,
                "rows": [
                    {
                        "id": 9,
                        "json_metadata": {"UID": "u1"},
                        "assays": "Alpha, with comma, Alpha, Beta",
                    }
                ],
            },
        )

    rows = _client(handler).search_samples_by_uid(
        ["u1"],
        known_assay_titles=["Alpha", "Alpha, with comma", "Beta"],
    )

    assert rows[0]["assay_titles"] == ["Alpha, with comma", "Alpha", "Beta"]


def test_search_samples_by_uid_longest_match_fails_closed_on_unknown_title():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "total": 1,
                "rows": [
                    {
                        "id": 9,
                        "json_metadata": {"UID": "u1"},
                        "assays": "Known, Unknown",
                    }
                ],
            },
        )

    with pytest.raises(ValueError, match="could not parse"):
        _client(handler).search_samples_by_uid(["u1"], known_assay_titles=["Known"])


def test_search_samples_by_uid_ignores_non_list_rows_and_non_dict_metadata():
    def handler(request):
        return httpx.Response(200, json={"total": 3, "rows": {"not": "a-list"}})

    assert _client(handler).search_samples_by_uid(["u1"]) == []

    def metadata_handler(request):
        return httpx.Response(
            200,
            json={"total": 1, "rows": [{"id": 9, "json_metadata": [], "assays": ""}]},
        )

    assert _client(metadata_handler).search_samples_by_uid(["u1"]) == []


def test_no_per_sample_assay_get():
    urls: list[str] = []

    def handler(request):
        urls.append(str(request.url))
        return httpx.Response(200, json={"total": 0, "rows": []})

    _client(handler).search_samples_by_uid(["UID-1"])
    assert not any("/samples/UID-1/" in url for url in urls)


def test_resolve_assay_title_zero_candidates_fail_closed():
    with pytest.raises(ValueError, match="not accessible"):
        _client(lambda request: httpx.Response(500)).resolve_assay_title("typo", {}, {1})


def test_list_assays_projects_and_title_resolution_branches():
    calls: list[str] = []

    def handler(request):
        calls.append(str(request.url))
        path = request.url.path
        page = request.url.params.get("page")
        if path.endswith("/assays/") and page == "1":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "351", "attributes": {"title": "Comet Chip"}},
                        {"id": "260", "attributes": {"title": "Comet Chip"}},
                    ],
                    "links": {"next": "/nextseek_api/assays/?page=2"},
                    "meta": {"count": 3},
                },
            )
        if path.endswith("/assays/") and page == "2":
            return httpx.Response(
                200,
                json={"data": [{"id": "400", "attributes": {"title": "Solo"}}]},
            )
        if path.endswith("/projects/"):
            return httpx.Response(
                200,
                json={"data": [{"id": "1", "attributes": {"title": "P"}}]},
            )
        if path.endswith("/projects/1/"):
            return httpx.Response(
                200,
                json={"data": {"relationships": {"assays": {
                    "data": [{"id": "351"}, {"id": "400"}],
                }}}},
            )
        raise AssertionError(f"unexpected route {request.url}")

    c = _client(handler)
    title_map = c.list_assays()
    project_ids = c.project_assays(1)
    assert c.list_projects()[0]["id"] == "1"
    assert title_map == {"Comet Chip": [260, 351], "Solo": [400]}
    assert project_ids == {351, 400}
    assert c.resolve_assay_title("Solo", title_map, project_ids) == 400
    assert c.resolve_assay_title("Comet Chip", title_map, project_ids) == 351
    with pytest.raises(ValueError, match="ambiguous"):
        c.resolve_assay_title("Comet Chip", title_map, {260, 351})
    with pytest.raises(ValueError, match="ambiguous"):
        c.resolve_assay_title("Solo", title_map, {351})
    assert any("page=2" in url for url in calls)


def test_path2_membership_normalizes_int_vs_str_id():
    c = _client(lambda request: httpx.Response(500))
    c.assay_samples = lambda assay_ids: {351: {"324503"}, 260: {"999"}}  # type: ignore[method-assign]
    resolved = c.resolve_current_assay_titles(
        ["Comet Chip Analysis - Data Attached"],
        sample_numeric_id=324503,
        title_map={"Comet Chip Analysis - Data Attached": [351, 260]},
        project_assay_ids={351, 260},
    )
    assert resolved == {351}


def test_current_assay_titles_fast_path_and_failure_branches():
    c = _client(lambda request: httpx.Response(500))
    c.assay_samples = pytest.fail  # type: ignore[method-assign]
    assert c.resolve_current_assay_titles(
        ["Solo"],
        sample_numeric_id="324503",
        title_map={"Solo": [351]},
        project_assay_ids={351},
    ) == {351}

    with pytest.raises(ValueError, match="not accessible"):
        c.resolve_current_assay_titles(
            ["Missing"],
            sample_numeric_id="324503",
            title_map={},
            project_assay_ids=set(),
        )

    c2 = _client(lambda request: httpx.Response(500))
    c2.assay_samples = lambda assay_ids: {351: {"111"}, 260: {"222"}}  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="could not resolve"):
        c2.resolve_current_assay_titles(
            ["Ambiguous"],
            sample_numeric_id="324503",
            title_map={"Ambiguous": [351, 260]},
            project_assay_ids={351, 260},
        )

    with pytest.raises(ValueError, match="not in project"):
        c2.resolve_current_assay_titles(
            ["Out of Project"],
            sample_numeric_id="324503",
            title_map={"Out of Project": [999]},
            project_assay_ids={351, 260},
        )


def test_paged_get_rejects_non_list_data_and_stops_on_empty_next():
    def bad_handler(request):
        return httpx.Response(200, json={"data": {"id": "not-a-list"}})

    with pytest.raises(ValueError, match="expected list data"):
        _client(bad_handler).list_projects()

    calls = 0

    def empty_next_handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "data": [],
                "links": {"next": "/nextseek_api/projects/?page=2"},
                "meta": {"count": 10},
            },
        )

    assert _client(empty_next_handler).list_projects() == []
    assert calls == 1


def test_client_surface_denylist_no_upload_methods():
    public = {
        name
        for name in dir(buc.BatchUploadClient)
        if not name.startswith("_") and callable(getattr(buc.BatchUploadClient, name))
    }
    forbidden = {"start", "upload", "update", "submit", "read_samples", "validate"}
    assert public.isdisjoint(forbidden)
    tree = ast.parse(pathlib.Path("build_context/plugins/nextseek/bin/_batch_upload_client.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "batch-upload/start" not in node.value
            assert "/start/" not in node.value
        if isinstance(node, ast.FunctionDef):
            assert not (node.name == "request" and not node.name.startswith("_"))


def test_mock_transport_rejects_non_get_except_search_validate(tmp_path):
    workbook = tmp_path / "payload.xlsx"
    workbook.write_bytes(b"xlsx")

    def handler(request):
        path = request.url.path
        if request.method != "GET":
            assert path.endswith("/samples/advanced_search/") or path.endswith("/batch-upload/validate/")
        if path.endswith("/samples/advanced_search/"):
            return httpx.Response(200, json={"total": 0, "rows": []})
        if path.endswith("/batch-upload/validate/"):
            return httpx.Response(200, json={"valid": True})
        if path.endswith("/assays/"):
            return httpx.Response(200, json={"data": []})
        if path.endswith("/projects/1/"):
            return httpx.Response(200, json={"data": {"relationships": {"assays": {"data": []}}}})
        if path.endswith("/people/current/"):
            return httpx.Response(200, json={"data": {"id": "1"}})
        if path.endswith("/projects/"):
            return httpx.Response(200, json={"data": []})
        if path.endswith("/sample_types/"):
            return httpx.Response(200, json={"data": []})
        if "/sample_types/" in path:
            return httpx.Response(200, json={"data": {"attributes": {"sample_attributes": []}}})
        if path.endswith("/assays/351/"):
            return httpx.Response(200, json={"data": {"relationships": {"samples": {"data": []}}}})
        raise AssertionError(f"unexpected route {request.method} {path}")

    c = _client(handler)
    c.list_sample_types()
    c.sample_type_attributes("TIS")
    c.current_person()
    c.list_projects()
    c.list_assays()
    c.project_assays(1)
    c.assay_samples({351})
    c.search_samples_by_uid(["u1"])
    c.validate_file(workbook, project_id=1, checks="structure")


def test_from_env_credential_precedence_and_transport(monkeypatch):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": []})

    monkeypatch.setenv("NEXTSEEK_URL", "http://ns.test")
    monkeypatch.setenv("NEXTSEEK_USERNAME", "next-user")
    monkeypatch.setenv("NEXTSEEK_PASSWORD", "next-pass")
    monkeypatch.setenv("API_USER", "legacy-user")
    monkeypatch.setenv("API_PASS", "legacy-pass")
    c = buc.BatchUploadClient.from_env(transport=httpx.MockTransport(handler))
    c.list_sample_types()
    assert seen["auth"].startswith("Basic ")
    encoded = seen["auth"].removeprefix("Basic ")
    assert base64.b64decode(encoded).decode() == "next-user:next-pass"
    assert str(c._client.base_url).rstrip("/") == "http://ns.test"


def test_from_env_legacy_fallback_partial_nextseek_pair_and_missing(monkeypatch):
    for key in ("NEXTSEEK_URL", "NEXTSEEK_BASE_URL", "NEXTSEEK_USERNAME", "NEXTSEEK_PASSWORD", "API_USER", "API_PASS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NEXTSEEK_BASE_URL", "http://fallback.ns.test")
    monkeypatch.setenv("API_USER", "u")
    monkeypatch.setenv("API_PASS", "p")
    assert isinstance(buc.BatchUploadClient.from_env(), buc.BatchUploadClient)
    monkeypatch.setenv("NEXTSEEK_USERNAME", "partial-user")
    with pytest.raises(SystemExit) as partial:
        buc.BatchUploadClient.from_env()
    assert partial.value.code == 2
    monkeypatch.delenv("NEXTSEEK_USERNAME")
    monkeypatch.delenv("API_PASS")
    with pytest.raises(SystemExit) as exc:
        buc.BatchUploadClient.from_env()
    assert exc.value.code == 2
