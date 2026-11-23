"""The taxprofiler 2.0.1 and funcscan 4.0.0 maps against the files those releases write.

There is no run fixture for either release yet (G-7), so these runs are synthetic: the
inventory paths are taken from the releases' own nf-test snapshots
(taxprofiler tests/default.nf.test.snap, test_motus and test_fastpnonpareilkrakenuniq;
funcscan tests/default.nf.test and test_cazyme_pyrodigal), and, for funcscan's annotations,
which no snapshot saves, from conf/modules.config at the tag.
"""
import json
import os
from pathlib import Path

import pytest

from NessieAI.ns.reingest import harvest, mapper, maps

pytestmark = pytest.mark.django_db

ATTRS = json.loads((Path(__file__).resolve().parents[1] / "fixtures"
                    / "sampletype_attributes.json").read_text())["sample_types"]
LOCAL = {"has_attribute": lambda st, a: a in ATTRS.get(st, ()),
         "next_name_ordinal": lambda sample_type, prefix: 1}


def _fq(name, read=1):
    return f"/net/fq/{name}_R{read}.fastq.gz"


TAX_SHEET = ("sample,run_accession,instrument_platform,fastq_1,fastq_2,fasta\n"
             f"S2611,S2611,ILLUMINA,{_fq('a')},{_fq('a', 2)},\n"
             f"S2612,S2612,ILLUMINA,{_fq('b')},,\n"
             f"ERR3201952,ERR3201952,OXFORD_NANOPORE,{_fq('ont')},,\n")
TAX_UIDS = {_fq("a"): "D.SEQ-1", _fq("b"): "D.SEQ-2", _fq("ont"): "D.SEQ-3"}


def _tax_paths():
    paths = []
    for s in ("S2611", "S2612"):
        paths += [f"kraken2/db2/{s}_db2.kraken2.kraken2.report.txt",
                  f"kraken2/db2/{s}_db2.kraken2.classified.fastq.gz",
                  f"bracken/db1/{s}_db1.bracken.tsv",
                  f"bracken/db1/{s}_db1.bracken.kraken2.report_bracken.txt",
                  f"metaphlan/mpa4/{s}_mpa4.metaphlan_profile.txt",
                  f"metaphlan/mpa4/{s}_mpa4.metaphlan.biom",
                  f"centrifuge/db3/{s}_db3.centrifuge.txt",
                  f"centrifuge/db3/{s}_db3.centrifuge.report.txt",
                  f"kaiju/db5/{s}_db5.kaijutable.txt",
                  f"ganon/db1/{s}_db1.ganon_report.tre",
                  f"kmcp/db1/{s}_db1.kmcp.profile",
                  f"diamond/db1/{s}_db1.diamond.tsv",
                  f"krakenuniq/db6/{s}.se.krakenuniq.report.txt",
                  f"motus/db1/{s}_{s}_db1.out"]
    paths += ["sylph/db1/S2612_db1.sylphmpa", "sylph/db1/S2612_db1.sylph.tsv",
              "sylph/db1/ERR3201952_db1.sylph.tsv",
              "kraken2/kraken2_db2_combined_reports.txt",
              "taxpasta/kraken2_db2.tsv", "taxpasta/bracken_db1.tsv", "taxpasta/metaphlan_mpa4.tsv",
              "multiqc/multiqc_report.html"]
    return paths


def _harvest(root, pipeline, version, sheet, paths, uids, params):
    (root / "pipeline_info").mkdir(parents=True)
    (root / "pipeline_info" / f"nf_core_{pipeline}_software_mqc_versions.yml").write_text(
        f"Workflow:\n  nf-core/{pipeline}: v{version}\n  Nextflow: 25.04.6\n")
    (root / "pipeline_info" / "params_2026-11-19_00-00-00.json").write_text(json.dumps(params))
    (root / "samplesheet.csv").write_text(sheet)
    return harvest.harvest_local(
        str(root), inventory=[{"path": p, "bytes": 1} for p in paths],
        lookup_by_fastq=lambda path, types=None: [uids[path]] if path in uids else [],
        sample_type_lookup=lambda found: {u: "D.SEQ" for u in found})


def _by(result, rule_id):
    return {(r.nfcore_sample or os.path.basename(r.attributes["File_PrimaryData"].source_file)):
            {k: v.value for k, v in r.attributes.items()}
            for r in result.rows if r.rule_id == rule_id}


@pytest.mark.parametrize("key,pin", [("taxprofiler", "2.0.1"), ("funcscan", "4.0.0")])
def test_the_map_covers_the_pinned_release(key, pin):
    m = maps.load(key)
    assert m.covers_revision(pin)
    assert all(rule.optional for rule in m.outputs), "every output depends on a flag"


def test_each_classifier_profile_is_a_taxonomic_profile_of_its_sample(tmp_path):
    run = _harvest(tmp_path, "taxprofiler", "2.0.1", TAX_SHEET, _tax_paths(), TAX_UIDS,
                   {"run_kraken2": True, "run_bracken": True, "perform_runmerging": True})
    result = mapper.apply(run, maps.load("taxprofiler"), context=LOCAL)
    kraken = _by(result, "kraken2")
    assert sorted(kraken) == ["S2611", "S2612"]
    assert kraken["S2611"]["Parent"] == "D.SEQ-1"
    assert kraken["S2611"]["Classifier"] == "Kraken2"
    assert kraken["S2611"]["File_PrimaryData"] == "S2611_db2.kraken2.kraken2.report.txt"
    assert kraken["S2612"]["Pipeline"] == "nf-core/taxprofiler"
    bracken = _by(result, "bracken")["S2612"]
    assert bracken["File_PrimaryData"] == "S2612_db1.bracken.tsv"
    assert bracken["File_SecondaryData"] == "S2612_db1.bracken.kraken2.report_bracken.txt"
    for rule_id, sample in [("metaphlan", "S2611"), ("centrifuge", "S2611"), ("kaiju", "S2612"),
                            ("ganon", "S2611"), ("kmcp", "S2612"), ("diamond", "S2611"),
                            ("krakenuniq", "S2611"), ("motus", "S2612")]:
        row = _by(result, rule_id)[sample]
        assert row["Parent"] == TAX_UIDS[_fq("a" if sample == "S2611" else "b")], rule_id
    # sylph: the taxonomic profile wins; a long-read sample with only the genome table
    # still gets its row (the second candidate).
    sylph = _by(result, "sylph")
    assert sylph["S2612"]["File_PrimaryData"] == "S2612_db1.sylphmpa"
    assert sylph["ERR3201952"]["File_PrimaryData"] == "ERR3201952_db1.sylph.tsv"
    # A classifier that did not run (no files) gives no rows and no complaint.
    assert not [w for w in result.warnings if "kraken2" in w and "matched nothing" in w]


def test_each_taxpasta_table_is_one_run_level_row_parented_by_every_sample(tmp_path):
    run = _harvest(tmp_path, "taxprofiler", "2.0.1", TAX_SHEET, _tax_paths(), TAX_UIDS,
                   {"run_profile_standardisation": True})
    rows = _by(mapper.apply(run, maps.load("taxprofiler"), context=LOCAL), "taxpasta")
    assert sorted(rows) == ["bracken_db1.tsv", "kraken2_db2.tsv", "metaphlan_mpa4.tsv"]
    assert rows["kraken2_db2.tsv"]["Parent"] == "D.SEQ-1;D.SEQ-2;D.SEQ-3"
    assert rows["kraken2_db2.tsv"]["Software"] == "TAXPASTA"


FUN_SHEET = "sample,fasta\nasm_1,/net/asm/asm_1.fna.gz\nasm_2,/net/asm/asm_2.fna.gz\n"


def _fun_paths(tool="pyrodigal"):
    paths = ["reports/ampcombi2/Ampcombi_summary.tsv", "reports/ampcombi2/Ampcombi_summary_cluster.tsv",
             "reports/ampcombi2/Ampcombi_summary_cluster_representative_seq.tsv",
             "reports/hamronization_summarize/hamronization_combined_report.tsv",
             "reports/combgc/combgc_complete_summary.tsv",
             "cazyme/dbcan/cazyme_annotation/asm_2/asm_2_overview.tsv",
             "multiqc/multiqc_report.html"]
    for s in ("asm_1", "asm_2"):
        if tool == "pyrodigal":
            paths += [f"annotation/pyrodigal/all/{s}_pyrodigal.gbk.gz",
                      f"annotation/pyrodigal/all/{s}_pyrodigal.faa.gz",
                      f"annotation/pyrodigal/long/{s}_pyrodigal.gbk.gz"]
        else:
            paths += [f"annotation/prokka/all/{s}_prokka/{s}_prokka.gff",
                      f"annotation/prokka/all/{s}_prokka/{s}_prokka.faa"]
    return paths


@pytest.mark.parametrize("tool,name,datatype", [
    ("pyrodigal", "asm_1_pyrodigal.gbk.gz", "GBK"),
    ("prokka", "asm_1_prokka.gff", "GFF3"),
])
def test_saved_annotations_are_genome_annotations_of_their_assembly(tmp_path, tool, name, datatype):
    run = _harvest(tmp_path, "funcscan", "4.0.0", FUN_SHEET, _fun_paths(tool), {},
                   {"save_annotations": True, "annotation_tool": tool})
    result = mapper.apply(run, maps.load("funcscan"), context=LOCAL)
    rows = _by(result, f"annotation_{tool}")
    assert sorted(rows) == ["asm_1", "asm_2"]
    assert rows["asm_1"]["File_PrimaryData"] == name
    assert rows["asm_1"]["DataType"] == datatype
    # Only the whole assembly's annotation: the BGC screen's long-contig subset is not a row.
    assert all("/long/" not in str(r.attributes["File_PrimaryData"].source_file) for r in result.rows)


def test_each_screen_summary_is_a_functional_screening_row(tmp_path):
    run = _harvest(tmp_path, "funcscan", "4.0.0", FUN_SHEET, _fun_paths(), {},
                   {"run_amp_screening": True, "run_arg_screening": True,
                    "run_bgc_screening": True, "run_cazyme_screening": True})
    result = mapper.apply(run, maps.load("funcscan"), context=LOCAL)
    cats = {r.rule_id: r.attributes["ScreeningCategory"].value for r in result.rows
            if r.rule_id in ("amp_summary", "arg_summary", "bgc_summary", "cazyme")}
    assert cats == {"amp_summary": "AMP", "arg_summary": "ARG", "bgc_summary": "BGC", "cazyme": "CAZyme"}
    amp = _by(result, "amp_summary")["Ampcombi_summary.tsv"]
    assert amp["File_SecondaryData"] in ("Ampcombi_summary_cluster.tsv",
                                         "Ampcombi_summary_cluster_representative_seq.tsv")
    assert sorted(_by(result, "cazyme")) == ["asm_2"]
