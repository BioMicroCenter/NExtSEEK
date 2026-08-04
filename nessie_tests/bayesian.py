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


class PriorRunWouldBeOverwritten(RuntimeError):
    """`out_dir` already holds a paired manifest and this run is not a resume.

    Protects completed PAID pairs. Pairs are written as they complete, so a
    second non-resume run into the same directory replaces a finished record
    with its own first pair and everything after that point is unrecoverable.
    Reproduced at 130 pairs -> 2.
    """


class CorpusChanged(RuntimeError):
    """The corpus is not the one the run being resumed was selected from.

    Protects completed PAID pairs, and the comparability the fingerprint exists
    for. `manifest.pairs` is rebuilt from the CURRENT selection rather than
    merged with the prior pairs, so any id that left the selection loses its
    paid result silently -- and selection can only change if corpus.json did.
    """


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

    # BEFORE the preflight, and before anything is selected: a mistyped --out must
    # cost zero turns. Read first, decide, and only then spend. This guard lives in
    # `run_paired` rather than in the CLI because `run_paired` is importable on its
    # own and the damage happens here.
    existing = read_bayes_manifest(out_dir)
    if existing is not None and not resume:
        raise PriorRunWouldBeOverwritten(
            f"{out_dir} already holds a paired manifest with {len(existing.pairs)} "
            f"pair(s), and this run would rewrite it from its own first pair "
            f"onward.\nThose pairs were PAID FOR and are not recoverable once "
            f"overwritten.\nEither continue that run with --resume, or send this "
            f"one somewhere else with --out.")

    fresh_fingerprint = runner.corpus_fingerprint(corpus_path)
    prior = existing if resume else None
    if prior is not None:
        prior_fingerprint = prior.run_meta.get("corpus_fingerprint")
        if prior_fingerprint != fresh_fingerprint:
            # A missing fingerprint takes this path too. `run_paired` always
            # writes the key, so nothing it produced can land here: a manifest
            # without one was hand-edited or truncated. `preflight` makes the
            # same call on its own inconclusive case -- an UNPROVEN match is as
            # unsafe to resume a paid run onto as a refused one.
            raise CorpusChanged(
                f"the corpus is not the one this run was selected from: prior "
                f"fingerprint {prior_fingerprint!r}, current {fresh_fingerprint!r}.\n"
                f"Selection comes from corpus.json, so a changed corpus means a "
                f"changed selection -- and pairs are rebuilt from the CURRENT "
                f"selection, so resuming would silently DROP the paid result of "
                f"every id that left it. The two runs are also no longer "
                f"comparable, which is what the fingerprint is for.\n"
                f"Restore the corpus to resume, or start a fresh run in a new --out.")

    if not skip_preflight:
        # Before anything is spent. A dropped force makes the entire run measure
        # the router instead of the engines.
        preflight.assert_force_route_works(post_query, get_progress)

    selected = corpus.bayesian_ids(corpus_path)
    by_id = {v.id: v for v in corpus.merged(corpus_path)}

    done = completed_arms(prior) if prior else set()
    pairs = {p.id: p for p in (prior.pairs if prior else [])}

    manifest = BayesManifest(
        run_meta={
            "mode": "bayesian",
            "arms": list(ARMS),
            "corpus_fingerprint": fresh_fingerprint,
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
        # Appended ONCE, before either arm runs, so the per-arm writes below
        # persist this pair's partial state without ever duplicating it.
        manifest.pairs.append(pair)

        # Both arms for THIS question before moving on. Two passes would confound
        # engine with wall-clock time, and a provider outage during one pass would
        # read as a real engine effect the model cannot separate out.
        for arm in ARMS:
            if (vid, arm) in done:
                costs.append(getattr(pair, arm).cost if getattr(pair, arm) else None)
                continue
            if max_usd is not None and _spent(costs) >= max_usd:
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
            # Per ARM, not per pair. `completed_arms` is keyed on the arm so that
            # "a run interrupted between the NS and CC halves of one question must
            # not repay for the NS half" -- but writing only once both arms were
            # done meant no crash could ever persist a half pair, and that
            # machinery was decorative on the exact path it names. Measured: a
            # Ctrl-C on pair 2's cc arm paid for 3 arms and persisted 2.
            write_bayes_manifest(manifest, out_dir)

    # A run that resumed with nothing left to do never entered a write above, and
    # would otherwise return a manifest whose `run_meta` (`resumed`, `max_usd`,
    # `git_sha`) disagrees with the file on disk. One write makes "the file equals
    # what was returned" true unconditionally.
    write_bayes_manifest(manifest, out_dir)
    return manifest
