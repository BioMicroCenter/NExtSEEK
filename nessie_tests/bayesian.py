"""Paired dual-route evaluation: every selected variant through BOTH engines.

Selection is `corpus.bayesian_ids()` and nothing else. No tier, no scope, no
sample, no seed: one flag, one source, so "what ran" always has exactly one
answer. `--cases` already refuses to mix selection sources for the same reason.
"""
from __future__ import annotations

import time

from nessie_tests import corpus, http_driver, preflight, runner
from nessie_tests.bayes_manifest import (
    BayesManifest, BayesPair, completed_arms, read_bayes_manifest, write_bayes_manifest,
)

ARMS = ("ns", "cc")


class BudgetExceeded(RuntimeError):
    """The run-level USD ceiling was reached. Resume with --resume after raising it."""


def _spent(costs) -> float:
    """Sum observed costs, skipping unobserved ones.

    `None` is NOT zero. Only container_cc emits `total_cost_usd` at all, so an NS
    arm always contributes `None`; treating that as 0.0 would be an accounting
    claim the harness cannot support, and `manifest.cost_summary` already refuses
    to make it.
    """
    return sum(c for c in costs if c is not None)


def run_paired(*, base_url, auth_header, out_dir, corpus_path=None,
               post_query=None, get_progress=None, max_usd=None, resume=False,
               full_timeout_s=600.0, pace_s=0.0, skip_preflight=False,
               sleep=time.sleep, clock=time.monotonic) -> BayesManifest:
    if post_query is None or get_progress is None:
        post_query, get_progress = http_driver.make_default_clients(base_url, auth_header)

    if not skip_preflight:
        # Before anything is selected or spent. A dropped force makes the entire
        # run measure the router instead of the engines.
        preflight.assert_force_route_works(post_query, get_progress)

    selected = corpus.bayesian_ids(corpus_path)
    by_id = {v.id: v for v in corpus.merged(corpus_path)}

    prior = read_bayes_manifest(out_dir) if resume else None
    done = completed_arms(prior) if prior else set()
    pairs = {p.id: p for p in (prior.pairs if prior else [])}

    manifest = BayesManifest(
        run_meta={
            "mode": "bayesian",
            "arms": list(ARMS),
            "corpus_fingerprint": runner.corpus_fingerprint(corpus_path),
            "git_sha": runner.git_sha(),
            "base_url": base_url,
            "selected_ids": selected,
            "max_usd": max_usd,
            "resumed": bool(prior),
        },
        pairs=[],
    )

    costs: list[float | None] = []
    for vid in selected:
        v = by_id[vid]
        meta = corpus.hibayes_meta(vid, corpus_path)
        pair = pairs.get(vid) or BayesPair(
            id=vid, family=v.family, hibayes_subtype=meta["hibayes_subtype"])

        # Both arms for THIS question before moving on. Two passes would confound
        # engine with wall-clock time, and a provider outage during one pass would
        # read as a real engine effect the model cannot separate out.
        for arm in ARMS:
            if (vid, arm) in done:
                costs.append(getattr(pair, arm).cost if getattr(pair, arm) else None)
                continue
            if max_usd is not None and _spent(costs) >= max_usd:
                manifest.pairs.append(pair)
                write_bayes_manifest(manifest, out_dir)
                raise BudgetExceeded(
                    f"spent ${_spent(costs):.2f} of ${max_usd:.2f} before {vid}:{arm}. "
                    f"Raise --max-usd and rerun with --resume; completed arms are kept.")
            entry = runner.run_case(
                v, tier="full", post_query=post_query, get_progress=get_progress,
                pace_s=pace_s, force_route=arm, strip_route_criteria=True,
                full_timeout_s=full_timeout_s, sleep=sleep, clock=clock)
            setattr(pair, arm, entry)
            costs.append(entry.cost)

        manifest.pairs.append(pair)
        # Per pair, not at the end: a crash, a timeout or a Ctrl-C must leave a
        # resumable manifest rather than nothing.
        write_bayes_manifest(manifest, out_dir)

    return manifest
