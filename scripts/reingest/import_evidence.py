#!/usr/bin/env python3
"""Turn nf-core's own published evidence into a reingest fixture (G-7 in NessieAI/ns/reingest_maps/README.md).

  import_evidence.py snapshot <pipeline> <version> <snap-name> [--test NAME]
                    [--variant NAME] [--param KEY=VALUE ...]
      Inventory from tests/<snap-name>.nf.test.snap at the release tag; the samplesheet and
      params from conf/test.config. A snap file can hold several nf-test tests: --test
      imports only the one with that name (else every test that is not a stub, merged).
      --param adds a param the test sets in its own .nf.test params block, which this
      script does not read (aligner=bwameth, rrbs=true). --variant names the folder
      <version>-<variant>, so a flag-dependent fixture sits beside the release's own.
  import_evidence.py aws <pipeline> <version> <profile> [--samplesheet-url URL]
      Inventory, MultiQC tables and run params from nf-core's AWS results,
      s3://nf-core-awsmegatests/<pipeline>/results-<release commit>/<profile>/.

Writes NessieAI/tests/ns/fixtures/reingest_runs/<pipeline>/<folder>/ in the layout
make_fixture.sh writes, plus EVIDENCE.txt naming the source; <folder> is <version>,
<version>-<variant>, or <version>-aws-<profile>. Reads public URLs only and touches no
cluster. Run it inside the stack image with PYTHONPATH=/src/NessieAI/chat_nextseek/src (it
imports pack_draft).

The fixture is built in a temporary folder, run through sanitize_fixture.py (cluster prefix
/RUN), and moved into place only if the sanitizer accepts it; otherwise the sanitizer's
refusal is printed, nothing is written under reingest_runs/ and the exit status is 1.
EVIDENCE.txt names its sources without a scheme, because the sanitizer refuses any scheme://.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

RAW = "https://raw.githubusercontent.com/nf-core"
API = "https://api.github.com/repos/nf-core"
BUCKET = "https://nf-core-awsmegatests.s3-eu-west-1.amazonaws.com"
_S3 = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_MAX_TABLE_BYTES = 5_000_000
_SKIP_TABLES = {"multiqc_sources.txt", "multiqc_citations.txt", "multiqc_software_versions.txt",
                "multiqc_data.json", "llms-full.txt"}
_TABLE = re.compile(r"(^|/)multiqc/(.+/)?multiqc_data/(multiqc_[^/]+\.txt)$")
_RUN_INFO = re.compile(r"^pipeline_info/(params_[^/]+\.json|[^/]*software_mqc_versions\.yml)$")
_INPUT = re.compile(r"""^\s*input\s*=\s*['"]([^'"]+)['"]""", re.M)
_ASSIGN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_COMMENT = re.compile(r"\s(//|#).*$")
# One complete quoted string: its body holds no quote character of its own kind, so a
# concatenation such as 'x' + 'y' is an expression and matches nothing.
_QUOTED = re.compile(r"""^(?:'([^']*)'|"([^"]*)")(?:\s+(?://|#).*)?$""")
# A variant folder name: what follows "<version>-". No slash, no leading dot or dash.
_VARIANT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _fetch(url: str) -> bytes:
    import requests
    resp = requests.get(url, timeout=(5, 60), headers={"User-Agent": "nextseek-import-evidence"})
    resp.raise_for_status()
    return resp.content


def parse_s3_listing(xml_bytes: bytes) -> tuple[list[dict], str | None]:
    root = ET.fromstring(xml_bytes)
    entries = [{"key": c.findtext(f"{_S3}Key"), "size": int(c.findtext(f"{_S3}Size") or 0)}
               for c in root.findall(f"{_S3}Contents")]
    return entries, root.findtext(f"{_S3}NextContinuationToken")


def list_prefix(prefix: str, fetch) -> list[dict]:
    entries, token = [], None
    while True:
        url = f"{BUCKET}/?list-type=2&max-keys=1000&prefix={urllib.parse.quote(prefix)}"
        if token:
            url += f"&continuation-token={urllib.parse.quote(token)}"
        page, token = parse_s3_listing(fetch(url))
        entries += page
        if not token:
            return entries


def aws_inventory(entries: list[dict], prefix: str) -> list[dict]:
    return sorted(({"path": e["key"][len(prefix):], "bytes": e["size"]}
                   for e in entries if e["key"].startswith(prefix) and not e["key"].endswith("/")),
                  key=lambda e: e["path"])


def wanted_aws_files(inventory: list[dict]) -> list[str]:
    out = []
    for entry in inventory:
        path, size = entry["path"], entry["bytes"]
        table = _TABLE.search(path)
        if table and table.group(3) not in _SKIP_TABLES and size <= _MAX_TABLE_BYTES:
            out.append(path)
        elif _RUN_INFO.match(path):
            out.append(path)
    return out


def localise_samplesheet(text: str) -> str:
    """Every remote or absolute path cell becomes /RUN/inputs/<file name>."""
    rows = list(csv.reader(io.StringIO(text)))
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    for i, row in enumerate(rows):
        if i == 0:
            writer.writerow(row)
            continue
        writer.writerow([f"/RUN/inputs/{cell.rsplit('/', 1)[-1]}"
                         if cell.startswith(("http://", "https://", "s3://", "/", "${"))
                         else cell for cell in row])
    return out.getvalue()


def versions_yml_from_snapshot(snapshot: dict, pipeline: str, tag: str | None = None,
                               test_name: str | None = None) -> str:
    """The pipeline's own version from the snapshot's Workflow entry (only `test_name`'s, when
    given). Some snapshots (sarek's) list only process tool versions; then the release tag the
    snapshot was read at stands in."""
    for name, test in snapshot.items():
        if test_name is not None and name != test_name:
            continue
        for part in test.get("content") or []:
            if isinstance(part, dict) and isinstance(part.get("Workflow"), dict):
                version = part["Workflow"].get(f"nf-core/{pipeline}")
                if version:
                    return f"Workflow:\n  nf-core/{pipeline}: {version}\n"
    if tag:
        return f"Workflow:\n  nf-core/{pipeline}: v{tag.removeprefix('v')}\n"
    raise SystemExit(f"no Workflow version for nf-core/{pipeline} in the snapshot")


def config_params(text: str) -> dict:
    """Scalar `name = value` lines of a Nextflow config. A comment starts at whitespace then `//`
    or `#`, so `https://...` values survive whole. A value is kept only if it is a complete quoted
    string, true/false or a number; closures `{ ... }`, lists `[ ...` and other expressions yield
    nothing."""
    found: dict = {}
    for line in text.splitlines():
        m = _ASSIGN.match(line)
        if not m:
            continue
        name, raw = m.group(1), m.group(2).strip()
        quoted = _QUOTED.match(raw)
        if not quoted:
            raw = _COMMENT.sub("", raw).strip()
        if quoted:
            found[name] = quoted.group(1) if quoted.group(1) is not None else quoted.group(2)
        elif raw in ("true", "false"):
            found[name] = raw == "true"
        elif re.fullmatch(r"-?\d+", raw):
            found[name] = int(raw)
        elif re.fullmatch(r"-?\d+\.\d+", raw):
            found[name] = float(raw)
    return found


def param_value(text: str):
    """One `--param KEY=VALUE` value, typed the way config_params types a config line:
    true/false, an integer, a decimal, else the text itself."""
    if text in ("true", "false"):
        return text == "true"
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def param_pairs(pairs: list[str]) -> dict:
    """`--param` arguments as a dict. A pair without `=`, or with an empty key, is refused."""
    out: dict = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise SystemExit(f"--param takes KEY=VALUE, got {pair!r}")
        out[key.strip()] = param_value(value.strip())
    return out


def fixture_folder(version: str, variant: str | None) -> str:
    """`<version>`, or `<version>-<variant>` for a flag-dependent fixture beside it."""
    if variant is None:
        return version
    if not _VARIANT.fullmatch(variant):
        raise SystemExit(f"--variant must be letters, digits, '.', '_' or '-' "
                         f"(not first), got {variant!r}")
    return f"{version}-{variant}"


def evidence_params(params: dict) -> dict:
    """Scalar params a map can read; anything that is a path or a URL is dropped."""
    kept = {k: v for k, v in params.items()
            if isinstance(v, (bool, int, float)) or (isinstance(v, str) and v
               and not v.startswith(("/", "s3://", "gs://", "http://", "https://", "${")))}
    kept.update({"outdir": "/RUN", "input": "/RUN/samplesheet.csv"})
    return kept


def write_fixture(dest: Path, *, pipeline: str, version: str, inventory: list[dict],
                  samplesheet: str, params: dict, versions_yml: str, files: dict[str, bytes],
                  evidence: str) -> None:
    if dest.exists():
        raise SystemExit(f"already exists: {dest}")
    (dest / "pipeline_info").mkdir(parents=True)
    on_disk = set(files)
    (dest / "inventory.json").write_text(json.dumps(
        [e for e in inventory if e["path"] not in on_disk], indent=1) + "\n")
    (dest / "samplesheet.csv").write_text(samplesheet)
    (dest / "pipeline_info" / "params_evidence.json").write_text(json.dumps(params, indent=1, sort_keys=True) + "\n")
    (dest / "pipeline_info" / f"nf_core_{pipeline}_software_mqc_versions.yml").write_text(versions_yml)
    (dest / "launch_record.json").write_text(json.dumps(
        {"run_dir": "/RUN", "pipeline": f"nf-core/{pipeline}",
         "revision": version.split("-", 1)[0], "cohort": []}) + "\n")
    for path, data in files.items():
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (dest / "EVIDENCE.txt").write_text(evidence.rstrip() + "\n")


def _test_config(pipeline: str, version: str, conf_name: str, fetch) -> str:
    return fetch(f"{RAW}/{pipeline}/{version}/conf/{conf_name}.config").decode()


def _samplesheet(pipeline: str, version: str, config_text: str, url: str | None, fetch) -> str:
    if not url:
        found = _INPUT.search(config_text)
        if not found:
            raise SystemExit("no input = '...' in the test config; pass --samplesheet-url")
        url = found.group(1).replace("${projectDir}", f"{RAW}/{pipeline}/{version}")
        if "${" in url:
            raise SystemExit(f"the test config's input is {found.group(1)!r}, which this script "
                             "cannot resolve; pass --samplesheet-url")
    return localise_samplesheet(fetch(url).decode())


def from_snapshot(pipeline: str, version: str, snap_name: str, fetch,
                  samplesheet_url: str | None = None, *, test: str | None = None,
                  variant: str | None = None, params: dict | None = None) -> dict:
    """A fixture from tests/<snap_name>.nf.test.snap: every test that is not a stub, merged, or
    only `test`. `params` are the test's own params, laid over conf/test.config's."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "NessieAI/chat_nextseek/src"))
    from chat_nextseek.seqera.pack_draft import snapshot_paths
    folder = fixture_folder(version, variant)
    url = f"{RAW}/{pipeline}/{version}/tests/{snap_name}.nf.test.snap"
    snapshot = json.loads(fetch(url))
    if test is not None and test not in snapshot:
        raise SystemExit(f"no test {test!r} in tests/{snap_name}.nf.test.snap; it has: "
                         + "; ".join(repr(name) for name in snapshot))
    config = _test_config(pipeline, version, "test", fetch)
    run_params = {**config_params(config), **(params or {})}
    try:
        versions_yml = versions_yml_from_snapshot(snapshot, pipeline, test_name=test)
        has_workflow = True
    except SystemExit:
        versions_yml, has_workflow = versions_yml_from_snapshot(snapshot, pipeline, version, test), False
    which = f'test "{test}" of ' if test is not None else ""
    set_by_test = ", ".join(f"{k}={str(v).lower() if isinstance(v, bool) else v}"
                            for k, v in sorted((params or {}).items()))
    return {"folder": folder,
            "inventory": [{"path": p, "bytes": 0} for p in snapshot_paths(snapshot, test)],
            "samplesheet": _samplesheet(pipeline, version, config, samplesheet_url, fetch),
            "params": evidence_params(run_params),
            "versions_yml": versions_yml,
            "files": {},
            "evidence": (f"Evidence: GitHub nf-core/{pipeline} tag {version}, "
                         f"{which}tests/{snap_name}.nf.test.snap and conf/test.config, read "
                         f"{date.today().isoformat()}."
                         + (f" Params the test sets, given by hand: {set_by_test}." if set_by_test else "")
                         + " File sizes are not in a snapshot, so every "
                         "inventory entry has bytes 0. No MultiQC values: snapshots hold none."
                         + ("" if has_workflow else
                            f" The workflow version comes from the release tag {version}, "
                            "because the snapshot lists none."))}


def from_aws(pipeline: str, version: str, profile: str, samplesheet_url: str | None, fetch) -> dict:
    sha = json.loads(fetch(f"{API}/{pipeline}/commits/{version}"))["sha"]
    prefix = f"{pipeline}/results-{sha}/{profile}/"
    inventory = aws_inventory(list_prefix(prefix, fetch), prefix)
    if not inventory:
        raise SystemExit(f"nothing published under s3://nf-core-awsmegatests/{prefix}")
    wanted = wanted_aws_files(inventory)
    files = {p: fetch(f"{BUCKET}/{urllib.parse.quote(prefix + p)}") for p in wanted}
    params_paths = sorted(p for p in wanted if p.startswith("pipeline_info/params_"))
    versions_paths = sorted(p for p in wanted if p.endswith("software_mqc_versions.yml"))
    if not versions_paths:
        raise SystemExit("no software_mqc_versions.yml in the published run")
    # Every run-info file is rewritten under a fixed name, so none stays in files or inventory;
    # the newest params file (by name, which carries the timestamp) is the one kept.
    params_blobs = [files.pop(p) for p in params_paths]
    params = json.loads(params_blobs[-1]) if params_blobs else {}
    versions_yml = files.pop(versions_paths[0]).decode()
    for extra in versions_paths[1:]:
        files.pop(extra)
    renamed = set(params_paths) | set(versions_paths)
    config = _test_config(pipeline, version, profile.removesuffix("_aws"), fetch) if not samplesheet_url else ""
    return {"folder": f"{version}-aws-{profile}",
            "inventory": [{"path": e["path"], "bytes": 0} for e in inventory
                          if e["path"] not in renamed],
            "samplesheet": _samplesheet(pipeline, version, config, samplesheet_url, fetch),
            "params": evidence_params(params), "versions_yml": versions_yml, "files": files,
            "evidence": (f"Evidence: nf-core/{pipeline} {version} AWS results, S3 bucket "
                         f"nf-core-awsmegatests, prefix {prefix}, listed {date.today().isoformat()}. "
                         f"MultiQC tables downloaded: {len(files)}; other files are inventory only, "
                         "and every inventory size is recorded as 0, as make_fixture.sh does.")}


def install_fixture(dest: Path, *, pipeline: str, version: str, built: dict) -> int:
    """Write the fixture to a temporary folder, sanitize it, and move it to `dest` only if the
    sanitizer accepts it. A refusal prints the sanitizer's output and leaves `dest` absent."""
    if dest.exists():
        raise SystemExit(f"already exists: {dest}")
    fields = {k: v for k, v in built.items() if k != "folder"}
    scratch = Path(tempfile.mkdtemp(prefix="import_evidence_"))
    try:
        staged = scratch / dest.name
        write_fixture(staged, pipeline=pipeline, version=version, **fields)
        done = subprocess.run([sys.executable, str(Path(__file__).resolve().with_name("sanitize_fixture.py")),
                               str(staged), "/RUN"], capture_output=True, text=True)
        if done.returncode != 0:
            print(done.stdout + done.stderr, end="", file=sys.stderr)
            print(f"not written: the sanitizer refused {dest.name}", file=sys.stderr)
            return 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staged), str(dest))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", choices=["snapshot", "aws"])
    parser.add_argument("pipeline")
    parser.add_argument("version")
    parser.add_argument("name", help="snapshot file name (tests/<name>.nf.test.snap), or AWS profile folder")
    parser.add_argument("--samplesheet-url", default=None)
    parser.add_argument("--test", default=None,
                        help="snapshot only: import this one nf-test test of the snap file")
    parser.add_argument("--variant", default=None,
                        help="snapshot only: write the fixture to <version>-<variant>")
    parser.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                        help="snapshot only: a param the test sets in its .nf.test (repeatable)")
    args = parser.parse_args()
    if args.source == "aws" and (args.test or args.variant or args.param):
        parser.error("--test, --variant and --param are for snapshot fixtures")
    built = (from_snapshot(args.pipeline, args.version, args.name, _fetch, args.samplesheet_url,
                           test=args.test, variant=args.variant, params=param_pairs(args.param))
             if args.source == "snapshot"
             else from_aws(args.pipeline, args.version, args.name, args.samplesheet_url, _fetch))
    dest = (Path(__file__).resolve().parents[2] / "NessieAI/tests/ns/fixtures/reingest_runs"
            / args.pipeline / built["folder"])
    status = install_fixture(dest, pipeline=args.pipeline, version=args.version, built=built)
    if status == 0:
        print(f"fixture written and sanitized: {dest}")
        print("next: review it per the reingest_runs README, then run the fixture test")
    return status


if __name__ == "__main__":
    sys.exit(main())
