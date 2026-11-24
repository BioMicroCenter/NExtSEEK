"""definitions_from_prod's plan: production's definitions onto dev, joined by code or title, never by id.

Synthetic bundles only (the shape ``definitions_from_prod.dump.bundle`` returns); no real catalog rows.
"""
import copy

from nextseek_api.definitions_from_prod import sync


def attr(t, title, pos, **kw):
    row = {"sample_type": t, "title": title, "pos": pos, "attribute_type": "String", "required": 0, "is_title": 0,
           "description": None, "unit": None, "vocab": None, "linked_sample_type": None, "pid": None,
           "isa_tag": None, "allow_cv_free_text": 0, "template_column_index": pos, "original_accessor_name": title}
    row.update(kw)
    return row


def ctx(code, **kw):
    row = {"sample_type": code, "name": code, "description": f"{code} text", "Tags": None,
           "required_metadata": "UID", "standard_metadata": None, "possible_metadata_fields": None, "clade": None,
           "parent_sampletypes": None, "child_sampletypes": None, "associated_assay_parents": None,
           "associated_assay_children": None, "repository_attributes": None, "sampletype_file_link": None}
    row.update(kw)
    return row


def assay(name, **kw):
    row = {"assay_name": name, "Description": f"{name} text", "Tags": None, "Alternative_Assay_Names": None,
           "Required_Parent_Sample_Types": "TIS", "Optional_Parent_Sample_Types": None,
           "Children_Sample_Types": "RNA", "Parent_Clade_Type": None, "Child_Clade_Type": None,
           "AssaySheet_Link": None, "AssociatedRepository": None, "Critical_Attributes": None,
           "Protocols_Phrases": None, "Protocols_UIDs": None}
    row.update(kw)
    return row


def bundle(types, attrs, contexts=(), assays=(), internal=(), clades=(), meanings=(), vocabs=(), units=()):
    return {"tables": {
        "seek.sample_types": [{"title": t, "description": f"{t} desc", "uploaded_template": 0,
                               "external_identifier": None} for t in types],
        "seek.sample_attributes": list(attrs),
        "seek.sample_attribute_types": [{"title": "String"}, {"title": "Text"}],
        "seek.sample_controlled_vocabs": [{"title": v} for v in vocabs],
        "seek.sample_controlled_vocab_terms": [],
        "seek.units": [{"title": u, "symbol": u} for u in units],
        "seek.sample_counts": [],
        "dmac.sample_types_context": list(contexts),
        "dmac.assay_context": list(assays),
        "dmac.internal_assays": [{"internal_assay_title": t} for t in internal],
        "dmac.clades": [{"title": "Source"}, {"title": "Processed"}],
        "dmac.sample_types_clades": [{"sample_type": t, "clade": c} for t, c in clades],
        "dmac.sample_attributes_unique": list(meanings),
    }}


def base():
    prod = bundle(["TIS", "RNA"],
                  [attr("TIS", "UID", 1, is_title=1, required=1), attr("TIS", "Name", 2), attr("TIS", "DOI", 3),
                   attr("RNA", "UID", 1, is_title=1, required=1), attr("RNA", "Kit", 2)],
                  contexts=[ctx("TIS"), ctx("RNA")], assays=[assay("RNA Extraction")],
                  internal=["RNA Extraction"], clades=[("TIS", "Source"), ("RNA", "Processed")])
    return prod, copy.deepcopy(prod)


def no_actions(p):
    return sync.action_count(p) == 0


def test_identical_boxes_plan_nothing():
    prod, dev = base()
    assert no_actions(sync.plan(prod, dev))


def test_prod_only_type_is_created_through_seek_with_its_title_attribute_only():
    prod, dev = base()
    prod["tables"]["seek.sample_types"].append({"title": "PTN", "description": "protein", "uploaded_template": 0,
                                                "external_identifier": None})
    prod["tables"]["seek.sample_attributes"] += [attr("PTN", "UID", 1, is_title=1, required=1, description="id"),
                                                 attr("PTN", "Purity", 2)]
    p = sync.plan(prod, dev)
    assert p["seek_types"] == [{"title": "PTN", "description": "protein",
                                "title_attribute": {"title": "UID", "sample_attribute_type": "String",
                                                    "required": True, "description": "id"}}]
    assert "PTN" not in p["attributes"]          # its other attributes wait for the type
    assert p["waiting"]["attributes"] == {"PTN": 1}   # Purity; UID comes with the type


def test_missing_attribute_is_created_at_production_position_in_one_batch_per_type():
    prod, dev = base()
    dev["tables"]["seek.sample_attributes"] = [a for a in dev["tables"]["seek.sample_attributes"]
                                               if (a["sample_type"], a["title"]) != ("TIS", "Name")]
    dev["tables"]["seek.sample_attributes"].append(attr("TIS", "DevOnly", 9))
    p = sync.plan(prod, dev)
    tis = p["attributes"]["TIS"]
    assert [c["title"] for c in tis["creates"]] == ["Name"]
    assert tis["creates"][0]["pos"] == 2           # after UID, before DOI, as on production
    assert tis["patches"] == []
    assert p["expected"]["dev_only"]["seek.sample_attributes"] == [["TIS", "DevOnly"]]


def test_description_patch_and_null_equals_empty():
    prod, dev = base()
    for a in prod["tables"]["seek.sample_attributes"]:
        if a["title"] == "Name":
            a["description"] = "the lab's name"
        if a["title"] == "Kit":
            a["description"] = ""                   # '' on production, NULL on dev: the same
    p = sync.plan(prod, dev)
    assert p["attributes"]["TIS"]["patches"] == [{"attribute": "Name", "changes": {"description": "the lab's name"}}]
    assert "RNA" not in p["attributes"]


def test_shared_order_follows_production_and_dev_only_attributes_keep_their_slot():
    prod, dev = base()
    # dev: UID, DOI, Extra, Name ; prod: UID, Name, DOI
    dev["tables"]["seek.sample_attributes"] = [a for a in dev["tables"]["seek.sample_attributes"] if a["sample_type"] != "TIS"]
    dev["tables"]["seek.sample_attributes"] += [attr("TIS", "UID", 1, is_title=1, required=1), attr("TIS", "DOI", 2),
                                                attr("TIS", "Extra", 3), attr("TIS", "Name", 4)]
    p = sync.plan(prod, dev)
    assert p["attributes"]["TIS"]["order"] == ["UID", "Name", "Extra", "DOI"]
    assert p["attributes"]["TIS"]["patches"] == [
        {"attribute": "UID", "changes": {"pos": 1}}, {"attribute": "Name", "changes": {"pos": 2}},
        {"attribute": "Extra", "changes": {"pos": 3}}, {"attribute": "DOI", "changes": {"pos": 4}}]


def test_hold_list_keeps_dev_values():
    prod, dev = base()
    for a in prod["tables"]["seek.sample_attributes"]:
        if a["title"] == "Kit":
            a["attribute_type"] = "Text"
    prod["tables"]["seek.sample_attributes"].append(attr("RNA", "Lot", 3))
    prod["tables"]["dmac.sample_types_context"][0]["description"] = "newer on prod"
    hold = [{"table": "seek.sample_attributes", "key": ["RNA", "Kit"], "column": "attribute_type"},
            {"table": "dmac.sample_types_context", "key": ["TIS"], "column": "description"}]
    p = sync.plan(prod, dev, hold)
    assert p["attributes"]["RNA"]["patches"] == []
    assert [c["title"] for c in p["attributes"]["RNA"]["creates"]] == ["Lot"]
    assert p["context_sample_types"]["update"] == {}
    assert len(p["held"]) == 2
    whole = sync.plan(prod, dev, [{"table": "seek.sample_types", "key": ["RNA"], "column": "*"}])
    assert "RNA" not in whole["attributes"]


def test_context_rows_hand_columns_copied_derived_columns_expected():
    prod, dev = base()
    prod["tables"]["dmac.sample_types_context"][0].update(name="Tissue", parent_sampletypes="PAT",
                                                          repository_attributes='{"GEO": {"a": "b"}}')
    dev["tables"]["dmac.sample_types_context"][0]["repository_attributes"] = '{"GEO":{"a":"b"}}'  # same JSON
    prod["tables"]["dmac.sample_types_context"].append(ctx("PTN", Tags="protein"))
    p = sync.plan(prod, dev)
    assert p["context_sample_types"]["update"] == {"TIS": {"name": "Tissue"}}
    assert p["expected"]["derived_cells"] == [["dmac.sample_types_context", "TIS", "parent_sampletypes"]]
    assert p["context_sample_types"]["create"] == []         # PTN has no SEEK type on dev yet
    assert p["waiting"]["context_sample_types"] == ["PTN"]
    prod["tables"]["seek.sample_types"].append({"title": "PTN"})
    dev["tables"]["seek.sample_types"].append({"title": "PTN"})
    p = sync.plan(prod, dev)
    assert p["context_sample_types"]["create"] == [{"sample_type": "PTN", "name": "PTN", "description": "PTN text",
                                                    "Tags": "protein", "required_metadata": "UID"}]


def test_assays_internal_assays_clades_and_meanings():
    prod, dev = base()
    prod["tables"]["dmac.internal_assays"] += [{"internal_assay_title": "Molecular Cloning"},
                                               {"internal_assay_title": "Chemical challenge"}]
    dev["tables"]["dmac.internal_assays"] += [{"internal_assay_title": "Chemical Challenge"},
                                              {"internal_assay_title": "RNA-Seq"}]
    prod["tables"]["dmac.assay_context"].append(assay("Molecular Cloning", Parent_Clade_Type="Processed"))
    prod["tables"]["dmac.assay_context"][0]["Optional_Parent_Sample_Types"] = "RNA"
    prod["tables"]["dmac.sample_attributes_unique"].append({"sample_type": "", "field_name": "DOI", "meaning": "doi"})
    dev["tables"]["dmac.sample_types_clades"] = [c for c in dev["tables"]["dmac.sample_types_clades"]
                                                 if c["sample_type"] != "RNA"]
    p = sync.plan(prod, dev)
    assert p["internal_assays"] == ["Molecular Cloning"]
    assert p["expected"]["case_only"] == [["dmac.internal_assays", "Chemical challenge", "Chemical Challenge"]]
    assert p["expected"]["dev_only"]["dmac.internal_assays"] == [["RNA-Seq"]]
    assert p["context_assays"]["create"] == [{"assay_name": "Molecular Cloning", "Description": "Molecular Cloning text",
                                              "Required_Parent_Sample_Types": "TIS", "Children_Sample_Types": "RNA",
                                              "Parent_Clade_Type": "Processed"}]
    assert p["context_assays"]["update"] == {"RNA Extraction": {"Optional_Parent_Sample_Types": "RNA"}}
    assert p["clades"] == [{"sample_type": "RNA", "clade": "Processed"}]
    assert p["meanings"] == [{"sample_type": "", "field_name": "DOI", "meaning": "doi"}]


def test_attribute_needing_a_missing_vocabulary_is_blocked_not_created():
    prod, dev = base()
    prod["tables"]["seek.sample_controlled_vocabs"].append({"title": "Mouse Strains"})
    prod["tables"]["seek.sample_attributes"].append(attr("TIS", "Strain", 4, vocab="Mouse Strains"))
    p = sync.plan(prod, dev)
    assert "TIS" not in p["attributes"]
    assert p["blocked"] == [{"table": "seek.sample_attributes", "key": ["TIS", "Strain"],
                             "reason": "needs the vocabulary 'Mouse Strains', which dev lacks"}]
    assert p["expected"]["prod_only"]["seek.sample_controlled_vocabs"] == [["Mouse Strains"]]


def test_seek_own_columns_are_never_patched():
    prod, dev = base()
    for a in prod["tables"]["seek.sample_attributes"]:
        a["template_column_index"] = 99
        a["original_accessor_name"] = a["title"].lower()
    prod["tables"]["seek.sample_types"][0]["uploaded_template"] = 1
    assert no_actions(sync.plan(prod, dev))


def test_applying_the_plan_in_memory_leaves_nothing_to_do():
    """Idempotence: a second plan after the first has landed has no actions."""
    prod, dev = base()
    prod["tables"]["seek.sample_attributes"].append(attr("RNA", "Lot", 3, description="lot"))
    for a in prod["tables"]["seek.sample_attributes"]:
        if a["title"] == "Name":
            a["description"] = "x"
    prod["tables"]["dmac.internal_assays"].append({"internal_assay_title": "Molecular Cloning"})
    first = sync.plan(prod, dev)
    assert not no_actions(first)
    landed = sync.simulate(first, dev)
    assert no_actions(sync.plan(prod, landed))


# --- the command and the applier (box reads and writes replaced) ---------------------------------------------------
import io
import json
import sys
from unittest import mock

import pytest
from django.core.management import call_command

from nextseek_api.definitions_from_prod import apply as applier
from nextseek_api.management.commands import definitions_from_prod as cmd


def run(stdin_obj, *args):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(stdin_obj))), pytest.raises(SystemExit) as ex:
        call_command("definitions_from_prod", "--source", "-", *args, stdout=out, stderr=err)
    return ex.value.code, out.getvalue(), err.getvalue()


def test_dry_run_reads_writes_nothing_and_takes_the_hold_list_from_stdin():
    prod, dev = base()
    prod["tables"]["dmac.sample_types_context"][0]["description"] = "prod text"
    hold = [{"table": "dmac.sample_types_context", "key": ["TIS"], "column": "description"}]
    with mock.patch.object(cmd, "read_box", return_value=dev), mock.patch.object(applier, "apply") as write:
        code, out, _ = run({"prod": prod, "hold": hold}, "--json")
    assert code == 0 and not write.called
    report = json.loads(out)
    assert report["mode"] == "dry_run" and report["plan"]["context_sample_types"]["update"] == {}
    assert report["plan"]["held"] == [{"table": "dmac.sample_types_context", "key": ["TIS"], "column": "description"}]


def test_apply_needs_a_login_and_a_bundle():
    prod, dev = base()
    with mock.patch.object(cmd, "read_box", return_value=dev):
        assert run(prod, "--apply")[0] == 2
        assert run({"prod": {"no": "tables"}})[0] == 2


def test_apply_writes_then_reports_what_is_left():
    prod, dev = base()
    prod["tables"]["dmac.internal_assays"].append({"internal_assay_title": "Molecular Cloning"})
    landed = copy.deepcopy(dev)
    landed["tables"]["dmac.internal_assays"].append({"internal_assay_title": "Molecular Cloning"})
    with mock.patch.object(cmd, "read_box", side_effect=[dev, landed]), \
            mock.patch.object(applier, "actor", return_value="person"), \
            mock.patch.object(applier, "apply", return_value={"internal_assays": {"created": 1, "errors": []}}) as write:
        code, out, _ = run(prod, "--apply", "--seek-login", "op", "--json")
    assert code == 0
    assert write.call_args.args[0]["internal_assays"] == ["Molecular Cloning"]
    assert not any(json.loads(out)["after"]["counts"].values())   # the second read finds nothing to do


def test_attribute_creates_are_not_sent_after_failed_patches():
    prod, dev = base()
    for a in prod["tables"]["seek.sample_attributes"]:
        if a["title"] == "Name":
            a["description"] = "x"
    prod["tables"]["seek.sample_attributes"].append(attr("RNA", "Lot", 3))
    p = sync.plan(prod, dev)
    calls = []

    def fake(kind, targets, dry_run, person):
        calls.append(kind)
        return ({"overall_status": "failed", "outcomes": []}, 409) if kind == "patch" else ({}, 200)

    with mock.patch.object(applier, "_attribute_call", side_effect=fake), \
            mock.patch.object(applier, "running_jobs", return_value=([], [])):
        report = applier.attributes(p, dry_run=False, person="p", wait_s=0, log=lambda m: None)
    assert calls == ["patch"] and report["text"]["http_status"] == 409


def test_no_attribute_write_while_an_attribute_job_runs():
    prod, dev = base()
    prod["tables"]["seek.sample_attributes"].append(attr("RNA", "Lot", 3))
    with mock.patch.object(applier, "_attribute_call") as call, \
            mock.patch.object(applier, "running_jobs", return_value=(["j1"], [])):
        report = applier.attributes(sync.plan(prod, dev), dry_run=False, person="p", wait_s=0, log=lambda m: None)
    assert not call.called and report["jobs"] == ["j1"]


def test_every_request_the_plan_makes_is_one_the_attribute_api_accepts():
    from nextseek_api.attributes.schemas import CREATE_REQUEST_ADAPTER, PATCH_REQUEST_ADAPTER

    prod, dev = base()
    prod["tables"]["seek.sample_controlled_vocabs"].append({"title": "Kits"})
    dev["tables"]["seek.sample_controlled_vocabs"].append({"title": "Kits"})
    for a in prod["tables"]["seek.sample_attributes"]:
        if a["title"] == "Kit":
            a.update(attribute_type="Text", vocab="Kits", required=1, description="the kit")
    prod["tables"]["seek.sample_attributes"].append(attr("RNA", "Lot", 3, required=1, description="lot"))
    reqs = applier.requests_for(sync.plan(prod, dev))
    assert [n for n, _, _ in reqs] == ["text", "rules", "create"]
    for name, kind, targets in reqs:
        adapter = CREATE_REQUEST_ADAPTER if kind == "create" else PATCH_REQUEST_ADAPTER
        adapter.validate_python({"targets": targets, "dry_run": True})
    rules = reqs[1][2][0]["attributes"][0]["changes"]
    assert rules == {"sample_attribute_type": "Text", "sample_controlled_vocab": "Kits", "required": True}


def test_uid_stays_required_the_api_refuses_otherwise():
    prod, dev = base()
    for a in prod["tables"]["seek.sample_attributes"]:
        if (a["sample_type"], a["title"]) == ("TIS", "UID"):
            a.update(required=0, description="the id")
    p = sync.plan(prod, dev)
    assert p["attributes"]["TIS"]["patches"] == [{"attribute": "UID", "changes": {"description": "the id"}}]
    assert p["expected"]["api_refuses"] == [["TIS", "UID", "required"]]


def test_a_partly_refused_request_fails_the_run():
    assert cmd._failed({"attributes": {"text": {"http_status": 207, "errors": [{"code": "x"}]}}})
    assert not cmd._failed({"attributes": {"text": {"http_status": 200}}})


def test_new_type_payload_is_one_the_proxy_accepts():
    prod, dev = base()
    prod["tables"]["seek.sample_types"].append({"title": "PTN", "description": "protein"})
    prod["tables"]["seek.sample_attributes"].append(attr("PTN", "UID", 1, is_title=1, required=1))
    body = cmd.type_payloads(sync.plan(prod, dev), "7")[0]
    assert body["data"]["attributes"]["sample_attributes"][0]["is_title"] is True
    assert body["data"]["relationships"]["projects"]["data"] == [{"id": "7", "type": "projects"}]


# --- review round: the writers run unmocked, and the plan's new rules ---------------------------------------------


@pytest.fixture
def catalog_tables():
    """The dmac and SEEK tables these writers touch, made on each SQLite test database that lacks them."""
    from django.db import connections, router
    from seek.models import Clades, Internal_assays, Sample_types, Sample_types_clades

    made = []
    for m in (Sample_types, Clades, Sample_types_clades, Internal_assays):
        conn = connections[router.db_for_write(m)]
        if m._meta.db_table not in conn.introspection.table_names():
            with conn.schema_editor() as editor:
                editor.create_model(m)
            made.append((conn, m))
    yield
    for conn, m in reversed(made):
        with conn.schema_editor() as editor:
            editor.delete_model(m)


def test_every_name_apply_imports_lazily_resolves():
    from nextseek_api.attributes.auth import AuthenticatedSeekPerson  # noqa: F401
    from nextseek_api.attributes.models_db import AttributeMutationJob  # noqa: F401
    from nextseek_api.attributes.schemas import CREATE_REQUEST_ADAPTER, PATCH_REQUEST_ADAPTER  # noqa: F401
    from nextseek_api.attributes.service import AttributeServices  # noqa: F401
    from nextseek_api.models import (DefinitionAssayUpdateRequest, DefinitionSampleTypeUpdateRequest,  # noqa: F401
                                     DefinitionTiers)
    from nextseek_api.services import definitions_admin as da
    from nextseek_api.services.context_catalog import parse_list  # noqa: F401
    from seek.models import Clades, Internal_assays, Sample_types, Sample_types_clades  # noqa: F401

    assert all(callable(getattr(da, f)) for f in ("create_sample_type", "update_sample_type", "sample_type_row",
                                                   "_write_sample_type", "create_assay", "update_assay",
                                                   "_write_assay", "stored_tiers"))


@pytest.mark.django_db(transaction=True, databases="__all__")
def test_clades_and_internal_assays_write_through_their_models(catalog_tables):
    from seek.models import Clades, Internal_assays, Sample_types, Sample_types_clades

    def seek_type(title):
        from django.utils import timezone
        now = timezone.now()
        return Sample_types.objects.create(title=title, uuid=title, created_at=now, updated_at=now, first_letter="T",
                                           description="", contributor_id=1, deleted_contributor="", template_id=0,
                                           other_creators="")

    tis = seek_type("TIS")
    seek_type("tis")       # alike under _ci; the exact title wins
    Clades.objects.create(title="Source", color="#000", order=1)
    Clades.objects.create(title="Processed", color="#111", order=2)
    assert applier.internal_assays(["Molecular Cloning"], log=lambda m: None) == {"created": 1, "errors": []}
    assert list(Internal_assays.objects.values_list("internal_assay_title", flat=True)) == ["Molecular Cloning"]
    assert applier.clades([{"sample_type": "TIS", "clade": "Source"}])["written"] == 1
    assert applier.clades([{"sample_type": "TIS", "clade": "Processed"}])["written"] == 1   # updates, no twin
    rows = list(Sample_types_clades.objects.values_list("sample_type_id", "clade_id"))
    assert rows == [(tis.id, Clades.objects.get(title="Processed").id)]
    assert applier.clades([{"sample_type": "PTN", "clade": "Source"}])["errors"]


def test_attribute_titles_alike_but_for_case_are_listed_not_created():
    prod, dev = base()
    prod["tables"]["seek.sample_attributes"].append(attr("TIS", "Cell Count", 4))
    dev["tables"]["seek.sample_attributes"].append(attr("TIS", "cell count", 4))
    prod["tables"]["seek.sample_types"].append({"title": "Rna"})
    p = sync.plan(prod, dev)
    assert "TIS" not in p["attributes"] and p["seek_types"] == []
    assert ["seek.sample_attributes", "TIS/Cell Count", "TIS/cell count"] in p["expected"]["case_only"]
    assert ["seek.sample_types", "Rna", "RNA"] in p["expected"]["case_only"]


def test_a_type_the_api_cannot_name_is_blocked():
    prod, dev = base()
    for b in (prod, dev):
        b["tables"]["seek.sample_types"].append({"title": "tis"})
    prod["tables"]["seek.sample_attributes"].append(attr("TIS", "Lot", 4))
    p = sync.plan(prod, dev)
    assert "TIS" not in p["attributes"]
    assert any(b["key"] == ["TIS"] for b in p["blocked"])


def test_holds_reach_every_kind_of_write_and_unread_holds_are_listed():
    prod, dev = base()
    prod["tables"]["seek.sample_attributes"].append(attr("RNA", "Lot", 3))
    prod["tables"]["dmac.internal_assays"].append({"internal_assay_title": "Molecular Cloning"})
    prod["tables"]["dmac.sample_attributes_unique"].append({"sample_type": "", "field_name": "DOI", "meaning": "d"})
    dev["tables"]["dmac.sample_types_clades"] = []
    hold = [{"table": "seek.sample_attributes", "key": ["RNA", "Lot"], "column": "*"},
            {"table": "dmac.internal_assays", "key": ["Molecular Cloning"], "column": "*"},
            {"table": "dmac.sample_attributes_unique", "key": ["", "DOI"], "column": "*"},
            {"table": "dmac.sample_types_clades", "key": ["TIS"], "column": "*"},
            {"table": "dmac.sample_types_context", "key": ["NOPE"], "column": "description"}]
    p = sync.plan(prod, dev, hold)
    assert "RNA" not in p["attributes"] and p["internal_assays"] == [] and p["meanings"] == []
    assert p["clades"] == [{"sample_type": "RNA", "clade": "Processed"}]
    assert p["expected"]["unused_holds"] == [hold[-1]]


def test_null_on_production_clears_dev_and_tiers_compare_as_lists():
    prod, dev = base()
    dev["tables"]["dmac.sample_types_context"][0]["Tags"] = "old tag"
    dev["tables"]["dmac.sample_types_context"][1]["required_metadata"] = "UID,Kit"
    prod["tables"]["dmac.sample_types_context"][1]["required_metadata"] = "UID, Kit"
    p = sync.plan(prod, dev)
    assert p["context_sample_types"]["update"] == {"TIS": {"Tags": ""}}
    assert no_actions(sync.plan(prod, sync.simulate(p, dev)))


def test_tier_titles_the_type_lacks_on_dev_are_listed_not_sent():
    prod, dev = base()
    prod["tables"]["dmac.sample_types_context"][0]["required_metadata"] = "UID, Ghost"
    p = sync.plan(prod, dev)
    assert p["expected"]["tier_titles_missing"] == [["TIS", "required_metadata", ["Ghost"]]]
    assert "TIS" not in p["context_sample_types"]["update"]


def test_seek_type_text_is_listed_for_seek_not_counted():
    prod, dev = base()
    prod["tables"]["seek.sample_types"][0]["description"] = "newer"
    p = sync.plan(prod, dev)
    assert no_actions(p) and p["expected"]["seek_type_text"] == [{"title": "TIS", "description": "newer"}]


def test_patch_naming_something_dev_lacks_is_blocked():
    prod, dev = base()
    prod["tables"]["seek.sample_controlled_vocabs"].append({"title": "Kits"})
    for a in prod["tables"]["seek.sample_attributes"]:
        if a["title"] == "Kit":
            a["vocab"] = "Kits"
    p = sync.plan(prod, dev)
    assert "RNA" not in p["attributes"]
    assert p["blocked"][0]["key"] == ["RNA", "Kit", "vocab"]


def test_a_skipped_attribute_write_fails_the_run():
    assert cmd._failed({"attributes": {"skipped": "attribute jobs still running", "jobs": ["j (running)"]}})


@pytest.mark.django_db
def test_only_a_live_attribute_job_holds_the_sync_back():
    from datetime import timedelta

    from django.utils import timezone

    from nextseek_api.attributes.models_db import AttributeMutationJob

    now = timezone.now()
    fields = {f.name for f in AttributeMutationJob._meta.get_fields()}
    with mock.patch.object(AttributeMutationJob.objects, "filter") as q:
        q.return_value.values_list.return_value = [
            ("old", "running", now - timedelta(days=48), now - timedelta(days=48)),   # crashed run, never closed
            ("held", "running", now + timedelta(minutes=2), now - timedelta(days=1)),  # lease still held
            ("new", "queued", None, now - timedelta(minutes=5)),                       # not claimed yet
        ]
        live, stale = applier.running_jobs(now)
    assert {"lease_expires_at", "created_at", "terminal_result"} <= fields
    assert [x.split()[0] for x in live] == ["held", "new"] and [x.split()[0] for x in stale] == ["old"]
