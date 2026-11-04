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
# Two questions in rna_question_cases.json expect a refusal purely on species —
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


# ---------------------------------------------------------------------------
# rnavar
#
# On 2026-10-29 a dev user asked which genetic variants a lab's breast cancer
# cells express. rnavar was the answer and was never offered. Its entry was the
# thinnest in the atlas: no input or data signals, and an `answers` line in
# pipeline words ("appear in the transcripts") that a scientist's "which
# variants do these cells express" does not echo, while "express" pulls
# toward rnaseq.
# ---------------------------------------------------------------------------

def test_rnavar_carries_the_fields_the_selector_reads():
    entry = load_atlas()["pipelines"]["rnavar"]
    for field in ("answers", "assumes_prep", "input_you_must_have", "data_signals"):
        assert entry.get(field), f"rnavar has no {field}"
    assert entry["data_signals"]["library_strategy"] == ["RNA-Seq"]
    assert entry["data_signals"]["library_source"] == ["TRANSCRIPTOMIC"]


def test_rnavar_answers_in_the_words_a_scientist_uses():
    answers = load_atlas()["pipelines"]["rnavar"]["answers"].lower()
    for word in ("genetic variants", "mutations", "snps", "carry", "express"):
        assert word in answers, word


def test_a_variant_question_that_says_express_is_still_rnavar():
    pipelines = load_atlas()["pipelines"]
    assert "express" in pipelines["rnavar"]["versus"]["rnaseq"]["differs_by"]
    assert "variant question" in pipelines["rnaseq"]["versus"]["rnavar"]["differs_by"]


def test_rnavar_prep_kit_hints_name_only_library_preps():
    """Selection reads prep_kit_hints as text, so a word about the biology there ("tumour",
    "mutation") would lean any vague request on tumour RNA-seq toward rnavar."""
    hints = [h.lower() for h in load_atlas()["pipelines"]["rnavar"]["data_signals"]["prep_kit_hints"]]
    for word in ("tumour", "tumor", "cancer", "mutation"):
        assert not any(word in hint for hint in hints), word
