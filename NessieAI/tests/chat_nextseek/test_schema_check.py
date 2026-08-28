"""Cross-check the final run params against the pipeline's own pinned schema."""
from chat_nextseek.seqera.nfcore_schema import SchemaFetchError
from chat_nextseek.seqera.schema_check import (
    check_params,
    check_reference_flags,
    schema_properties,
)

DEFS_SCHEMA = {
    "$defs": {
        "input_output_options": {"properties": {"input": {"type": "string"},
                                                "outdir": {"type": "string"}}},
        "alignment": {"properties": {
            "aligner": {"type": "string", "enum": ["star_salmon", "star_rsem", "hisat2"]},
            "skip_dupradar": {"type": "boolean"},
            "min_trimmed_reads": {"type": "integer"},
            "extra_fdr": {"type": "number"},
        }},
        "reference_genome_options": {"properties": {"genome": {"type": "string"},
                                                    "fasta": {"type": "string"},
                                                    "gtf": {"type": "string"}}},
    }
}

DEFINITIONS_SCHEMA = {
    "definitions": {"g": {"properties": {"protocol": {"type": "string"}}}}
}

TOP_LEVEL_SCHEMA = {"properties": {"loose": {"type": "string"}}}


def _getter(schema=DEFS_SCHEMA, fail=False):
    def get(pipeline, revision):
        if fail:
            raise SchemaFetchError(f"{pipeline}@{revision}: read timeout")
        return {"nextflow_schema": schema}
    return get


# ---- flattening -----------------------------------------------------------

def test_flattens_defs_groups():
    props = schema_properties(DEFS_SCHEMA)
    assert "aligner" in props and "genome" in props


def test_flattens_definitions_groups():
    assert "protocol" in schema_properties(DEFINITIONS_SCHEMA)


def test_reads_top_level_properties_too():
    assert "loose" in schema_properties(TOP_LEVEL_SCHEMA)


def test_an_empty_schema_flattens_to_nothing():
    assert schema_properties({}) == {}


# ---- existence ------------------------------------------------------------

def test_a_known_param_passes():
    errors, skip = check_params("rnaseq", "3.18.0", {"aligner": "star_salmon"},
                                schema_getter=_getter())
    assert (errors, skip) == ([], None)


def test_a_param_absent_from_the_schema_is_an_error():
    errors, skip = check_params("rnaseq", "3.18.0", {"skip_duprader": True},
                                schema_getter=_getter())
    assert skip is None
    assert len(errors) == 1
    assert "skip_duprader" in errors[0]
    assert "3.18.0" in errors[0]


# ---- values ---------------------------------------------------------------

def test_a_bad_enum_value_is_an_error():
    errors, _ = check_params("rnaseq", "3.18.0", {"aligner": "bowtie2"},
                             schema_getter=_getter())
    assert "bowtie2" in errors[0]
    assert "star_salmon" in errors[0]


def test_a_string_where_a_boolean_belongs_is_an_error():
    errors, _ = check_params("rnaseq", "3.18.0", {"skip_dupradar": "true"},
                             schema_getter=_getter())
    assert "skip_dupradar" in errors[0]


def test_a_boolean_is_not_accepted_as_an_integer():
    errors, _ = check_params("rnaseq", "3.18.0", {"min_trimmed_reads": True},
                             schema_getter=_getter())
    assert "min_trimmed_reads" in errors[0]


def test_an_integer_is_accepted_where_a_number_belongs():
    errors, _ = check_params("rnaseq", "3.18.0", {"extra_fdr": 1}, schema_getter=_getter())
    assert errors == []


def test_a_boolean_is_not_accepted_as_a_number():
    errors, _ = check_params("rnaseq", "3.18.0", {"extra_fdr": True},
                             schema_getter=_getter())
    assert "extra_fdr" in errors[0]


def test_a_list_type_accepts_any_matching_value():
    """List-valued type declarations occur in real schemas: help is
    ["boolean", "string"] in hlatyping and smrnaseq."""
    schema = {
        "$defs": {"test": {"properties": {
            "help": {"type": ["boolean", "string"]}
        }}}
    }
    # Bool should pass
    errors, _ = check_params("test", "1.0", {"help": True}, schema_getter=lambda p, r: {"nextflow_schema": schema})
    assert errors == []
    # String should pass
    errors, _ = check_params("test", "1.0", {"help": "some text"}, schema_getter=lambda p, r: {"nextflow_schema": schema})
    assert errors == []
    # Int should fail
    errors, _ = check_params("test", "1.0", {"help": 42}, schema_getter=lambda p, r: {"nextflow_schema": schema})
    assert len(errors) == 1
    assert "help" in errors[0]


def test_null_in_type_list_is_dropped():
    """List-type declarations include "null" in real schemas: dfam_version
    is ["number", "null"] in rnafusion. None values are already skipped,
    so "null" adds no information and would make the type unjudgeable."""
    schema = {
        "$defs": {"test": {"properties": {
            "version": {"type": ["number", "null"]}
        }}}
    }
    # Number should pass
    errors, _ = check_params("test", "1.0", {"version": 1.5}, schema_getter=lambda p, r: {"nextflow_schema": schema})
    assert errors == []
    # String should fail (not "number" or "null")
    errors, _ = check_params("test", "1.0", {"version": "1.5"}, schema_getter=lambda p, r: {"nextflow_schema": schema})
    assert len(errors) == 1
    assert "version" in errors[0]


def test_unrecognized_type_spelling_passes_through():
    """If a type in the schema is not in _TYPE_CHECKS, we cannot judge and
    must not guess. Rejecting on a subset of recognisable types could
    falsely reject a value that is legal under an unrecognised type."""
    schema = {
        "$defs": {"test": {"properties": {
            "custom": {"type": "custom_type"}
        }}}
    }
    # Should silently pass even though "custom_type" is not recognised
    errors, _ = check_params("test", "1.0", {"custom": "anything"}, schema_getter=lambda p, r: {"nextflow_schema": schema})
    assert errors == []


def test_a_none_value_is_not_type_checked():
    errors, _ = check_params("rnaseq", "3.18.0", {"gtf": None}, schema_getter=_getter())
    assert errors == []


def test_every_bad_param_is_reported_not_just_the_first():
    errors, _ = check_params("rnaseq", "3.18.0",
                             {"aligner": "bowtie2", "nonesuch": 1, "skip_dupradar": "x"},
                             schema_getter=_getter())
    assert len(errors) == 3


# ---- degradation ----------------------------------------------------------

def test_an_unfetchable_schema_skips_rather_than_failing():
    errors, skip = check_params("rnaseq", "3.18.0", {"nonesuch": 1},
                                schema_getter=_getter(fail=True))
    assert errors == []
    assert "read timeout" in skip


def test_an_unexpected_exception_also_skips():
    def explode(pipeline, revision):
        raise OSError("no route to host")
    errors, skip = check_params("rnaseq", "3.18.0", {"nonesuch": 1}, schema_getter=explode)
    assert errors == []
    assert "no route to host" in skip


def test_no_revision_skips_with_a_reason():
    errors, skip = check_params("mystery", None, {"a": 1}, schema_getter=_getter())
    assert errors == []
    assert "revision" in skip


def test_a_schema_with_no_properties_skips_rather_than_rejecting_everything():
    """An empty flatten means we failed to read the schema, not that the
    pipeline has no parameters. Rejecting every param would be catastrophic."""
    errors, skip = check_params("rnaseq", "3.18.0", {"aligner": "star_salmon"},
                                schema_getter=lambda p, r: {"nextflow_schema": {}})
    assert errors == []
    assert skip is not None


# ---- reference flags ------------------------------------------------------

def test_declared_reference_flags_that_the_schema_lacks_are_reported(monkeypatch):
    from chat_nextseek.seqera import schema_check

    monkeypatch.setitem(schema_check.NFCORE_PIPELINE_CATALOG, "toy",
                        {"default_revision": "1.0.0", "reference_cli_flags": ["genome", "gff"]})
    warnings = check_reference_flags("toy", "1.0.0", schema_getter=_getter())
    assert len(warnings) == 1
    assert "gff" in warnings[0]


def test_reference_flags_all_declared_is_silent(monkeypatch):
    from chat_nextseek.seqera import schema_check

    monkeypatch.setitem(schema_check.NFCORE_PIPELINE_CATALOG, "toy",
                        {"default_revision": "1.0.0",
                         "reference_cli_flags": ["genome", "fasta", "gtf"]})
    assert check_reference_flags("toy", "1.0.0", schema_getter=_getter()) == []


def test_reference_flags_are_silent_when_the_schema_is_unreachable(monkeypatch):
    from chat_nextseek.seqera import schema_check

    monkeypatch.setitem(schema_check.NFCORE_PIPELINE_CATALOG, "toy",
                        {"default_revision": "1.0.0", "reference_cli_flags": ["gff"]})
    assert check_reference_flags("toy", "1.0.0", schema_getter=_getter(fail=True)) == []
