import csv
from pathlib import Path
from chat_nextseek.luria.fetchngs_helpers import (
    _main_cached, _main_fill, fill_rows, needs_fetch_accessions, uncached_accessions,
)


def test_needs_fetch_accessions_selects_blank_fastq_srr_rows():
    rows = [
        {"sample": "a", "accession": "SRR1", "fastq_1": ""},
        {"sample": "b", "accession": "SRR2", "fastq_1": "/net/bmc/b.fastq.gz"},  # already local
        {"sample": "c", "accession": "", "fastq_1": ""},                          # no accession
        {"sample": "d", "accession": "SRR1", "fastq_1": ""},                      # dup
    ]
    assert needs_fetch_accessions(rows) == ["SRR1"]


def test_needs_fetch_accessions_rejects_non_bare_ids():
    assert needs_fetch_accessions([{"accession": "../evil", "fastq_1": ""}]) == []


def test_fill_rows_paired(tmp_path):
    (tmp_path / "fastq").mkdir()
    (tmp_path / "fastq" / "SRR1_1.fastq.gz").write_text("x")
    (tmp_path / "fastq" / "SRR1_2.fastq.gz").write_text("x")
    rows, missing = fill_rows([{"sample": "a", "accession": "SRR1", "fastq_1": "", "fastq_2": ""}], str(tmp_path))
    assert missing == []
    assert rows[0]["fastq_1"].endswith("SRR1_1.fastq.gz")
    assert rows[0]["fastq_2"].endswith("SRR1_2.fastq.gz")


def test_fill_rows_single_end(tmp_path):
    (tmp_path / "fastq").mkdir()
    (tmp_path / "fastq" / "SRR9.fastq.gz").write_text("x")
    rows, missing = fill_rows([{"sample": "a", "accession": "SRR9", "fastq_1": "", "fastq_2": ""}], str(tmp_path))
    assert missing == [] and rows[0]["fastq_1"].endswith("SRR9.fastq.gz") and rows[0]["fastq_2"] == ""


def test_fill_rows_reports_missing(tmp_path):
    (tmp_path / "fastq").mkdir()
    rows, missing = fill_rows([{"sample": "a", "accession": "SRRX", "fastq_1": ""}], str(tmp_path))
    assert missing == ["SRRX"]


def test_fill_rows_leaves_local_rows_untouched(tmp_path):
    rows, missing = fill_rows([{"sample": "a", "fastq_1": "/net/bmc/a.fastq.gz"}], str(tmp_path))
    assert missing == [] and rows[0]["fastq_1"] == "/net/bmc/a.fastq.gz"


def test_fill_rows_matches_experiment_prefixed_fetchngs_names(tmp_path):
    # nf-core/fetchngs writes <experiment>_<run>_1.fastq.gz, NOT <run>_1.fastq.gz —
    # confirmed on Luria (SRX6818190_SRR10085181_1.fastq.gz).
    (tmp_path / "fastq").mkdir()
    (tmp_path / "fastq" / "SRX6818190_SRR10085181_1.fastq.gz").write_text("x")
    (tmp_path / "fastq" / "SRX6818190_SRR10085181_2.fastq.gz").write_text("x")
    rows, missing = fill_rows(
        [{"sample": "a", "accession": "SRR10085181", "fastq_1": "", "fastq_2": ""}], str(tmp_path))
    assert missing == []
    assert rows[0]["fastq_1"].endswith("SRX6818190_SRR10085181_1.fastq.gz")
    assert rows[0]["fastq_2"].endswith("SRX6818190_SRR10085181_2.fastq.gz")


def test_fill_rows_experiment_prefixed_single_end(tmp_path):
    (tmp_path / "fastq").mkdir()
    (tmp_path / "fastq" / "SRX999_SRR8.fastq.gz").write_text("x")
    rows, missing = fill_rows(
        [{"sample": "a", "accession": "SRR8", "fastq_1": "", "fastq_2": ""}], str(tmp_path))
    assert missing == []
    assert rows[0]["fastq_1"].endswith("SRX999_SRR8.fastq.gz") and rows[0]["fastq_2"] == ""


# --- experiment accessions (SRX/ERX/DRX): the accession is the PREFIX of fetchngs' name ----

def _cache(tmp_path, *names):
    (tmp_path / "fastq").mkdir()
    for name in names:
        (tmp_path / "fastq" / name).write_text("x")
    return str(tmp_path)


def test_fill_rows_experiment_accession_paired(tmp_path):
    # Luria run rnavar-mda-mb-231: the sheet carried SRX ids and fetchngs wrote <SRX>_<SRR>_{1,2}.
    cache = _cache(tmp_path, "SRX25141024_SRR29635281_1.fastq.gz", "SRX25141024_SRR29635281_2.fastq.gz")
    rows, missing = fill_rows(
        [{"sample": "a", "accession": "SRX25141024", "fastq_1": "", "fastq_2": ""}], cache)
    assert missing == []
    assert len(rows) == 1
    assert rows[0]["fastq_1"].endswith("SRX25141024_SRR29635281_1.fastq.gz")
    assert rows[0]["fastq_2"].endswith("SRX25141024_SRR29635281_2.fastq.gz")


def test_fill_rows_experiment_accession_single_end(tmp_path):
    cache = _cache(tmp_path, "SRX25141026_SRR29635279.fastq.gz")
    rows, missing = fill_rows(
        [{"sample": "a", "accession": "SRX25141026", "fastq_1": "", "fastq_2": ""}], cache)
    assert missing == []
    assert rows[0]["fastq_1"].endswith("SRX25141026_SRR29635279.fastq.gz") and rows[0]["fastq_2"] == ""


def test_fill_rows_experiment_with_several_runs_emits_one_row_per_run(tmp_path):
    # One experiment, two runs: nf-core pipelines merge rows that share a sample, so every run
    # becomes its own row. Picking one would silently drop reads.
    cache = _cache(tmp_path,
                   "SRX1_SRR11_1.fastq.gz", "SRX1_SRR11_2.fastq.gz",
                   "SRX1_SRR12_1.fastq.gz", "SRX1_SRR12_2.fastq.gz")
    sheet = [
        {"sample": "before", "accession": "", "fastq_1": "/net/bmc/x_1.fastq.gz", "fastq_2": "", "strandedness": "auto"},
        {"sample": "a", "accession": "SRX1", "fastq_1": "", "fastq_2": "", "strandedness": "reverse"},
        {"sample": "after", "accession": "", "fastq_1": "/net/bmc/y_1.fastq.gz", "fastq_2": "", "strandedness": "auto"},
    ]
    rows, missing = fill_rows(sheet, cache)
    assert missing == []
    assert [r["sample"] for r in rows] == ["before", "a", "a", "after"]
    runs = rows[1:3]
    assert [r["fastq_1"].rsplit("/", 1)[1] for r in runs] == ["SRX1_SRR11_1.fastq.gz", "SRX1_SRR12_1.fastq.gz"]
    assert [r["fastq_2"].rsplit("/", 1)[1] for r in runs] == ["SRX1_SRR11_2.fastq.gz", "SRX1_SRR12_2.fastq.gz"]
    assert all(r["strandedness"] == "reverse" and r["accession"] == "SRX1" for r in runs)


def test_fill_rows_run_of_a_multi_run_experiment_gets_only_its_own_files(tmp_path):
    cache = _cache(tmp_path,
                   "SRX1_SRR11_1.fastq.gz", "SRX1_SRR11_2.fastq.gz",
                   "SRX1_SRR12_1.fastq.gz", "SRX1_SRR12_2.fastq.gz")
    rows, missing = fill_rows([{"sample": "a", "accession": "SRR12", "fastq_1": "", "fastq_2": ""}], cache)
    assert missing == [] and len(rows) == 1
    assert rows[0]["fastq_1"].endswith("SRX1_SRR12_1.fastq.gz")


def test_fill_rows_ena_and_ddbj_experiments(tmp_path):
    cache = _cache(tmp_path,
                   "ERX100_ERR200_1.fastq.gz", "ERX100_ERR200_2.fastq.gz",
                   "DRX300_DRR400.fastq.gz")
    rows, missing = fill_rows([
        {"sample": "e", "accession": "ERX100", "fastq_1": "", "fastq_2": ""},
        {"sample": "d", "accession": "DRX300", "fastq_1": "", "fastq_2": ""},
    ], cache)
    assert missing == []
    assert rows[0]["fastq_1"].endswith("ERX100_ERR200_1.fastq.gz")
    assert rows[0]["fastq_2"].endswith("ERX100_ERR200_2.fastq.gz")
    assert rows[1]["fastq_1"].endswith("DRX300_DRR400.fastq.gz") and rows[1]["fastq_2"] == ""


def test_fill_rows_accession_never_prefix_matches_a_longer_accession(tmp_path):
    # SRR1 must not take SRR10's files, nor SRX1 take SRX10's.
    cache = _cache(tmp_path,
                   "SRX10_SRR10_1.fastq.gz", "SRX10_SRR10_2.fastq.gz",
                   "SRR15_1.fastq.gz", "SRX7_SRR100.fastq.gz")
    rows, missing = fill_rows([
        {"sample": "a", "accession": "SRR1", "fastq_1": "", "fastq_2": ""},
        {"sample": "b", "accession": "SRX1", "fastq_1": "", "fastq_2": ""},
    ], cache)
    assert missing == ["SRR1", "SRX1"]
    assert all(r["fastq_1"] == "" for r in rows)


def test_fill_rows_ignores_a_lone_read_2(tmp_path):
    cache = _cache(tmp_path, "SRX1_SRR11_2.fastq.gz")
    _, missing = fill_rows([{"sample": "a", "accession": "SRX1", "fastq_1": "", "fastq_2": ""}], cache)
    assert missing == ["SRX1"]


def test_main_fill_writes_one_row_per_run(tmp_path):
    cache = _cache(tmp_path, "SRX1_SRR11_1.fastq.gz", "SRX1_SRR12_1.fastq.gz")
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sample,accession,fastq_1,fastq_2\na,SRX1,,\n", encoding="utf-8")
    assert _main_fill(cache, str(sheet)) == 0
    with open(sheet, newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["sample"] for r in rows] == ["a", "a"]
    assert [Path(r["fastq_1"]).name for r in rows] == ["SRX1_SRR11_1.fastq.gz", "SRX1_SRR12_1.fastq.gz"]


# --- the shell's "already cached?" check uses the same matcher as fill --------------------

def test_uncached_accessions_uses_the_fill_matcher(tmp_path):
    cache = _cache(tmp_path, "SRX1_SRR11_1.fastq.gz", "SRR10_1.fastq.gz")
    # SRR1 is a substring of SRR10 and of SRR11 but has no files of its own.
    assert uncached_accessions(["SRX1", "SRR11", "SRR10", "SRR1"], cache) == ["SRR1"]


def test_main_cached_exit_code(tmp_path):
    cache = _cache(tmp_path, "SRX1_SRR11_1.fastq.gz")
    ids = tmp_path / "ids.csv"
    ids.write_text("SRX1\n", encoding="utf-8")
    assert _main_cached(cache, str(ids)) == 0
    ids.write_text("SRX1\nSRR1\n", encoding="utf-8")
    assert _main_cached(cache, str(ids)) == 1


def test_main_cached_with_no_fastq_dir_is_not_cached(tmp_path):
    ids = tmp_path / "ids.csv"
    ids.write_text("SRR1\n", encoding="utf-8")
    assert _main_cached(str(tmp_path), str(ids)) == 1


# --- fetchngs' run list (<cache>/metadata/<acc>.runinfo_ftp.tsv) catches a partial download ---

def _runinfo(tmp_path, acc, runs, header="id\trun_accession\texperiment_accession\tfastq_ftp"):
    # The leading columns of fetchngs 1.12's runinfo_ftp.tsv, as found in the Luria cache.
    meta = tmp_path / "metadata"
    meta.mkdir(exist_ok=True)
    lines = [header] + [f"{acc}_{r}\t{r}\t{acc}\tftp.sra.ebi.ac.uk/{r}_1.fastq.gz" for r in runs]
    (meta / f"{acc}.runinfo_ftp.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_partial_download_of_a_multi_run_experiment_is_missing_not_filled(tmp_path):
    # An interrupted fetch left SRR11 but not SRR12; filling from SRR11 alone would drop reads.
    cache = _cache(tmp_path, "SRX1_SRR11_1.fastq.gz", "SRX1_SRR11_2.fastq.gz")
    _runinfo(tmp_path, "SRX1", ["SRR11", "SRR12"])
    rows, missing = fill_rows([{"sample": "a", "accession": "SRX1", "fastq_1": "", "fastq_2": ""}], cache)
    assert missing == ["SRX1"]
    assert rows == [{"sample": "a", "accession": "SRX1", "fastq_1": "", "fastq_2": ""}]


def test_partial_download_is_not_cached_so_fetchngs_reruns(tmp_path):
    cache = _cache(tmp_path, "SRX1_SRR11_1.fastq.gz")
    _runinfo(tmp_path, "SRX1", ["SRR11", "SRR12"])
    assert uncached_accessions(["SRX1"], cache) == ["SRX1"]


def test_complete_download_matches_the_run_list(tmp_path):
    cache = _cache(tmp_path, "SRX1_SRR11_1.fastq.gz", "SRX1_SRR12_1.fastq.gz")
    _runinfo(tmp_path, "SRX1", ["SRR11", "SRR12"])
    rows, missing = fill_rows([{"sample": "a", "accession": "SRX1", "fastq_1": "", "fastq_2": ""}], cache)
    assert missing == [] and len(rows) == 2
    assert uncached_accessions(["SRX1"], cache) == []


def test_run_list_without_a_run_column_falls_back_to_the_files(tmp_path):
    cache = _cache(tmp_path, "SRX1_SRR11_1.fastq.gz")
    _runinfo(tmp_path, "SRX1", ["SRR11", "SRR12"], header="id\taccession\texperiment_accession\tfastq_ftp")
    rows, missing = fill_rows([{"sample": "a", "accession": "SRX1", "fastq_1": "", "fastq_2": ""}], cache)
    assert missing == [] and len(rows) == 1
