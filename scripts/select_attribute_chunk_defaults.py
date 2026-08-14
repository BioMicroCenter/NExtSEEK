from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
from itertools import product
from pathlib import Path


ROWS = (1000, 10000, 50000)
WIDTHS = (512, 4096, 16384)
CHUNK_ROWS = (250, 1000, 2000)
CHUNK_BYTES = (1048576, 4194304, 16777216)
WORKLOADS = ("idle", "rails_like")
REPETITIONS = range(5)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def percentile95(values):
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def select(raw_directory):
    files = sorted(raw_directory.glob("metadata-*.json"))
    source = [{"path": item.name, "sha256": digest(item.read_bytes())} for item in files]
    runs = [row for item in files for row in json.loads(item.read_text())]
    expected = set(product(ROWS, WIDTHS, CHUNK_ROWS, CHUNK_BYTES, WORKLOADS, REPETITIONS))
    observed = {(row["row_count"], row["metadata_width_bytes"], row["chunk_rows"],
                 row["chunk_bytes"], row["workload"], row["repetition"]) for row in runs}
    if observed != expected or len(runs) != len(expected):
        raise SystemExit("T06 raw Cartesian matrix is incomplete or duplicated")
    summaries, rejected = [], []
    for chunk_rows, chunk_bytes in product(CHUNK_ROWS, CHUNK_BYTES):
        selected = [row for row in runs if row["chunk_rows"] == chunk_rows
                    and row["chunk_bytes"] == chunk_bytes and not row["warmup"]]
        reasons = set()
        for row in selected:
            if row["errors"]: reasons.add("errors")
            if row["timeouts"]: reasons.add("timeouts")
            if row["before_checksum"] == row["after_checksum"]: reasons.add("checksum_unchanged")
            if row["peak_rss_bytes"] > 268435456: reasons.add("rss")
            if row["maximum_lock_wait_seconds"] > 2: reasons.add("lock")
            ceiling = 3 * math.ceil(row["row_count"] / chunk_rows) + 2
            if row["sql_count"] > ceiling: reasons.add("sql")
            if row["packet_bytes"] > chunk_bytes: reasons.add("packet")
        for width, workload in product(WIDTHS, WORKLOADS):
            medians = {}
            for count in ROWS:
                cell = [row["elapsed_seconds"] for row in selected
                        if row["row_count"] == count
                        and row["metadata_width_bytes"] == width
                        and row["workload"] == workload]
                if len(cell) != 4: reasons.add("repetitions")
                else: medians[count] = statistics.median(cell)
            if 10000 in medians and 50000 in medians and medians[50000] > 6 * medians[10000]:
                reasons.add("scaling")
        summary = {"chunk_rows": chunk_rows, "chunk_bytes": chunk_bytes,
                   "maximum_p95_seconds": max(percentile95([
                       row["elapsed_seconds"] for row in selected
                       if row["metadata_width_bytes"] == width and row["workload"] == workload
                       and row["row_count"] == count])
                       for width, workload, count in product(WIDTHS, WORKLOADS, ROWS)),
                   "maximum_peak_rss_bytes": max(row["peak_rss_bytes"] for row in selected)}
        (rejected if reasons else summaries).append(
            summary | ({"reasons": sorted(reasons)} if reasons else {})
        )
    if not summaries:
        raise SystemExit("no chunk pair passes every T06 gate")
    winner = min(summaries, key=lambda row: (row["maximum_p95_seconds"],
                 row["maximum_peak_rss_bytes"], row["chunk_bytes"], row["chunk_rows"]))
    return {"schema_version": "attribute-chunk-selection/v1",
            "source_matrix_sha256": digest(canonical(source)),
            "chunk_rows": winner["chunk_rows"], "chunk_bytes": winner["chunk_bytes"],
            "candidate_summaries": sorted(summaries, key=lambda row: (row["chunk_rows"], row["chunk_bytes"])),
            "rejected_candidates": sorted(rejected, key=lambda row: (row["chunk_rows"], row["chunk_bytes"]))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = canonical(select(args.raw_directory)); selection_sha256 = digest(payload)
    root = args.output.parent.resolve()
    artifact = root / "selections" / "sha256" / f"{selection_sha256}.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(artifact, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError:
        if artifact.is_symlink() or artifact.read_bytes() != payload:
            raise SystemExit("content-addressed selection collision")
    else:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
    pointer = canonical({"schema_version": "attribute-chunk-selection-pointer/v1",
                         "path": str(artifact), "sha256": selection_sha256})
    temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(pointer); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, args.output)
    directory = os.open(args.output.parent, os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)


if __name__ == "__main__":
    main()
