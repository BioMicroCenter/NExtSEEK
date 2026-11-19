"""The proteinfold 2.0.0 and pangenome 1.1.3 maps against hand-built manifests: whose rows they
write and which file lands where. proteinfold's file rules are also checked against its nf-test
snapshot fixtures (test_fixture_runs.py); pangenome's only against its pinned output docs
(test_doc_only_maps.py), so this is the one test that runs its rule."""
from NessieAI.ns.reingest import harvest, manifest, mapper, maps


def _run(pipeline, version, samples, paths):
    names = [s.nfcore_sample for s in samples]
    return manifest.RunManifest(
        run_dir="/net/cluster/runs/r", params={"n_haplotypes": 12},
        software_versions={}, pipeline=manifest.PipelineInfo(name=f"nf-core/{pipeline}", version=version),
        samples=samples,
        outputs=[manifest.OutputRecord(path=p, bytes=10, sample=harvest._sample_for_output_path(p, names))
                 for p in paths])


def _sample(name, uid, parent_type):
    return manifest.SampleRecord(nfcore_sample=name, d_seq_uid=uid,
                                 uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD,
                                 parent_sample_type=parent_type)


def test_the_pangenome_graph_is_one_a_pang_row_parented_by_the_combined_assembly():
    stem = "ecoli_12.fa.gz"
    run = _run("pangenome", "1.1.3", [_sample("A.ASM-1", "A.ASM-1", "A.ASM")], [
        f"FINAL_GFA/{stem}.gfaffix.unchop.Ygs.view.gfa",
        f"FINAL_ODGI/{stem}.gfaffix.unchop.Ygs.og",
        f"odgi_stats/{stem}.gfaffix.og.stats.yaml",
        f"odgi_stats/{stem}.seqwish.og.stats.yaml",
    ])
    result = mapper.apply(run, maps.load("pangenome"))
    (row,) = [r for r in result.rows if r.sample_type == "A.PANG"]
    # A per_run file keeps its run folder in the name (two runs' graphs share a file name).
    assert row.attributes["File_PrimaryData"].value.endswith(f"/{stem}.gfaffix.unchop.Ygs.view.gfa")
    assert row.attributes["File_SecondaryData"].value.endswith(f"{stem}.gfaffix.unchop.Ygs.og")
    assert row.attributes["Parent"].value == "A.ASM-1"
    assert row.attributes["GenomeCount"].value == 12
    assert "stats.yaml" in row.attributes["Notes"].value


def test_with_communities_the_squeezed_graph_wins_over_each_communitys():
    stem = "plants.fa.gz"
    run = _run("pangenome", "1.1.3", [_sample("A.ASM-2", "A.ASM-2", "A.ASM")], [
        f"FINAL_GFA/{stem}.squeeze.unchop.Ygs.view.gfa",
        f"FINAL_GFA/{stem}.community.0.gfaffix.unchop.Ygs.view.gfa",
        f"FINAL_ODGI/{stem}.squeeze.og",
    ])
    (row,) = [r for r in mapper.apply(run, maps.load("pangenome")).rows if r.sample_type == "A.PANG"]
    assert row.attributes["File_PrimaryData"].value.endswith(f"/{stem}.squeeze.unchop.Ygs.view.gfa")


def test_each_protein_gets_its_own_model_row_per_mode_parented_by_its_d_pseq():
    samples = [_sample("D.PSEQ-1", "D.PSEQ-1", "D.PSEQ"), _sample("D.PSEQ-10", "D.PSEQ-10", "D.PSEQ")]
    run = _run("proteinfold", "2.0.0", samples, [
        "esmfold/top_ranked_structures/D.PSEQ-1.pdb", "esmfold/D.PSEQ-1/D.PSEQ-1_plddt.tsv",
        "esmfold/top_ranked_structures/D.PSEQ-10.pdb", "esmfold/D.PSEQ-10/D.PSEQ-10_plddt.tsv",
        "colabfold/top_ranked_structures/D.PSEQ-1.pdb", "colabfold/D.PSEQ-1/D.PSEQ-1_plddt.tsv",
        "colabfold/D.PSEQ-1/D.PSEQ-1_ptm.tsv",
    ])
    result = mapper.apply(run, maps.load("proteinfold"))
    rows = {(r.rule_id, r.nfcore_sample): r for r in result.rows if r.sample_type == "A.PSTR"}
    # colabfold ran for one sample only: the other gets no fileless row.
    assert set(rows) == {("esmfold", "D.PSEQ-1"), ("esmfold", "D.PSEQ-10"), ("colabfold", "D.PSEQ-1")}
    ten = rows[("esmfold", "D.PSEQ-10")]
    assert ten.attributes["File_PrimaryData"].value == "D.PSEQ-10.pdb"
    assert ten.attributes["File_SecondaryData"].value == "D.PSEQ-10_plddt.tsv"
    assert ten.attributes["Parent"].value == "D.PSEQ-10"
    assert ten.attributes["PredictionMethod"].value == "ESMFold"
    assert rows[("colabfold", "D.PSEQ-1")].attributes["PredictionMethod"].value == "ColabFold"
