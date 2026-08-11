"""Paired dual-route evaluation: every selected variant through BOTH engines.

Selection is `corpus.bayesian_ids()` and nothing else. No tier, no scope, no
sample, no seed: one flag, one source, so "what ran" always has exactly one
answer. `run_suite`'s `cases_path` makes the same call for the same reason: an
explicit running order replaces scope, family, variant, sample and seed outright
rather than combining with them. (It is `cases_path`, not `--cases`: no flag on
this branch's parser reaches it.)
"""
from __future__ import annotations

import time

from nessie_tests import corpus, http_driver, preflight, runner
from nessie_tests.bayes_manifest import (
    MANIFEST_NAME, BayesManifest, BayesPair, completed_arms, read_bayes_manifest,
    write_bayes_manifest,
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


class NoRunToResume(RuntimeError):
    """`--resume` was given but `out_dir` holds no paired manifest.

    The mirror of `PriorRunWouldBeOverwritten`, and the same defect class: that
    one refuses a fresh run onto a prior record, and nothing refused a resume
    onto NOTHING, which silently starts a fresh ~322-arm paid run.

    The likely route in is the budget abort's own advice -- "rerun the SAME
    command with a higher --max-usd and --resume" -- retyped without `--out`,
    which defaults to `nessie_out_bayes` rather than to the run's directory. The
    operator pays twice and the original run is never continued.
    """


class CorpusChanged(RuntimeError):
    """The corpus is not PROVABLY the one the run being resumed was selected from.

    Protects completed PAID pairs, and the comparability the fingerprint exists
    for. `manifest.pairs` is rebuilt from the CURRENT selection rather than
    merged with the prior pairs, so any id that left the selection loses its
    paid result silently -- and selection can only change if corpus.json did.

    Two routes here, one refusal and two different messages: the fingerprints
    disagree, or the prior manifest records none at all. Their remedies do not
    overlap, so they do not share wording.
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
    if existing is None and resume:
        # The other direction of the same guard. `--resume` is a promise that a
        # run exists here; if none does, honouring it silently would spend a
        # FULL fresh run while the paid arms it was meant to continue sit
        # untouched in the directory the operator meant to name.
        raise NoRunToResume(
            f"--resume was given but {out_dir} holds no paired manifest (no "
            f"{MANIFEST_NAME}), so there is nothing to continue -- this would "
            f"start a FRESH full paid run instead of finishing one.\n"
            f"The likeliest cause is a missing or mistyped --out. The budget "
            f"abort tells you to rerun the SAME command with a higher --max-usd "
            f"and --resume; dropping --out from that rerun sends it to the "
            f"default nessie_out_bayes/ rather than to your run's directory, and "
            f"the arms you already paid for are never continued.\n"
            f"Point --out at the directory holding {MANIFEST_NAME}, or drop "
            f"--resume if a fresh run really is what you want.")

    fresh_fingerprint = runner.corpus_fingerprint(corpus_path)
    prior = existing if resume else None
    if prior is not None:
        prior_fingerprint = prior.run_meta.get("corpus_fingerprint")
        if prior_fingerprint is None:
            # Its own message, not the drift one. `preflight` was split for
            # exactly this reason: the two conditions reach the same refusal by
            # different routes and have DIFFERENT remedies, and the drift
            # message's two exits are both wrong here -- the corpus did not
            # change, so there is nothing to "restore", and "start a fresh run"
            # costs a whole paid run to escape a missing JSON key.
            raise CorpusChanged(
                f"this manifest records NO corpus_fingerprint, so the corpus it "
                f"was selected from cannot be identified.\n"
                f"`run_paired` always writes that key, so nothing it produced can "
                f"land here: a manifest without one was hand-edited or truncated. "
                f"An UNPROVEN match is as unsafe to resume a paid run onto as a "
                f"refused one -- pairs are rebuilt from the CURRENT selection, so "
                f"any id that has since left it loses its paid result silently. "
                f"`preflight` makes the same call on its own inconclusive case.\n"
                f"If you know these pairs came from the corpus in this checkout, "
                f"add \"corpus_fingerprint\": {fresh_fingerprint!r} to run_meta in "
                f"{out_dir}/{MANIFEST_NAME} and resume. Do NOT delete the "
                f"manifest to clear this: that repays for every arm on disk.")
        if prior_fingerprint != fresh_fingerprint:
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
        # the router instead of the engines, and an image with no `ns_run_root`
        # loses the join key on all 127 NS arms.
        #
        # `sleep`/`clock` are threaded exactly as they are into `run_case` below:
        # the preflight now polls its probe turn to completion, so a test driving
        # `run_paired` past the force check with the real clock would block for
        # the whole timeout.
        #
        # The timeout IS `--full-timeout`. The probe is a real NS turn, so the
        # operator's own per-turn ceiling is what governs it; a hardcoded 600s
        # under `--full-timeout 900` refuses a healthy run as INCONCLUSIVE and
        # aborts a run that would have succeeded.
        preflight.assert_force_route_works(
            post_query, get_progress, sleep=sleep, clock=clock,
            ns_run_root_timeout_s=full_timeout_s)

    selected = corpus.bayesian_ids(corpus_path)
    by_id = {v.id: v for v in corpus.merged(corpus_path)}

    done = completed_arms(prior) if prior else set()
    pairs = {p.id: p for p in (prior.pairs if prior else [])}

    # A resumed run's arms were NOT all produced by the build recorded below.
    # `run_meta` is rebuilt from scratch every time, so a resume after a rebuild
    # silently restated one `git_sha` and one `base_url` for a two-build run --
    # the inverse of the honesty `corpus_fingerprint` is guarded for, which stops
    # the QUESTIONS changing mid-run while nothing recorded that the thing being
    # MEASURED had. A changed sha does not raise: unlike a corpus edit, finishing
    # a run after a rebuild is legitimate and sometimes the only way to finish it.
    # It is made visible instead, oldest segment first, flattened so a reader gets
    # one list rather than a chain of nested manifests: every build and base_url
    # that contributed arms is `[m["git_sha"] for m in superseded] + [git_sha]`.
    # Always present, `[]` on a fresh run, so plan 3 need not special-case it.
    superseded = []
    if prior is not None:
        prior_meta = dict(prior.run_meta)
        superseded = list(prior_meta.pop("superseded_runs", []))
        superseded.append(prior_meta)

    manifest = BayesManifest(
        schema_version="bayes_manifest/v1",
        run_meta={
            "mode": "bayesian",
            "arms": list(ARMS),
            "corpus_fingerprint": fresh_fingerprint,
            "git_sha": runner.git_sha(),
            "base_url": base_url,
            "selected_ids": selected,
            "max_usd": max_usd,
            "resumed": bool(prior),
            "superseded_runs": superseded,
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
