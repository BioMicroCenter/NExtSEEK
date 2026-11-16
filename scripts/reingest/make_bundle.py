"""Write a results bundle for `run-harvest --bundle` from a run's results folder on this machine.

    python3 scripts/reingest/make_bundle.py <results folder> --origin <where the files live> \
        [--samplesheet <the run's input csv>] [--listing <path<TAB>bytes file>] -o run.tar.gz

The bundle holds what the Luria harvest would copy from the folder (the same allowlist,
`harvest.staged_globs()`), the newest execution report (whose verdict line is all reingest
reads of it), the samplesheet as `samplesheet.csv`, and `nessie_files.tsv`: the `#origin`
line, then one `path<TAB>bytes` line for every file reingest only lists
(`harvest.inventory_globs()`), taken from the folder's own files (their sizes, sparse
placeholders included) or, with `--listing`, from a listing made where the files really are
(for example `aws s3 ls --recursive`, cut to path and size). Links are never followed.

Run it from the repo root, with the app's environment (pydantic), for example inside the
app image. It reads only the folder and the files named, and writes only the bundle.
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import tarfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from NessieAI.ns.reingest import bundle, harvest


def _matches(rel: str, patterns) -> bool:
    return any(PurePosixPath(rel).full_match(p) for p in patterns)


def _walk(folder: Path):
    """Every regular file under folder, as (relative path, Path), never through a link."""
    for root, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(root, d)))
        for name in sorted(files):
            path = Path(root) / name
            if path.is_symlink() or not path.is_file():
                continue
            yield str(path.relative_to(folder)).replace(os.sep, "/"), path


def build(folder: Path, origin: str, out: Path, *, samplesheet: Path | None = None,
          listing: Path | None = None) -> dict:
    stage, inventory = harvest.staged_globs(), harvest.inventory_globs()
    members: dict[str, Path] = {}
    listed: dict[str, int] = {}
    reports: list[str] = []
    for rel, path in _walk(folder):
        if rel in (bundle.LISTING_NAME, harvest.RUN_VERDICT_NAME):
            continue
        if PurePosixPath(rel).full_match("pipeline_info/execution_report*.html"):
            reports.append(rel)
            members[rel] = path
            continue
        if _matches(rel, inventory) and listing is None:
            listed[rel] = path.stat().st_size
        if _matches(rel, stage) and path.stat().st_size <= harvest.MAX_FILE_BYTES:
            members[rel] = path
    for old in sorted(reports)[:-1]:
        members.pop(old)  # reingest reads the newest report only
    if samplesheet is not None:
        members["samplesheet.csv"] = samplesheet
    if listing is not None:
        for line in listing.read_text().splitlines():
            path, sep, size = line.partition("\t")
            if sep and size.strip().isdigit() and _matches(path, inventory):
                listed[path] = int(size)
    text = f"#origin\t{origin}\n" + "".join(f"{p}\t{n}\n" for p, n in sorted(listed.items()))
    with tarfile.open(out, "w:gz") as tar:
        for rel in sorted(members):
            tar.add(str(members[rel]), arcname=rel, recursive=False)
        data = text.encode()
        info = tarfile.TarInfo(bundle.LISTING_NAME)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return {"files": len(members), "listed": len(listed), "bytes": out.stat().st_size}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--origin", required=True, help="where the run's files live (absolute path or URL)")
    parser.add_argument("--samplesheet", type=Path, help="the run's input samplesheet, if not in the folder")
    parser.add_argument("--listing", type=Path, help="path<TAB>bytes lines for the files reingest lists")
    parser.add_argument("-o", "--out", type=Path, required=True)
    args = parser.parse_args(argv)
    summary = build(args.folder, args.origin, args.out, samplesheet=args.samplesheet, listing=args.listing)
    print(f"{args.out}: {summary['files']} files, {summary['listed']} listed, {summary['bytes']} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
