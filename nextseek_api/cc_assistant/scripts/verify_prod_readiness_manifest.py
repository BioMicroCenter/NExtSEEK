#!/usr/bin/env python3
"""DoD manifest (SPEC-dev-merge-and-e2e-verification.md §8): `generate` assembles
one after verification evidence exists; `--verify` RE-CHECKS everything by
independently recomputing it — it never trusts a manifest's own claims, and it
never trusts an artifact's own "PASS"/self-reported verdict either.

2026-07-09 hardening (this file implements the hardened bar, not the
task-13-brief.md sketch, which is an illustrative scaffold only):

- Every artifact record (image_provenance, each lanes_artifacts[i],
  migration_evidence, e2e_run_dir, db_backup, ledger, survivals_output,
  host_only_allowlist_output, each secret_scan_artifacts[i]) carries `path`,
  `sha256`, `command` (producer argv), `exit_code` (producer exit code), and
  `candidate_sha` (the merged SHA the artifact was produced against — a stale
  value here means the evidence predates the candidate under test); plus
  image/container/session/approval identity fields where applicable
  (`image_id`, `container_id`, `approval_path`/`approval_sha256`).
- `--verify` invokes `full_ui_e2e.py --approval <artifact> --run-dir <dir>
  --validate-only` as a REAL subprocess and FAILs unless it exits 0. It may
  parse `summary.json` (informationally, e.g. to surface context), but the
  pass/fail verdict is taken ONLY from the subprocess exit code — a mutated
  raw artifact with a still-"passing" `summary.json` must be caught by the
  invoked validator, not silently accepted here.
- `git rev-parse HEAD` is re-run and compared to `merged_sha`.
- Lane artifacts must be structured JSON carrying `collected` (node IDs),
  `failures`, `skips`, `xfails`, `deselected`, `image_id`, `source_sha`, and
  `db_identity`. Any non-empty `failures`, or any skip/xfail/deselected entry
  not explicitly marked `"expected": true`, is a FAIL. A free-form transcript
  containing the word "PASS" is not evidence.
- Image provenance is re-checked live: `docker image inspect` is re-run
  (injectable — see below) for every recorded tag, and any tag whose image ID
  no longer matches (rebuilt, re-tagged, or never existed) FAILs.
- Secret-scan artifacts are parsed as structured per-image JSON: `image_tag`,
  `image_id`, `categories` (all four of `filename`/`value`/`key_entropy`/
  `config_env` required), `export_member_count`, `scanned_bytes`, `command`,
  `exit_code`. Every category's `hits` must each match an `allowlist` entry;
  allowlist entries that are empty, `"*"`, `".*"`, `"**"`, or shorter than 4
  characters are rejected as overly broad (an exclusion that broad is not a
  reviewed allowlist, it is a bypass).
- DB backup + migration evidence are parsed semantically: the dump checksum
  is recomputed from the dump file's real bytes (not trusted from the
  record); `required_tables` must be a subset of `present_tables`; the 0007
  migration row must be confirmed present; every `foreign_keys` entry must be
  confirmed `present: true`; every `charset_equality` entry's
  `child_charset`/`parent_charset` are compared directly (not a trusted
  boolean); `second_migrate_output` must literally contain "No migrations to
  apply".
- `verify_merge_survivals.py` is re-run (injectable) and must exit 0.

All four "does something outside this process" calls
(`full_ui_e2e.py --validate-only`, `docker image inspect`, `git rev-parse
HEAD`, `verify_merge_survivals.py`) go through small module-level wrapper
functions (`_run_full_ui_e2e_validate`, `_docker_image_inspect`,
`_git_rev_parse_head`, `_run_survivals`) that `verify()` looks up by their
bare (monkeypatchable) module-global name, and additionally accepts as
optional keyword overrides — so callers/tests can inject fakes either by
`monkeypatch.setattr(module, "_docker_image_inspect", fake)` or by passing
`docker_image_inspect=fake` directly to `verify()`, with no docker/git/E2E
dependency required for the hermetic test suite
(test_verify_prod_readiness_manifest.py).

Usage:
    verify_prod_readiness_manifest.py <manifest.json> --verify
    verify_prod_readiness_manifest.py <manifest.json> --from <fields.json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = 1

# scripts -> cc_assistant -> nextseek_api -> repo root (mirrors full_ui_e2e.py's
# own parents[3] convention for this exact directory depth).
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FULL_UI_E2E = Path(__file__).with_name("full_ui_e2e.py")
DEFAULT_SURVIVALS = Path(__file__).with_name("verify_merge_survivals.py")

# Top-level manifest fields (SPEC §8 / task-13-brief.md scaffold, unchanged).
FIELDS = ["merged_sha", "merge_parents", "image_ids", "image_provenance", "lanes_artifacts",
          "migration_evidence", "e2e_run_dir", "e2e_approval_sha256", "db_backup",
          "db_backup_sha256", "ledger", "survivals_output", "host_only_allowlist_output",
          "secret_scan_artifacts", "blocker_fix_shas"]

# Every artifact record (a dict describing one produced piece of evidence)
# carries at least these fields, per the 2026-07-09 hardening.
_GENERIC_ARTIFACT_FIELDS = ("path", "sha256", "command", "exit_code", "candidate_sha")

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_BROAD_ALLOWLIST_MATCHES = {"*", "", ".*", "**"}
_SECRET_SCAN_CATEGORIES = ("filename", "value", "key_entropy", "config_env")
_LANE_CONTENT_KEYS = ("collected", "failures", "skips", "xfails", "deselected",
                      "image_id", "source_sha", "db_identity")
_MIGRATION_CONTENT_KEYS = ("required_tables", "present_tables", "migration_0007_present",
                           "foreign_keys", "charset_equality", "second_migrate_output")


# ── Injectable "reach outside this process" primitives ───────────────────
# Each is called by its BARE module-global name inside verify()'s helpers
# (never bound as a default-parameter value), so `monkeypatch.setattr(module,
# "<name>", fake)` intercepts it for every subsequent call in that test.


def _git_rev_parse_head(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo_root),
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _docker_image_inspect(tag: str) -> str | None:
    """Returns the image ID `tag` currently resolves to, or None if the tag
    no longer resolves (deleted, re-tagged elsewhere, docker unavailable)."""
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _run_full_ui_e2e_validate(script: Path, approval_path: Path, run_dir: Path):
    return subprocess.run(
        [sys.executable, str(script), "--approval", str(approval_path),
         "--run-dir", str(run_dir), "--validate-only"],
        capture_output=True, text=True,
    )


def _run_survivals(script: Path):
    return subprocess.run([sys.executable, str(script)], capture_output=True, text=True)


# ── Generic helpers ────────────────────────────────────────────────────────


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_file_artifact(errs: list[str], label: str, rec: Any, merged_sha: str | None,
                          extra_fields: tuple[str, ...] = ()) -> Path | None:
    """Validates one artifact record's generic shape (path/sha256/command/
    exit_code/candidate_sha [+ extra_fields]) and, if the file exists,
    returns its Path so callers can go on to parse/validate its CONTENT even
    when some other aspect (stale candidate_sha, nonzero exit_code, sha256
    drift) already failed — maximizing how much real evidence a single
    `--verify` run surfaces, and letting content-level checks (e.g. "this
    lane actually recorded a failing node") fire independently of whichever
    generic field also happens to be wrong."""
    if not isinstance(rec, dict):
        errs.append(f"{label}: not an object")
        return None
    missing = [f for f in (*_GENERIC_ARTIFACT_FIELDS, *extra_fields) if f not in rec]
    if missing:
        errs.append(f"{label}: missing field(s) {missing}")
    if "path" not in rec:
        return None
    p = Path(rec["path"])
    if not p.is_file():
        errs.append(f"{label}: artifact missing {p}")
        return None
    if "command" in rec and (not isinstance(rec.get("command"), list) or not rec["command"]):
        errs.append(f"{label}: producer command missing/empty")
    if "exit_code" in rec and rec.get("exit_code") != 0:
        errs.append(f"{label}: producer exit_code != 0 ({rec.get('exit_code')!r})")
    if "candidate_sha" in rec and rec.get("candidate_sha") != merged_sha:
        errs.append(f"{label}: stale candidate_sha ({rec.get('candidate_sha')!r} != {merged_sha!r})")
    if "sha256" in rec:
        digest = _sha256_file(p)
        if digest != rec["sha256"]:
            errs.append(f"{label}: sha256 mismatch (recorded={rec['sha256']} recomputed={digest})")
    return p


def _check_e2e_run_dir(errs: list[str], rec: Any, merged_sha: str | None) -> Path | None:
    """Like `_check_file_artifact` but for `e2e_run_dir`, whose `path` is a
    DIRECTORY (a whole evidence tree, not a single file) — `sha256` is
    defined as the hash of that directory's `summary.json`."""
    if not isinstance(rec, dict):
        errs.append("e2e_run_dir: not an object")
        return None
    required = (*_GENERIC_ARTIFACT_FIELDS, "approval_path", "approval_sha256")
    missing = [f for f in required if f not in rec]
    if missing:
        errs.append(f"e2e_run_dir: missing field(s) {missing}")
    if "path" not in rec:
        return None
    p = Path(rec["path"])
    if not p.is_dir():
        errs.append(f"e2e_run_dir: run dir missing {p}")
        return None
    if "exit_code" in rec and rec.get("exit_code") != 0:
        errs.append(f"e2e_run_dir: producer exit_code != 0 ({rec.get('exit_code')!r})")
    if "candidate_sha" in rec and rec.get("candidate_sha") != merged_sha:
        errs.append(f"e2e_run_dir: stale candidate_sha ({rec.get('candidate_sha')!r} != {merged_sha!r})")
    summary_path = p / "summary.json"
    if not summary_path.is_file():
        errs.append("e2e_run_dir: missing summary.json")
        return None
    if "sha256" in rec:
        digest = _sha256_file(summary_path)
        if digest != rec["sha256"]:
            errs.append(f"e2e_run_dir: summary.json sha256 mismatch "
                        f"(recorded={rec['sha256']} recomputed={digest})")
    return p


def _known_image_ids(prov_rec: Any) -> set[str]:
    """Best-effort read of the image_provenance artifact's own `images` list,
    used only to cross-check lane/secret-scan `image_id` values against it.
    Never appends errors itself (the dedicated image_provenance check does
    that) — returns an empty set on any problem so callers skip the
    cross-check rather than double-report."""
    if not isinstance(prov_rec, dict) or "path" not in prov_rec:
        return set()
    try:
        content = json.loads(Path(prov_rec["path"]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    images = content.get("images") if isinstance(content, dict) else None
    if not isinstance(images, list):
        return set()
    return {im["image_id"] for im in images if isinstance(im, dict) and "image_id" in im}


def _check_lane_entries(errs: list[str], label: str, kind: str, entries: Any) -> None:
    if not isinstance(entries, list):
        errs.append(f"{label}: {kind} not a list")
        return
    for e in entries:
        if not isinstance(e, dict) or "nodeid" not in e:
            errs.append(f"{label}: malformed {kind} entry {e!r}")
            continue
        if e.get("expected") is not True:
            errs.append(f"{label}: unexpected {kind}: {e['nodeid']}")


# ── Per-field verification ─────────────────────────────────────────────────


def _verify_merged_sha(errs: list[str], m: dict, repo_root: Path,
                        git_rev_parse_head: Callable[[Path], str]) -> str | None:
    merged_sha = m.get("merged_sha")
    if not (isinstance(merged_sha, str) and merged_sha):
        if "merged_sha" in m:
            errs.append("merged_sha: empty/invalid")
        return None
    try:
        head = git_rev_parse_head(repo_root)
    except Exception as exc:  # noqa: BLE001 — surfaced as a FAIL, not a crash
        errs.append(f"git rev-parse HEAD failed: {exc}")
        return merged_sha
    if merged_sha != head:
        errs.append(f"merged_sha mismatch: {merged_sha} != {head}")
    return merged_sha


def _verify_merge_parents(errs: list[str], parents: Any) -> None:
    if not isinstance(parents, list) or len(parents) < 2:
        errs.append("merge_parents: expected a list of >=2 commit SHAs")
        return
    for p in parents:
        if not isinstance(p, str) or not _SHA_RE.match(p):
            errs.append(f"merge_parents: malformed SHA {p!r}")


def _verify_blocker_fix_shas(errs: list[str], shas: Any) -> None:
    if not isinstance(shas, list) or not shas:
        errs.append("blocker_fix_shas: expected a non-empty list of commit SHAs")
        return
    for s in shas:
        if not isinstance(s, str) or not _SHA_RE.match(s):
            errs.append(f"blocker_fix_shas: malformed SHA {s!r}")


def _verify_image_provenance(errs: list[str], m: dict, merged_sha: str | None,
                              docker_image_inspect: Callable[[str], str | None]) -> None:
    prov = m.get("image_provenance")
    image_ids = m.get("image_ids")
    images: list[dict] | None = None
    if prov is not None:
        p = _check_file_artifact(errs, "image_provenance", prov, merged_sha)
        if p is not None:
            try:
                content = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                errs.append(f"image_provenance: unreadable JSON: {exc}")
                content = None
            if isinstance(content, dict):
                candidate = content.get("images")
                if not isinstance(candidate, list) or not candidate:
                    errs.append("image_provenance: 'images' missing/empty")
                else:
                    images = candidate
            elif content is not None:
                errs.append("image_provenance: content is not a JSON object")

    prov_map: dict[str, str] = {}
    if images is not None:
        for im in images:
            if not isinstance(im, dict) or "tag" not in im or "image_id" not in im:
                errs.append(f"image_provenance: malformed image entry {im!r}")
                continue
            prov_map[im["tag"]] = im["image_id"]

    if isinstance(image_ids, dict) and images is not None:
        if prov_map != image_ids:
            errs.append("image_ids top-level dict disagrees with image_provenance.images")

    images_to_check: dict[str, str] = dict(prov_map)
    if isinstance(image_ids, dict):
        for tag, iid in image_ids.items():
            images_to_check.setdefault(tag, iid)

    for tag, recorded_id in images_to_check.items():
        actual = docker_image_inspect(tag)
        if actual is None:
            errs.append(f"image provenance: tag {tag!r} no longer resolves (docker image inspect failed)")
        elif actual != recorded_id:
            errs.append(f"image provenance drift for {tag!r}: recorded={recorded_id!r} actual={actual!r}")


def _verify_lanes(errs: list[str], lanes: Any, merged_sha: str | None, image_prov_rec: Any) -> None:
    if not isinstance(lanes, list) or not lanes:
        errs.append("lanes_artifacts: expected a non-empty list")
        return
    known_ids = _known_image_ids(image_prov_rec)
    for i, rec in enumerate(lanes):
        label = f"lanes_artifacts[{i}]"
        p = _check_file_artifact(errs, label, rec, merged_sha)
        if p is None:
            continue
        try:
            content = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errs.append(f"{label}: unreadable JSON: {exc}")
            continue
        if not isinstance(content, dict):
            errs.append(f"{label}: content is not a JSON object (placeholder artifact rejected)")
            continue
        missing = [k for k in _LANE_CONTENT_KEYS if k not in content]
        if missing:
            errs.append(f"{label}: content missing keys {missing}")
            continue
        if not isinstance(content["collected"], list) or not content["collected"]:
            errs.append(f"{label}: collected node IDs empty")
        if content["source_sha"] != merged_sha:
            errs.append(f"{label}: source_sha stale ({content['source_sha']!r} != {merged_sha!r})")
        failures = content["failures"]
        if not isinstance(failures, list):
            errs.append(f"{label}: failures not a list")
        elif failures:
            errs.append(f"{label}: {len(failures)} failing node(s): {failures}")
        for kind in ("skips", "xfails", "deselected"):
            _check_lane_entries(errs, label, kind, content[kind])
        image_id = content["image_id"]
        if image_id is not None and known_ids and image_id not in known_ids:
            errs.append(f"{label}: image_id {image_id!r} not among recorded image_provenance images")
        db_identity = content["db_identity"]
        if db_identity is not None and not isinstance(db_identity, dict):
            errs.append(f"{label}: db_identity malformed")


def _verify_secret_scans(errs: list[str], scans: Any, merged_sha: str | None, image_prov_rec: Any) -> None:
    if not isinstance(scans, list) or not scans:
        errs.append("secret_scan_artifacts: expected a non-empty list")
        return
    known_ids = _known_image_ids(image_prov_rec)
    for i, rec in enumerate(scans):
        label = f"secret_scan_artifacts[{i}]"
        p = _check_file_artifact(errs, label, rec, merged_sha)
        if p is None:
            continue
        try:
            content = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errs.append(f"{label}: unreadable JSON: {exc}")
            continue
        if not isinstance(content, dict):
            errs.append(f"{label}: content is not a JSON object (placeholder artifact rejected)")
            continue
        required_keys = ("image_tag", "image_id", "categories", "export_member_count",
                         "scanned_bytes", "command", "exit_code")
        missing = [k for k in required_keys if k not in content]
        if missing:
            errs.append(f"{label}: content missing keys {missing}")
            continue
        if content["exit_code"] != 0:
            errs.append(f"{label}: scan exit_code != 0 ({content['exit_code']!r})")
        if rec.get("exit_code") != content["exit_code"]:
            errs.append(f"{label}: manifest record exit_code disagrees with scan content exit_code")
        if not isinstance(content["export_member_count"], int) or content["export_member_count"] <= 0:
            errs.append(f"{label}: export_member_count not a positive int")
        if not isinstance(content["scanned_bytes"], int) or content["scanned_bytes"] <= 0:
            errs.append(f"{label}: scanned_bytes not a positive int")
        if known_ids and content["image_id"] not in known_ids:
            errs.append(f"{label}: image_id {content['image_id']!r} not among recorded image_provenance images")

        categories = content["categories"]
        if not isinstance(categories, dict):
            errs.append(f"{label}: categories not an object")
            continue
        missing_cats = [c for c in _SECRET_SCAN_CATEGORIES if c not in categories]
        if missing_cats:
            errs.append(f"{label}: missing secret-scan categories {missing_cats}")
        for cat_name in _SECRET_SCAN_CATEGORIES:
            if cat_name not in categories:
                continue
            cat = categories[cat_name]
            if not isinstance(cat, dict) or "hits" not in cat or "allowlist" not in cat:
                errs.append(f"{label}/{cat_name}: malformed category")
                continue
            hits, allow = cat["hits"], cat["allowlist"]
            if not isinstance(hits, list) or not isinstance(allow, list):
                errs.append(f"{label}/{cat_name}: hits/allowlist not lists")
                continue
            allow_matches: set[str] = set()
            for a in allow:
                match = a.get("match") if isinstance(a, dict) else None
                if not isinstance(match, str) or match in _BROAD_ALLOWLIST_MATCHES or len(match) < 4:
                    errs.append(f"{label}/{cat_name}: overly broad or invalid allowlist entry {match!r}")
                    continue
                allow_matches.add(match)
            for h in hits:
                hm = h.get("match") if isinstance(h, dict) else None
                if hm not in allow_matches:
                    errs.append(f"{label}/{cat_name}: un-allowlisted secret hit {hm!r}")


def _verify_migration_evidence(errs: list[str], rec: Any, merged_sha: str | None) -> None:
    p = _check_file_artifact(errs, "migration_evidence", rec, merged_sha)
    if p is None:
        return
    try:
        content = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errs.append(f"migration_evidence: unreadable JSON: {exc}")
        return
    if not isinstance(content, dict):
        errs.append("migration_evidence: content is not a JSON object (placeholder artifact rejected)")
        return
    missing = [k for k in _MIGRATION_CONTENT_KEYS if k not in content]
    if missing:
        errs.append(f"migration_evidence: content missing keys {missing}")
        return

    required_tables, present_tables = content["required_tables"], content["present_tables"]
    if not isinstance(required_tables, list) or not isinstance(present_tables, list):
        errs.append("migration_evidence: required_tables/present_tables must be lists")
    else:
        gap = sorted(set(required_tables) - set(present_tables))
        if gap:
            errs.append(f"migration_evidence: required table(s) missing from candidate: {gap}")

    if content["migration_0007_present"] is not True:
        errs.append("migration_evidence: migration 0007 row not confirmed present")

    fks = content["foreign_keys"]
    if not isinstance(fks, list) or not fks:
        errs.append("migration_evidence: no foreign_keys recorded")
    else:
        for fk in fks:
            if (not isinstance(fk, dict) or fk.get("present") is not True
                    or not fk.get("table") or not fk.get("constraint")):
                errs.append(f"migration_evidence: FK not confirmed present: {fk!r}")

    charsets = content["charset_equality"]
    if not isinstance(charsets, list) or not charsets:
        errs.append("migration_evidence: no charset_equality entries recorded")
    else:
        for ce in charsets:
            if not isinstance(ce, dict):
                errs.append(f"migration_evidence: malformed charset_equality entry {ce!r}")
                continue
            if ce.get("child_charset") != ce.get("parent_charset"):
                errs.append(
                    "migration_evidence: charset mismatch "
                    f"{ce.get('child_table')}={ce.get('child_charset')!r} vs "
                    f"{ce.get('parent_table')}={ce.get('parent_charset')!r}"
                )

    second = content["second_migrate_output"]
    if not isinstance(second, str) or "No migrations to apply" not in second:
        errs.append("migration_evidence: second migrate not confirmed idempotent "
                     "('No migrations to apply' not found in second_migrate_output)")

    excerpt = content.get("error_log_excerpt")
    if isinstance(excerpt, str):
        for bad in ("3780", "1050"):
            if bad in excerpt:
                errs.append(f"migration_evidence: error log excerpt contains MySQL error {bad}")


def _verify_db_backup(errs: list[str], m: dict, merged_sha: str | None) -> None:
    rec = m.get("db_backup")
    _check_file_artifact(errs, "db_backup", rec, merged_sha, extra_fields=("container_id",))
    if isinstance(rec, dict) and "db_backup_sha256" in m and m.get("db_backup_sha256") != rec.get("sha256"):
        errs.append("db_backup_sha256: top-level value disagrees with db_backup.sha256")


def _verify_survivals(errs: list[str], rec: Any, merged_sha: str | None,
                       survivals_script: Path, run_survivals: Callable) -> None:
    _check_file_artifact(errs, "survivals_output", rec, merged_sha)
    result = run_survivals(survivals_script)
    if getattr(result, "returncode", 1) != 0:
        errs.append("survival assertions fail on the candidate (verify_merge_survivals.py re-run)")


def _verify_e2e(errs: list[str], m: dict, merged_sha: str | None,
                 full_ui_e2e_script: Path, run_full_ui_e2e_validate: Callable) -> None:
    rec = m.get("e2e_run_dir")
    run_dir = _check_e2e_run_dir(errs, rec, merged_sha)
    if run_dir is None:
        return
    approval_path = Path(rec["approval_path"]) if "approval_path" in rec else None
    if approval_path is None or not approval_path.is_file():
        errs.append(f"e2e_run_dir: approval artifact missing {approval_path}")
        return
    approval_digest = _sha256_file(approval_path)
    if "approval_sha256" in rec and approval_digest != rec["approval_sha256"]:
        errs.append("e2e_run_dir: approval_sha256 mismatch against approval artifact on disk "
                     "(approval hash drift)")
    top_hash = m.get("e2e_approval_sha256")
    if "approval_sha256" in rec and top_hash != rec.get("approval_sha256"):
        errs.append("e2e_approval_sha256: top-level value disagrees with e2e_run_dir.approval_sha256")
    if top_hash != approval_digest:
        errs.append("e2e_approval_sha256: top-level value disagrees with the approval artifact's "
                     "real sha256 on disk (approval hash drift)")

    result = run_full_ui_e2e_validate(full_ui_e2e_script, approval_path, run_dir)
    if getattr(result, "returncode", 1) != 0:
        stderr_tail = (getattr(result, "stderr", "") or "")[-500:]
        errs.append(
            f"full_ui_e2e --validate-only failed (exit {getattr(result, 'returncode', '?')}): {stderr_tail}"
        )
    # summary.json may be read for CONTEXT ONLY — its own verdict is never
    # the pass/fail signal; the subprocess exit code above is authoritative.
    try:
        json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass


# ── Top-level entry points ─────────────────────────────────────────────────


def generate(out: Path, fields: dict) -> None:
    """Assemble and write a DoD manifest from already-produced evidence
    records. `fields` must supply the FIELDS above shaped per the schema this
    module's `verify()` checks (see module docstring) — real evidence
    collection is Task 17's job; this function only stamps `schema_version`
    and serializes deterministically. It does NOT itself validate `fields` —
    running `--verify` immediately afterward is the intended workflow, and is
    exactly how a manifest is proven trustworthy."""
    manifest = {"schema_version": SCHEMA_VERSION, **fields}
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def verify(
    manifest_path: Path,
    *,
    repo_root: Path = REPO_ROOT,
    full_ui_e2e_script: Path = DEFAULT_FULL_UI_E2E,
    survivals_script: Path = DEFAULT_SURVIVALS,
    git_rev_parse_head: Callable[[Path], str] | None = None,
    docker_image_inspect: Callable[[str], str | None] | None = None,
    run_full_ui_e2e_validate: Callable | None = None,
    run_survivals: Callable | None = None,
) -> tuple[bool, list[str]]:
    """RE-CHECKS every claim in the manifest at `manifest_path` by
    recomputing it independently (never by trusting the manifest's own
    fields, and never by trusting an artifact's own embedded verdict).
    Returns `(ok, errs)` — `ok` is `not errs`. See the module docstring for
    the full list of what's re-verified and how the 4 "reach outside this
    process" calls are made injectable."""
    git_rev_parse_head = git_rev_parse_head or _git_rev_parse_head
    docker_image_inspect = docker_image_inspect or _docker_image_inspect
    run_full_ui_e2e_validate = run_full_ui_e2e_validate or _run_full_ui_e2e_validate
    run_survivals = run_survivals or _run_survivals

    errs: list[str] = []
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, [f"manifest unreadable: {exc}"]
    try:
        m = json.loads(raw)
    except json.JSONDecodeError as exc:
        return False, [f"manifest is not valid JSON: {exc}"]
    if not isinstance(m, dict):
        return False, ["manifest is not a JSON object"]

    for f in FIELDS:
        if f not in m:
            errs.append(f"missing field: {f}")

    merged_sha = _verify_merged_sha(errs, m, repo_root, git_rev_parse_head)

    if "merge_parents" in m:
        _verify_merge_parents(errs, m["merge_parents"])
    if "blocker_fix_shas" in m:
        _verify_blocker_fix_shas(errs, m["blocker_fix_shas"])
    if "image_provenance" in m or "image_ids" in m:
        _verify_image_provenance(errs, m, merged_sha, docker_image_inspect)
    if "lanes_artifacts" in m:
        _verify_lanes(errs, m["lanes_artifacts"], merged_sha, m.get("image_provenance"))
    if "secret_scan_artifacts" in m:
        _verify_secret_scans(errs, m["secret_scan_artifacts"], merged_sha, m.get("image_provenance"))
    if "migration_evidence" in m:
        _verify_migration_evidence(errs, m["migration_evidence"], merged_sha)
    if "db_backup" in m:
        _verify_db_backup(errs, m, merged_sha)
    if "ledger" in m:
        _check_file_artifact(errs, "ledger", m["ledger"], merged_sha)
    if "host_only_allowlist_output" in m:
        _check_file_artifact(errs, "host_only_allowlist_output", m["host_only_allowlist_output"], merged_sha)
    if "survivals_output" in m:
        _verify_survivals(errs, m["survivals_output"], merged_sha, survivals_script, run_survivals)
    if "e2e_run_dir" in m:
        _verify_e2e(errs, m, merged_sha, full_ui_e2e_script, run_full_ui_e2e_validate)

    return (not errs), errs


# ── CLI ──────────────────────────────────────────────────────────────────


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", type=Path, help="manifest JSON path (write target for generate, read source for --verify)")
    ap.add_argument("--verify", action="store_true", help="re-check an existing manifest instead of generating one")
    ap.add_argument("--from", dest="from_json", type=Path,
                    help="(generate mode) JSON file of raw manifest field values to assemble")
    ap.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    ap.add_argument("--full-ui-e2e-script", type=Path, default=DEFAULT_FULL_UI_E2E)
    ap.add_argument("--survivals-script", type=Path, default=DEFAULT_SURVIVALS)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.verify:
        ok, errs = verify(
            args.manifest,
            repo_root=args.repo_root,
            full_ui_e2e_script=args.full_ui_e2e_script,
            survivals_script=args.survivals_script,
        )
        for e in errs:
            print("FAIL:", e)
        print("MANIFEST:", "PASS" if ok else "FAIL")
        return 0 if ok else 1

    if args.from_json is None:
        print("use Task 17 to generate a real manifest (no --from provided)")
        return 0
    fields = json.loads(args.from_json.read_text(encoding="utf-8"))
    generate(args.manifest, fields)
    print("WROTE:", args.manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
