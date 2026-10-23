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


def test_every_named_file_is_staged():
    staged = _staged()
    for name in _named():
        assert any(fnmatch.fnmatch(s, name) for s in staged), name


def test_every_staged_file_is_named():
    named = _named()
    for staged in _staged():
        assert any(fnmatch.fnmatch(staged, n) for n in named), staged


def test_both_known_indel_sets_are_staged():
    hits = [s for s in _staged() if fnmatch.fnmatch(s, "known_indels/*.vcf.gz")]
    assert len(hits) == 2, hits
