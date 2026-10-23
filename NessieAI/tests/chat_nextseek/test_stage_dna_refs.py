"""stage_dna_refs.sh stages exactly the files run_script names for GATK.GRCh38."""
import fnmatch
import re
import subprocess
from pathlib import Path

import chat_nextseek.luria.run_script as rs

SCRIPT = Path(rs.__file__).parent / "refs" / "stage_dna_refs.sh"


def _staged() -> set[str]:
    return set(re.findall(r'^\s+"\S+ (\S+)"$', SCRIPT.read_text(), re.M))


def _named() -> list[str]:
    names = [rs.LURIA_GENOMES["GATK.GRCh38"]["fasta"], *rs.LURIA_GENOME_RESOURCES["GATK.GRCh38"].values()]
    return [n.split("/", 1)[1] for n in names]


def test_script_parses():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def _matches(staged: str, name: str) -> bool:
    # A name ending in '/' is a folder (sarek's --bwa): it covers the files staged under it.
    return staged.startswith(name) if name.endswith("/") else fnmatch.fnmatch(staged, name)


def test_every_named_file_is_staged():
    staged = _staged()
    for name in _named():
        assert any(_matches(s, name) for s in staged), name


def test_every_staged_file_is_named():
    named = _named()
    for staged in _staged():
        assert any(_matches(staged, n) for n in named), staged


def test_the_whole_bwa_index_is_staged():
    exts = {s.rsplit(".", 1)[1] for s in _staged() if s.startswith("BWAIndex/")}
    assert exts == {"alt", "amb", "ann", "bwt", "pac", "sa"}


def test_both_known_indel_sets_are_staged():
    hits = [s for s in _staged() if fnmatch.fnmatch(s, "known_indels/*.vcf.gz")]
    assert len(hits) == 2, hits
