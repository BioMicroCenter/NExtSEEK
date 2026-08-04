"""Required-user-param elicitation: gating, validation, and the question text.

The contract these guard: a pipeline that needs a value nobody can derive (a CRISPR
guide, a Hi-C digestion protocol) must BLOCK at configure_run and hand back a
question, rather than defaulting. The failure mode being prevented is silent — a
wrong protospacer reports a wrong editing efficiency instead of erroring.
"""
import json

import pytest

from chat_nextseek.seqera.user_params import (
    missing_user_params,
    render_elicitation,
    required_user_params,
    validate_user_params,
)


# --- crisprseq: the analysis mode selects which other answers are needed --------

def test_only_analysis_is_asked_before_the_mode_is_known():
    """Ask for the gate first. Demanding protospacer AND library up front would be
    incoherent — they belong to mutually exclusive modes."""
    missing = [s["name"] for s in missing_user_params("crisprseq", {})]
    assert missing == ["analysis"]


def test_targeted_mode_asks_for_guide_and_amplicon_not_library():
    missing = [s["name"] for s in missing_user_params("crisprseq", {"analysis": "targeted"})]
    assert missing == ["protospacer", "reference_fasta"]
    assert "library" not in missing


def test_screening_mode_asks_for_library_not_guide():
    missing = [s["name"] for s in missing_user_params("crisprseq", {"analysis": "screening"})]
    assert missing == ["library"]


def test_fully_answered_targeted_run_is_not_blocked():
    params = {"analysis": "targeted", "protospacer": "GGCACTGCGGCTGGAGGTGG",
              "reference_fasta": "/net/bmc-pub10/refs/amplicon.fa"}
    assert missing_user_params("crisprseq", params) == []
    assert validate_user_params("crisprseq", params) == []


# --- validation catches the paste-level mistakes -------------------------------

@pytest.mark.parametrize("bad,why", [
    ("GGCACTGCGG CTGGAGGTGG", "whitespace"),
    ("GGCACUGCGGCUGGAGGUGG", "RNA U instead of DNA T"),
    ("GGCACTGCGGCTGGAGGTGG-PAM", "punctuation"),
    ("", "blank is caught as missing, not invalid"),
])
def test_malformed_guide_sequences_are_rejected_or_treated_as_missing(bad, why):
    params = {"analysis": "targeted", "protospacer": bad,
              "reference_fasta": "/net/x.fa"}
    errors = validate_user_params("crisprseq", params)
    missing = [s["name"] for s in missing_user_params("crisprseq", params)]
    assert errors or "protospacer" in missing, f"{why}: {bad!r} slipped through"


def test_enum_typo_is_rejected_with_the_allowed_values():
    errors = validate_user_params("crisprseq", {"analysis": "targetted"})   # sic
    assert errors and "targeted" in errors[0]


def test_a_valid_guide_passes():
    assert validate_user_params("crisprseq", {"analysis": "targeted",
                                              "protospacer": "GGCACTGCGGCTGGAGGTGG"}) == []


# --- hic: required_unless, the inverse gate ------------------------------------

def test_hic_asks_for_digestion_by_default():
    assert [s["name"] for s in missing_user_params("hic", {})] == ["digestion"]


def test_dnase_hic_escapes_the_digestion_question():
    """DNase Hi-C has no enzyme digestion. `dnase` is a separate boolean, not a
    member of the digestion enum, so it must be expressible as an escape."""
    for truthy in (True, "true", "True"):
        assert missing_user_params("hic", {"dnase": truthy}) == [], truthy


def test_dnase_false_still_requires_digestion():
    assert [s["name"] for s in missing_user_params("hic", {"dnase": False})] == ["digestion"]


def test_dnase_is_not_a_valid_digestion_value():
    """It would fail nf-schema: the enum is hindiii|mboi|dpnii|arima."""
    assert validate_user_params("hic", {"digestion": "dnase"})


def test_hic_accepts_a_real_enzyme():
    assert validate_user_params("hic", {"digestion": "arima"}) == []


# --- the question the user actually sees ---------------------------------------

def test_elicitation_carries_a_definition_and_an_example():
    text = render_elicitation(missing_user_params("crisprseq", {"analysis": "targeted"}))
    assert "protospacer" in text
    assert "WITHOUT the PAM" in text          # the definition, not just the name
    assert "GGCACTGCGGCTGGAGGTGG" in text     # a concrete example
    assert "guess" in text.lower()            # states that it will not be invented


def test_elicitation_lists_allowed_values_for_an_enum():
    text = render_elicitation(missing_user_params("crisprseq", {}))
    assert "targeted" in text and "screening" in text


def test_elicitation_warns_when_a_value_varies_per_sample():
    text = render_elicitation([{"name": "protospacer", "definition": "d", "example": "e",
                                "scope": "sample"}])
    assert "PER SAMPLE" in text and "upload" in text


def test_elicitation_of_nothing_is_empty():
    assert render_elicitation([]) == ""


# --- pipelines without the contract are unaffected ------------------------------

def test_pipelines_with_no_required_user_params_are_never_blocked():
    for key in ("rnaseq", "scrnaseq", "seqinspector", "hlatyping"):
        assert required_user_params(key) == [], key
        assert missing_user_params(key, {}) == [], key


# --- configure_run is the enforcement point ------------------------------------

def test_configure_run_refuses_and_returns_a_question(monkeypatch, tmp_path):
    """The block must live in code, not only in the prompt: an instruction can be
    forgotten mid-conversation, a fail-closed tool cannot."""
    from chat_nextseek.pipeline import agent_tools as at
    state = {"pipeline_key": "crisprseq",
             "artifacts": {"samplesheet": str(tmp_path / "s.csv"), "base_dir": str(tmp_path)},
             "bundle_key": None}
    out = json.loads(at.tool_configure_run(object(), state, {"params": {}}, str(tmp_path)))
    assert out["ok"] is False
    assert out["needs_user_input"] == ["analysis"]
    assert "targeted" in out["ask_the_user"]


def test_configure_run_reports_an_invalid_value_rather_than_correcting_it(tmp_path):
    from chat_nextseek.pipeline import agent_tools as at
    state = {"pipeline_key": "crisprseq",
             "artifacts": {"samplesheet": str(tmp_path / "s.csv"), "base_dir": str(tmp_path)},
             "bundle_key": None}
    out = json.loads(at.tool_configure_run(
        object(), state, {"params": {"analysis": "targeted", "protospacer": "not-dna"}}, str(tmp_path)))
    assert out["ok"] is False
    assert any("protospacer" in e for e in out["invalid_user_params"])
