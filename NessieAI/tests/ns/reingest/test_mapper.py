from NessieAI.ns.reingest import manifest, mapper, maps


def _run(metrics=None, derived=None, params=None, software_versions=None):
    return manifest.RunManifest(
        run_dir="/net/cluster/runs/r",
        params=params or {"genome": "GRCm39", "aligner": "star_salmon"},
        software_versions=software_versions or {},
        pipeline=manifest.PipelineInfo(name="nf-core/rnaseq", version="3.18.0"),
        samples=[manifest.SampleRecord(
            nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-EXAMPLE-1",
            uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD,
            metrics=metrics or {}, derived=derived or {})],
        sources={"metrics": "multiqc/star_salmon/multiqc_data/multiqc_general_stats.txt"})


def test_a_committed_rule_maps_and_is_marked_origin_map():
    result = mapper.apply(_run(metrics={"star-uniquely_mapped_percent": 91.4}),
                          maps.load("rnaseq"))
    row = next(r for r in result.rows if r.sample_type == "D.SEQ")
    assert row.attributes["MappedPercent"].value == 91.4
    assert row.attributes["MappedPercent"].origin == mapper.ORIGIN_MAP
    assert row.attributes["MappedPercent"].raw_key == "star-uniquely_mapped_percent"


def test_the_alternate_is_used_when_the_primary_key_is_absent():
    result = mapper.apply(_run(metrics={"samtools_stats-reads_mapped_percent": 88.0}),
                          maps.load("rnaseq"))
    row = next(r for r in result.rows if r.sample_type == "D.SEQ")
    assert row.attributes["MappedPercent"].value == 88.0
    assert row.attributes["MappedPercent"].raw_key == "samtools_stats-reads_mapped_percent"


def test_no_source_at_all_yields_no_attribute_rather_than_a_zero():
    result = mapper.apply(_run(metrics={}), maps.load("rnaseq"))
    row = next(r for r in result.rows if r.sample_type == "D.SEQ")
    assert "MappedPercent" not in row.attributes


def test_an_unknown_metric_key_lands_in_unmapped_with_its_evidence():
    result = mapper.apply(_run(metrics={"Kraken2_bracken_fraction": 3.2}),
                          maps.load("rnaseq"))
    entry = next(u for u in result.unmapped if u["raw_key"] == "Kraken2_bracken_fraction")
    assert entry["example_value"] == 3.2
    assert entry["source_file"].endswith("multiqc_general_stats.txt")


def test_a_deliberately_unmapped_key_is_never_proposed():
    pipeline_map = maps.load("rnaseq")
    assert "Picard_PERCENT_DUPLICATION" in pipeline_map.ruled_out()
    result = mapper.apply(
        _run(metrics={"Picard_PERCENT_DUPLICATION": 18.4,
                      "Some_Definitely_Unknown_Metric_XYZ": 1.0}),
        pipeline_map)
    # The ruling specifically excludes its own key...
    assert not any(u["raw_key"] == "Picard_PERCENT_DUPLICATION" for u in result.unmapped)
    # ...but unmapped is genuinely populated: a second, genuinely unknown key
    # on the same sample still surfaces as evidence.
    assert any(u["raw_key"] == "Some_Definitely_Unknown_Metric_XYZ" for u in result.unmapped)


def test_an_approved_rule_applies_and_is_marked_origin_approved():
    # approved_rules is keyed BY attribute, so the rule carries only its source.
    # The map's own ContamPercent rule reads "kraken2-pct_unclassified", which is
    # absent here, so the committed rule finds nothing and the approved rule applies.
    approved = {"ContamPercent": maps.AttributeRule(
        **{"from": "Kraken2_bracken_fraction", "target": "D.SEQ",
           "datatype": "number", "provenance": "approved 2026-09-15 by tester"})}
    result = mapper.apply(_run(metrics={"Kraken2_bracken_fraction": 3.2}),
                          maps.load("rnaseq"), approved_rules=approved)
    row = next(r for r in result.rows if r.sample_type == "D.SEQ")
    assert row.attributes["ContamPercent"].origin == mapper.ORIGIN_APPROVED
    assert not any(u["raw_key"] == "Kraken2_bracken_fraction" for u in result.unmapped)


def test_provenance_attributes_are_applied_to_the_analysis_rows():
    result = mapper.apply(_run(), maps.load("rnaseq"))
    analysis = [r for r in result.rows if r.sample_type.startswith("A.")]
    assert analysis, "expected at least one analysis row"
    assert analysis[0].attributes["ReferenceGenome"].value == "GRCm39"


def test_a_multirun_sample_gets_no_d_seq_row():
    run = _run(metrics={"star-uniquely_mapped_percent": 91.4,
                        "Kraken2_bracken_fraction": 3.2})
    run.samples[0].uid_resolution = manifest.RESOLUTION_MULTIRUN
    run.samples[0].d_seq_uid = None
    result = mapper.apply(run, maps.load("rnaseq"))
    # No D.SEQ row for the multirun sample...
    assert not any(r.sample_type == "D.SEQ" for r in result.rows)
    # ...but the run's analysis/output rows are unaffected (they come from
    # pipeline_map.outputs, not from any one sample's resolution)...
    assert any(r.sample_type.startswith("A.") for r in result.rows)
    # ...and the sample's own metrics are still walked: an unknown key on it
    # still reaches unmapped rather than being silently swallowed along with
    # the D.SEQ row.
    assert any(u["raw_key"] == "Kraken2_bracken_fraction" for u in result.unmapped)


# --- Resolution 2: an output rule's own attribute must win over a
# provenance attribute of the same name (A.ALN's "Software" is the STAR
# version string; provenance_attributes' "Software" is the whole
# software_versions dict, which must not clobber it). ---

def test_an_output_rules_own_attribute_wins_over_a_same_named_provenance_attribute():
    run = _run(software_versions={"STAR": "2.7.11a"})
    result = mapper.apply(run, maps.load("rnaseq"))
    aln = next(r for r in result.rows if r.sample_type == "A.ALN")
    assert aln.attributes["Software"].value == "2.7.11a"
    assert isinstance(aln.attributes["Software"].value, str)


# --- Resolution 3: when a committed map rule and an approved rule both
# produce a value for the same attribute, the committed map rule must win. ---

def test_a_committed_rule_wins_over_a_contested_approved_rule():
    approved = {"ContamPercent": maps.AttributeRule(
        **{"from": "Kraken2_bracken_fraction", "target": "D.SEQ",
           "datatype": "number", "provenance": "approved 2026-09-15 by tester"})}
    result = mapper.apply(
        _run(metrics={"kraken2-pct_unclassified": 4.5, "Kraken2_bracken_fraction": 3.2}),
        maps.load("rnaseq"), approved_rules=approved)
    row = next(r for r in result.rows if r.sample_type == "D.SEQ")
    assert row.attributes["ContamPercent"].value == 4.5
    assert row.attributes["ContamPercent"].origin == mapper.ORIGIN_MAP


# --- The "raw_key in claimed or raw_key in ruled_out" skip is meant to be
# order-independent: either reason alone must be enough to keep a key out of
# unmapped. Nothing demonstrated that until now. ---

def test_a_ruled_out_key_claimed_by_an_approved_rule_still_stays_out_of_unmapped():
    # "Picard_PERCENT_DUPLICATION" is a real deliberately_unmapped key in the
    # committed rnaseq map. No committed rule claims it as a "from" or
    # alternate, so we give it to an approved rule instead -- a realistic way
    # a key ends up BOTH ruled_out (committed map) AND claimed (approved
    # rule) at once.
    pipeline_map = maps.load("rnaseq")
    assert "Picard_PERCENT_DUPLICATION" in pipeline_map.ruled_out()
    approved = {"PercentDuplication": maps.AttributeRule(
        **{"from": "Picard_PERCENT_DUPLICATION", "target": "D.SEQ",
           "datatype": "number", "provenance": "approved 2026-09-15 by tester"})}
    result = mapper.apply(
        _run(metrics={"Picard_PERCENT_DUPLICATION": 18.4}),
        pipeline_map, approved_rules=approved)
    assert not any(u["raw_key"] == "Picard_PERCENT_DUPLICATION" for u in result.unmapped)
    # And the approved rule genuinely fired -- this isn't passing merely
    # because the key was skipped for an unrelated reason.
    row = next(r for r in result.rows if r.sample_type == "D.SEQ")
    assert row.attributes["PercentDuplication"].value == 18.4
    assert row.attributes["PercentDuplication"].origin == mapper.ORIGIN_APPROVED


def test_a_ruled_out_key_on_two_different_samples_stays_out_of_unmapped():
    # "MaxReads" is a real deliberately_unmapped key in the committed rnaseq
    # map. Put it on two different samples in the same run and confirm
    # neither occurrence is proposed -- the per-sample dedup-by-raw_key guard
    # must not be the only thing keeping it out.
    pipeline_map = maps.load("rnaseq")
    assert "MaxReads" in pipeline_map.ruled_out()
    run = manifest.RunManifest(
        run_dir="/net/cluster/runs/r",
        params={"genome": "GRCm39", "aligner": "star_salmon"},
        pipeline=manifest.PipelineInfo(name="nf-core/rnaseq", version="3.18.0"),
        samples=[
            manifest.SampleRecord(
                nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-EXAMPLE-1",
                uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD,
                metrics={"MaxReads": 40000000.0}),
            manifest.SampleRecord(
                nfcore_sample="CONTROL_REP2", d_seq_uid="D.SEQ-EXAMPLE-2",
                uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD,
                metrics={"MaxReads": 40000000.0}),
        ],
        sources={"metrics": "multiqc/star_salmon/multiqc_data/multiqc_general_stats.txt"})
    result = mapper.apply(run, pipeline_map)
    assert not any(u["raw_key"] == "MaxReads" for u in result.unmapped)
