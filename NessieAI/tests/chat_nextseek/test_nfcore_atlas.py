"""load_atlas: read the curated nf-core RNA atlas and validate its integrity."""
import json
import warnings

import pytest

from chat_nextseek.seqera.nfcore_atlas import ATLAS_PATH, AtlasError, load_atlas


def _atlas(pipelines, guidance=None):
    return {
        "guidance": guidance or {"ties_are_acceptable": True, "notes": ["x"]},
        "pipelines": pipelines,
    }


def _write(tmp_path, payload):
    p = tmp_path / "atlas.json"
    p.write_text(json.dumps(payload))
    return p


def test_loads_a_minimal_valid_atlas(tmp_path):
    payload = _atlas({
        "rnaseq": {"revision": "3.18.0", "answers": "expression", "versus": {}},
    })
    out = load_atlas(_write(tmp_path, payload))
    assert out["pipelines"]["rnaseq"]["revision"] == "3.18.0"
    assert out["guidance"]["ties_are_acceptable"] is True


def test_raises_when_pipelines_key_is_missing(tmp_path):
    payload = {"guidance": {"ties_are_acceptable": True, "notes": ["x"]}}
    with pytest.raises(AtlasError, match="pipelines"):
        load_atlas(_write(tmp_path, payload))


def test_raises_when_top_level_is_not_an_object(tmp_path):
    p = tmp_path / "atlas.json"
    p.write_text(json.dumps(["not", "an", "object"]))
    with pytest.raises(AtlasError, match="pipelines"):
        load_atlas(p)


def test_raises_when_a_pipeline_entry_is_not_an_object(tmp_path):
    payload = _atlas({"rnaseq": "not an object"})
    with pytest.raises(AtlasError, match="rnaseq"):
        load_atlas(_write(tmp_path, payload))


def test_raises_when_versus_names_an_unknown_pipeline(tmp_path):
    payload = _atlas({
        "rnaseq": {"revision": "3.18.0", "answers": "expression",
                   "versus": {"nosuchthing": {"differs_by": "…"}}},
    })
    with pytest.raises(AtlasError, match="nosuchthing"):
        load_atlas(_write(tmp_path, payload))


def test_raises_when_an_entry_has_no_revision(tmp_path):
    payload = _atlas({"rnaseq": {"answers": "expression", "versus": {}}})
    with pytest.raises(AtlasError, match="revision"):
        load_atlas(_write(tmp_path, payload))


def test_warns_but_loads_when_a_versus_pair_is_one_sided(tmp_path):
    payload = _atlas({
        "rnaseq": {"revision": "3.18.0", "answers": "a",
                   "versus": {"rnasplice": {"differs_by": "…"}}},
        "rnasplice": {"revision": "1.0.4", "answers": "b", "versus": {}},
    })
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = load_atlas(_write(tmp_path, payload))
    assert out["pipelines"]["rnasplice"]["revision"] == "1.0.4"
    assert any("one-sided" in str(w.message) for w in caught)


def test_warns_when_revision_disagrees_with_the_catalog(tmp_path):
    payload = _atlas({"rnaseq": {"revision": "9.9.9", "answers": "a", "versus": {}}})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load_atlas(_write(tmp_path, payload))
    assert any("3.18.0" in str(w.message) for w in caught)


def test_shipped_atlas_loads_clean(recwarn):
    out = load_atlas(ATLAS_PATH)
    assert set(out["pipelines"]) == {
        "rnaseq", "scrnaseq", "smrnaseq", "hlatyping", "rnafusion", "rnasplice",
        "rnavar", "riboseq", "denovotranscript", "differentialabundance",
    }
    assert [str(w.message) for w in recwarn.list] == []


def test_shipped_atlas_has_ask_user_on_the_ties_that_matter():
    entry = load_atlas(ATLAS_PATH)["pipelines"]["rnaseq"]
    for neighbour in ("rnasplice", "rnafusion", "rnavar", "scrnaseq"):
        assert entry["versus"][neighbour].get("ask_user"), neighbour


# ---------------------------------------------------------------------------
# Species constraints
#
# Two migrated stories (sha-macaque-hla, sas-mouse-fusion) expect a refusal purely on species —
# HLA typing on macaques, fusion calling on mouse. Neither is answerable unless
# the atlas says so, because nothing else in the payload carries the limit: the
# sample metadata gives the organism, but only the atlas can say which
# pipelines that organism rules out.
# ---------------------------------------------------------------------------

SPECIES_LIMITED = {
    # rnafusion pins genome = 'GRCh38' in nextflow.config and its README states
    # "GRCh38 is the only supported reference" (verified at revision 4.1.3).
    "rnafusion": "HUMAN ONLY",
    # HLA is the human MHC; OptiType maps against human class I alleles.
    "hlatyping": "HUMAN ONLY",
    # Needs mirtrace_species plus mature/hairpin FASTAs — miRBase species only.
    "smrnaseq": "miRBase",
}


def test_species_limited_pipelines_declare_their_constraint():
    atlas = load_atlas()
    for key, marker in SPECIES_LIMITED.items():
        entry = atlas["pipelines"][key]
        constraint = entry.get("species_constraint")
        assert constraint, f"{key} lost its species_constraint"
        assert marker.lower() in constraint.lower(), f"{key}: {constraint!r} no longer says {marker!r}"


def test_species_constraint_is_explained_in_guidance():
    """A field the model is never told to read is a field that does nothing."""
    notes = " ".join(load_atlas()["guidance"]["notes"])
    assert "species_constraint" in notes
    assert "hard limit" in notes.lower()


def test_pipelines_without_a_species_limit_do_not_claim_one():
    """Absence must mean 'unrestricted', not 'nobody checked' — so an empty or
    placeholder value is worse than no key at all."""
    for key, entry in load_atlas()["pipelines"].items():
        if key in SPECIES_LIMITED:
            continue
        assert "species_constraint" not in entry or entry["species_constraint"], \
            f"{key} carries an empty species_constraint"


import json as _json

import chat_nextseek.seqera.nfcore_atlas as nfcore_atlas


def _file(tmp_path, name, pipelines, notes=("x",)):
    path = tmp_path / name
    path.write_text(_json.dumps({"guidance": {"notes": list(notes)}, "pipelines": pipelines}))
    return path


def test_the_default_atlas_merges_rna_and_dna():
    pipelines = load_atlas()["pipelines"]
    assert "rnaseq" in pipelines and "sarek" in pipelines


def test_a_key_in_two_files_is_refused(tmp_path, monkeypatch):
    a = _file(tmp_path, "rna.json", {"sarek": {"revision": "3.10.0", "versus": {}}})
    b = _file(tmp_path, "dna.json", {"sarek": {"revision": "3.10.0", "versus": {}}})
    monkeypatch.setattr(nfcore_atlas, "ATLAS_PATHS", (a, b))
    with pytest.raises(AtlasError, match="sarek"):
        load_atlas()


def test_versus_may_point_across_files_and_notes_add_up(tmp_path, monkeypatch):
    a = _file(tmp_path, "rna.json", {"rnavar": {"revision": "1.3.0", "versus": {"sarek": {"differs_by": "d"}}}},
              notes=("rna note",))
    b = _file(tmp_path, "dna.json", {"sarek": {"revision": "3.10.0", "versus": {"rnavar": {"differs_by": "d"}}}},
              notes=("dna note",))
    monkeypatch.setattr(nfcore_atlas, "ATLAS_PATHS", (a, b))
    atlas = load_atlas()
    assert set(atlas["pipelines"]) == {"rnavar", "sarek"}
    assert atlas["guidance"]["notes"] == ["rna note", "dna note"]


def test_a_single_path_still_reads_one_file():
    assert "sarek" not in load_atlas(ATLAS_PATH)["pipelines"]


def test_a_single_file_may_name_a_pipeline_that_lives_in_its_sibling(tmp_path):
    lone = _file(tmp_path, "lone.json", {"rnavar": {"revision": "1.3.0", "versus": {"sarek": {"differs_by": "d"}}}})
    assert "rnavar" in load_atlas(lone)["pipelines"]


def test_a_single_file_still_refuses_a_versus_key_no_file_has(tmp_path):
    lone = _file(tmp_path, "lone.json", {"rnavar": {"revision": "1.3.0", "versus": {"nosuch": {"differs_by": "d"}}}})
    with pytest.raises(AtlasError, match="nosuch"):
        load_atlas(lone)


def test_a_single_file_load_fails_loudly_on_a_malformed_sibling(tmp_path, monkeypatch):
    lone = _file(tmp_path, "lone.json", {"rnavar": {"revision": "1.3.0", "versus": {}}})
    broken = tmp_path / "broken_sibling.json"
    broken.write_text("{not json")
    monkeypatch.setattr(nfcore_atlas, "ATLAS_PATHS", (broken,))
    with pytest.raises(AtlasError, match="broken_sibling.json"):
        load_atlas(lone)


NEW_CHOOSABLE = ("atacseq", "chipseq", "methylseq")


@pytest.mark.parametrize("key", NEW_CHOOSABLE)
def test_new_choosable_pipelines_are_in_the_merged_atlas_at_their_catalog_pin(key):
    from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG
    entry = load_atlas()["pipelines"][key]
    assert entry["revision"] == NFCORE_PIPELINE_CATALOG[key]["default_revision"]
    for field in ("answers", "assumes_prep", "input_you_must_have", "data_signals", "species_constraint", "versus"):
        assert entry.get(field), (key, field)


def test_bisulfite_reads_are_kept_away_from_sarek_both_ways():
    pipelines = load_atlas()["pipelines"]
    assert "sarek" in pipelines["methylseq"]["versus"]
    assert "methylseq" in pipelines["sarek"]["versus"]


def test_atac_and_chip_point_at_each_other():
    pipelines = load_atlas()["pipelines"]
    assert "chipseq" in pipelines["atacseq"]["versus"] and "atacseq" in pipelines["chipseq"]["versus"]


def test_differentialabundance_is_choosable_but_not_launchable():
    assert load_atlas()["pipelines"]["differentialabundance"].get("launchable") is False
