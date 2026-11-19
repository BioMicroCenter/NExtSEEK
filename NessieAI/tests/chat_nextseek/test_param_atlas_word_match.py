"""param_atlas `match: word`: markers that must match whole words, not substrings.

A substring marker cannot tell "male" from "female" or "affected" from "unaffected": the
first hides inside the second, so every female sample would vote both ways. nf-core/raredisease's
pedigree columns (sex 1/2, phenotype 1/2) are read from exactly such words, so a spec may say
`"match": "word"`: then each marker (primary, corroborators and ambiguous markers alike) matches
only where it stands as a whole word, case-insensitively.
"""
import json

import pytest

from chat_nextseek.seqera.param_atlas import (
    ParamAtlasError,
    evaluate_data_driven_param,
    load_param_atlas,
)


def _spec(**over):
    spec = {
        "target": "row_column",
        "scope": "sample",
        "allowed": ["1", "2"],
        "match": "word",
        "derive_from": {"signal": "Sex", "map": {"1": ["male", "m"], "2": ["female", "f"]}},
        "corroborate_with": [{"signal": "Name", "1_markers": ["male"], "2_markers": ["female"]}],
        "ask": {"definition": "d", "on_conflict": "c", "on_absent": "a"},
    }
    spec.update(over)
    return spec


def _write(tmp_path, spec):
    path = tmp_path / "param_atlas.json"
    path.write_text(json.dumps({"guidance": {"notes": ["x"]},
                                "pipelines": {"sarek": {"params": {"sex": spec}}}}))
    return path


@pytest.mark.parametrize("value,expected", [
    ("Female", "2"), ("female", "2"), ("F", "2"),
    ("Male", "1"), ("M", "1"), ("male (XY)", "1"),
])
def test_a_word_marker_keeps_male_out_of_female(value, expected):
    out = evaluate_data_driven_param(_spec(), {"Sex": value})
    assert out["verdict"] == "derived_uncorroborated" and out["value"] == expected


def test_a_word_marker_does_not_match_inside_a_longer_word():
    out = evaluate_data_driven_param(_spec(), {"Sex": "Mfemale-ish"})
    assert out["verdict"] == "absent"


def test_corroborators_match_words_too():
    out = evaluate_data_driven_param(_spec(), {"Sex": "F", "Name": "female donor 3"})
    assert out["verdict"] == "corroborated" and out["value"] == "2"
    out = evaluate_data_driven_param(_spec(), {"Sex": "F", "Name": "male donor 3"})
    assert out["verdict"] == "conflict"


def test_ambiguous_markers_match_words_too():
    spec = _spec(allowed=["2", "1"], derive_from={"signal": "Phenotype", "map": {
        "2": ["affected"], "1": ["unaffected"]}}, corroborate_with=[],
        ambiguous_markers=["carrier"])
    assert evaluate_data_driven_param(spec, {"Phenotype": "Unaffected"})["value"] == "1"
    assert evaluate_data_driven_param(spec, {"Phenotype": "Affected"})["value"] == "2"
    assert evaluate_data_driven_param(spec, {"Phenotype": "carrier, unaffected"})["verdict"] == "conflict"
    # "carriers" is not the word "carrier": no ambiguity, the plain marker decides.
    assert evaluate_data_driven_param(spec, {"Phenotype": "unaffected; parents carriers"})["value"] == "1"


def test_without_match_the_substring_hazard_is_unchanged():
    spec = _spec()
    del spec["match"]
    assert evaluate_data_driven_param(spec, {"Sex": "Female"})["verdict"] == "conflict"


def test_a_word_spec_loads_without_a_collision_warning(tmp_path, recwarn):
    load_param_atlas(_write(tmp_path, _spec()))
    assert not [w for w in recwarn.list if "substring" in str(w.message)]


def test_a_substring_spec_still_warns_on_the_same_markers(tmp_path):
    spec = _spec()
    del spec["match"]
    with pytest.warns(UserWarning, match="substring"):
        load_param_atlas(_write(tmp_path, spec))


def test_a_word_spec_still_warns_when_one_marker_is_a_whole_word_of_another(tmp_path):
    spec = _spec(derive_from={"signal": "Sex", "map": {"1": ["male"], "2": ["not male"]}},
                 corroborate_with=[])
    with pytest.warns(UserWarning, match="substring"):
        load_param_atlas(_write(tmp_path, spec))


def test_match_must_be_word_or_substring(tmp_path):
    with pytest.raises(ParamAtlasError, match="match"):
        load_param_atlas(_write(tmp_path, _spec(match="regex")))
