"""Live contract check for the paper reingest adapters (network; run by hand, never in CI).

Usage: PYTHONPATH=. uv run --no-project --with 'pydantic>=2.13' --with requests --with openpyxl \
         python scripts/paper_reingest_live_check.py geo:GSE252781 pride:PXD048479 ena:SRP000001 \
         zenodo:<id> figshare:<id> sdr:<druid> epmc:<pmid>

Prints, per accession, how many records of each kind came back and how many files they
carry, or the error. A zero or an error on an accession you know is public means the
API shape changed: fix the adapter and its fixture together.
"""
from __future__ import annotations

import collections
import sys

from NessieAI.ns.paper import europepmc
from NessieAI.ns.paper.deposits import ADAPTERS
from NessieAI.ns.paper.fetch import Fetcher
from NessieAI.ns.paper.jats import parse_jats
from NessieAI.ns.paper.models import PaperInput


def main(argv: list[str]) -> int:
    fetcher, failures = Fetcher(), 0
    for arg in argv:
        repo, _, acc = arg.partition(":")
        try:
            if repo == "epmc":
                ref = europepmc.resolve(PaperInput(kind="pmid", value=acc), fetcher)
                xml = europepmc.full_text_xml(ref.pmcid, fetcher) if ref and ref.pmcid else None
                text = parse_jats(xml) if xml else None
                print(f"{arg}: open_access={ref.open_access if ref else None} "
                      f"sections={[s.slug for s in text.sections] if text else None} "
                      f"supplements={len(text.supplements) if text else None}")
                continue
            records = ADAPTERS[repo](acc, fetcher)
            kinds = collections.Counter(r.kind for r in records)
            print(f"{arg}: {dict(kinds)} files={sum(len(r.files) for r in records)}")
            failures += 0 if records else 1
        except Exception as exc:  # report and keep going
            failures += 1
            print(f"{arg}: {type(exc).__name__}: {exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
