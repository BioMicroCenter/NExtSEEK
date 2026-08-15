#!/usr/bin/env python3
import argparse, hashlib, importlib.util, json, os, sys, tempfile
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "validate_attribute_api_evidence",
    Path(__file__).resolve().parent / "validate_attribute_api_evidence.py",
)
_validator = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _validator
_spec.loader.exec_module(_validator)
Rejected = _validator.Rejected
TASK_REQUIRED_LANES = _validator.TASK_REQUIRED_LANES
validate = _validator.validate

def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()

def stage(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload); stream.flush(); os.fsync(stream.fileno())
    temporary = Path(temporary)
    if temporary.read_bytes() != payload:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("staged evidence bytes drifted")
    return temporary

def publish(staged, output):
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(staged.read_bytes()); stream.flush(); os.fsync(stream.fileno())
    except BaseException:
        Path(output).unlink(missing_ok=True)
        raise
    directory = os.open(output.parent, os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)

def validate_selection_payload(value, task, required_lanes, root):
    if (set(value) != {"schema_version", "task", "records"}
            or value["schema_version"] != "attribute-viewset-evidence-selection/v1"
            or value["task"] != task or set(value["records"]) != required_lanes):
        raise RuntimeError("selection payload shape drift")
    for selected in value["records"].values():
        if set(selected) != {"path", "sha256"}:
            raise RuntimeError("selection member shape drift")
        path = root / selected["path"]
        if (not path.is_file() or path.is_symlink()
                or hashlib.sha256(path.read_bytes()).hexdigest() != selected["sha256"]):
            raise RuntimeError("selection member checksum drift")

def replace_pointer(path, value):
    staged = stage(path, encoded(value))
    try:
        os.chmod(staged, 0o444)
        os.replace(staged, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    finally: staged.unlink(missing_ok=True)

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True); parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--record", action="append", required=True)
    parser.add_argument("--selection-output", type=Path, required=True)
    parser.add_argument("--baseline-output", type=Path)
    parser.add_argument("--pointer-output", type=Path, required=True)
    args = parser.parse_args(argv); root = args.root.resolve(strict=True); records = {}
    manifest = json.loads(args.manifest.read_text())
    if args.task == "task-00" and args.baseline_output is None:
        raise SystemExit("task-00 requires --baseline-output")
    if args.task != "task-00" and args.baseline_output is not None:
        raise SystemExit("only task-00 may publish a baseline")
    outputs = [args.selection_output, args.pointer_output] + ([args.baseline_output] if args.baseline_output else [])
    if len({path.resolve(strict=False) for path in outputs}) != len(outputs):
        raise SystemExit("generation outputs and canonical pointer must be distinct")
    generations = [args.selection_output] + ([args.baseline_output] if args.baseline_output else [])
    if any(path.exists() for path in generations):
        raise SystemExit("immutable generation outputs must be absent")
    if args.pointer_output.exists() and (args.pointer_output.is_symlink() or not args.pointer_output.is_file()):
        raise SystemExit("canonical pointer must be absent or an ordinary file")
    if args.pointer_output.resolve(strict=False) != root / "selection.json":
        raise SystemExit("--pointer-output must be the canonical task-root selection.json")
    for generation in [args.selection_output] + ([args.baseline_output] if args.baseline_output else []):
        if generation.is_symlink() or not generation.resolve(strict=False).is_relative_to(root):
            raise SystemExit("immutable generations must remain under the task evidence root")
    for raw in args.record:
        if any(token in raw for token in "*?[]"):
            raise SystemExit("record paths are explicit; globs are forbidden")
        relative = Path(raw)
        path = args.root / relative
        if relative.is_absolute() or ".." in relative.parts or path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
            raise SystemExit("record path escape/symlink")
        payload = json.loads(path.read_text())
        lane = payload.get("lane")
        if payload.get("task_id") != args.task or payload.get("exit_code") != 0 or lane in records:
            raise SystemExit("record task/exit/lane is not selectable")
        validate(payload, manifest, artifact_root=path.parent)
        records[lane] = {"path": relative.as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    if set(records) != TASK_REQUIRED_LANES[args.task]:
        raise SystemExit("selected lanes are not the exact required set")
    selection_value = {"schema_version": "attribute-viewset-evidence-selection/v1", "task": args.task, "records": records}
    validate_selection_payload(selection_value, args.task, TASK_REQUIRED_LANES[args.task], args.root)
    selection_bytes = encoded(selection_value)
    if json.loads(selection_bytes) != selection_value:
        raise RuntimeError("selection payload failed round-trip validation")
    baseline_bytes = None
    if args.task == "task-00":
        full_record = args.root / records["full"]["path"]
        node_path = full_record.parent / "node-results.json"
        rows = json.loads(node_path.read_text())
        nodes = [row["nodeid"] for row in rows]
        if nodes != sorted(set(nodes)):
            raise Rejected("E_NODE_RESULTS")
        baseline_bytes = encoded(nodes)
        if json.loads(baseline_bytes) != nodes:
            raise RuntimeError("baseline payload failed round-trip validation")
    staged_selection = staged_baseline = None
    try:
        staged_selection = stage(args.selection_output, selection_bytes)
        staged_baseline = stage(args.baseline_output, baseline_bytes) if baseline_bytes is not None else None
        publish(staged_selection, args.selection_output)
        if staged_baseline is not None:
            publish(staged_baseline, args.baseline_output)
        if hashlib.sha256(args.selection_output.read_bytes()).hexdigest() != hashlib.sha256(selection_bytes).hexdigest():
            raise RuntimeError("published selection checksum drift")
        pointer = {"schema_version": "attribute-viewset-evidence-pointer/v1",
                   "task": args.task,
                   "selection": {"path": args.selection_output.resolve().relative_to(root).as_posix(),
                                 "sha256": hashlib.sha256(selection_bytes).hexdigest()}}
        if baseline_bytes is not None:
            baseline_sha = hashlib.sha256(baseline_bytes).hexdigest()
            if hashlib.sha256(args.baseline_output.read_bytes()).hexdigest() != baseline_sha:
                raise RuntimeError("published baseline checksum drift")
            pointer["baseline"] = {"path": args.baseline_output.resolve().relative_to(root).as_posix(),
                                  "sha256": baseline_sha}
        replace_pointer(args.pointer_output, pointer)
        if baseline_bytes is not None: print(pointer["baseline"]["sha256"])
    finally:
        if staged_selection is not None: staged_selection.unlink(missing_ok=True)
        if staged_baseline is not None: staged_baseline.unlink(missing_ok=True)

if __name__ == "__main__": main()
