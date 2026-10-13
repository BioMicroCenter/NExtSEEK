"""Measure what Task 4 Step 7 was supposed to measure.

Three unmeasured constants depend on this:
  MAX_SELECTION_UIDS        = 75     (queried UIDs)
  DIGEST_TIMEOUT_SECONDS    = 90.0   (wall clock on build_sample_digest)
  SELECTION_MODEL_TIMEOUT_SECONDS = 240.0  (not measured here - needs a model call)

Also measures the expansion ratio the whole-branch review flagged: the cap counts
QUERIED uids, while MAX_RESOLVE_LEAVES (75) counts EXPANDED leaves. If the ratio is
far from 1, the two caps are not comparable and the comment saying otherwise is wrong.

Read-only: fetches metadata and downloads protocol documents. Writes nothing.
"""
import json
import sys
import time
from pathlib import Path

from chat_nextseek.config import ChatConfig
from chat_nextseek.pipeline.sample_digest import DigestError, build_sample_digest

COHORTS = Path("/app/NessieAI/chat_nextseek/evals/groundtruth_cohorts.json")


def leaf_count(digest):
    """Records the digest actually profiled, from its own inventory block."""
    inv = digest.get("input_data_inventory") or {}
    total = 0
    for key in ("raw_reads", "aligned_reads", "derived_products", "no_primary_data"):
        block = inv.get(key) or {}
        total += block.get("n_records", 0) if isinstance(block, dict) else 0
    return total


def main():
    cohorts = json.loads(COHORTS.read_text())
    ranked = sorted(cohorts, key=lambda c: len(c["uids"]))
    # a spread across the real range, smallest -> largest
    picks, seen = [], set()
    for target in (1, 5, 20, 43, 68, 111, 176):
        best = min(ranked, key=lambda c: abs(len(c["uids"]) - target))
        key = f"{best['study']}::{best['analysis']}"
        if key not in seen:
            seen.add(key)
            picks.append(best)

    config = ChatConfig()
    print(f"{'uids':>5} {'secs':>8} {'records':>8} {'ratio':>6}  cohort / protocol status")
    print("-" * 96)
    rows = []
    for c in picks:
        uids = sorted(c["uids"])
        label = f"{c['study']}::{c['analysis']}"
        t0 = time.perf_counter()
        try:
            d = build_sample_digest(config, uids)
            dt = time.perf_counter() - t0
            n = leaf_count(d)
            ratio = (n / len(uids)) if uids else 0
            status = (d.get("protocol_text_status") or {})
            note = f"{status.get('n_readable', 0)}/{status.get('n_protocols', 0)} protocols readable"
            rows.append((len(uids), dt, n, ratio))
            print(f"{len(uids):>5} {dt:>8.1f} {n:>8} {ratio:>6.1f}  {label} | {note}")
        except DigestError as exc:
            dt = time.perf_counter() - t0
            print(f"{len(uids):>5} {dt:>8.1f} {'-':>8} {'-':>6}  {label} | DigestError: {exc}")
        except Exception as exc:
            dt = time.perf_counter() - t0
            print(f"{len(uids):>5} {dt:>8.1f} {'-':>8} {'-':>6}  {label} | {type(exc).__name__}: {exc}")
        sys.stdout.flush()

    if rows:
        slowest = max(rows, key=lambda r: r[1])
        ratios = [r[3] for r in rows if r[3]]
        print("-" * 96)
        print(f"slowest: {slowest[1]:.1f}s at {slowest[0]} uids "
              f"(DIGEST_TIMEOUT_SECONDS = 90.0)")
        print(f"expansion ratio records/uid: min {min(ratios):.1f}  max {max(ratios):.1f}  "
              f"(MAX_SELECTION_UIDS = 75 counts uids; MAX_RESOLVE_LEAVES = 75 counts leaves)")


if __name__ == "__main__":
    main()
