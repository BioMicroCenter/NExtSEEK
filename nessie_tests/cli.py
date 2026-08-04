from __future__ import annotations
import argparse
import urllib.error
from pathlib import Path
from nessie_tests import runner, http_driver

_CORPUS = Path(__file__).resolve().parent / "corpus.json"


EXIT_CODES = """exit codes
  0  the run completed
  1  a normal run had real failures (--bayesian never returns this: in a paired
     run a wrong answer is the measurement, not a gate failure)
  2  bad arguments. Owned by argparse, which is why no abort below reuses it:
     a wrapper that retried "the budget code" would loop forever on a typo.
  3  --bayesian: the budget ceiling was reached. MONEY WAS SPENT and every
     completed arm is on disk; rerun with a higher --max-usd and --resume.
  4  --bayesian: refused, --out already holds a paired run. Nothing was billed.
  5  --bayesian: refused, the server did not honour force_route. Nothing was billed.
  6  --bayesian: refused, the corpus changed under a --resume. Nothing was billed.
  7  --bayesian: could not talk to --base-url. Any completed arms are on disk.
"""

# The default per-turn deadline, named so `main` can tell "the operator asked for
# 900s" from "argparse supplied the default" without a second sentinel value.
FULL_TIMEOUT_DEFAULT_S = 600.0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nessie_tests", description="Router-aware assistant e2e harness",
                                epilog=EXIT_CODES,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", required=True, help="e.g. http://localhost:8000")
    p.add_argument("--tier", choices=["route", "full"], default="route")
    p.add_argument("--scope", choices=["specific", "all"], default="specific")
    p.add_argument("--family", default=None)
    p.add_argument("--variant", default=None)
    p.add_argument("--user", default="demo")
    p.add_argument("--password", default="demopassword")
    p.add_argument("--pace", type=float, default=0.0,
                   help="Seconds to sleep between the TURNS of one multi-turn case. It does "
                        "not pace between cases, and under --bayesian it does not pace "
                        "between the two arms of a pair or between pairs.")
    p.add_argument("--consistency", action="store_true", default=False,
                   help="Run the #33 consistency groups (auto-on for --tier full)")
    p.add_argument("--sample", type=float, default=1.0,
                   help="Fraction of selected variants to run, sampled per family (e.g. 0.1 for a tenth). Default 1.0 = all.")
    p.add_argument("--seed", type=int, default=0, help="Deterministic sampling seed.")
    p.add_argument("--out", type=Path, default=None,
                   help="Output directory. Default nessie_out, or nessie_out_bayes under "
                        "--bayesian, which keeps a paired run out of a normal run's directory.")
    p.add_argument("--bayesian", action="store_true", default=False,
                   help="PAID, ~260 turns. Paired dual-route run over the corpus's is_bayesian "
                        "selection (130 variants today): each one is driven down BOTH engines, "
                        "NS then CC, interleaved per question, with the router forced out. "
                        "Full depth, every case, no sampling. Needs a STAFF account, since "
                        "force_route is silently dropped for anyone else. Budget it with "
                        "--max-usd and resume it with --resume.")
    p.add_argument("--max-usd", type=float, default=None,
                   help="--bayesian only. Run-level USD ceiling, cumulative across resumes. "
                        "Aborts cleanly before the arm that would breach it, keeping every "
                        "completed arm; exit 3. Only container_cc reports cost, so NS spend "
                        "is invisible to this ceiling and the real total is higher.")
    p.add_argument("--resume", action="store_true", default=False,
                   help="--bayesian only. Continue the paired run in --out: every (variant, arm) "
                        "already recorded there is skipped rather than repaid.")
    p.add_argument("--full-timeout", type=float, default=FULL_TIMEOUT_DEFAULT_S,
                   help="--bayesian only. Per-turn deadline in seconds for full-depth turns.")
    return p


def _run_bayesian(a, auth) -> int:
    """The paired dual-route run. Split out of `main` so the four abort paths read
    as one unit and `main` stays a dispatcher over two unrelated run shapes."""
    # `is_bayesian` IS the selection. Accepting a second selection source would
    # make "what ran" depend on two things at once.
    #
    # --family and --variant are in this list even though the plan omitted them:
    # --scope defaults to "specific", so `--bayesian --family reporting` cleared
    # the whole check and was then SILENTLY IGNORED. --cases is not, because this
    # branch's parser has no such flag (run_suite takes cases_path; nothing
    # exposes it) and argparse already rejects it as unrecognized.
    conflicting = [name for name, val in (
        ("--tier", a.tier != "route"), ("--scope", a.scope != "specific"),
        ("--sample", a.sample != 1.0), ("--seed", a.seed != 0),
        ("--family", a.family is not None), ("--variant", a.variant is not None),
    ) if val]
    if conflicting:
        build_parser().error(
            f"--bayesian selects on the corpus's is_bayesian flag and cannot be "
            f"combined with {', '.join(conflicting)}.")

    from nessie_tests import bayes_manifest, bayesian, preflight
    try:
        m = bayesian.run_paired(
            base_url=a.base_url, auth_header=auth, out_dir=a.out,
            # Named, not left to `run_paired`'s default, so both run shapes
            # resolve the corpus by the same rule. The paired run fingerprints
            # this file and refuses a `--resume` onto a different one.
            corpus_path=_CORPUS,
            max_usd=a.max_usd, resume=a.resume,
            full_timeout_s=a.full_timeout, pace_s=a.pace)
    # Five aborts, five exit codes, none of them 0, 1 or 2. They share nothing an
    # operator would act on: the first spent real money and left resumable work on
    # disk, the next three refused before a single turn was billed, and each has a
    # different remedy. A single code would force a wrapper script to parse English
    # out of stdout to tell "raise the ceiling and continue" from "you are on the
    # wrong account" -- and 2 in particular is argparse's own usage error, so a
    # wrapper that retried on "the budget code" would loop forever on a mistyped
    # flag if the budget code were 2.
    except bayesian.BudgetExceeded as e:
        print("nessie: budget ceiling reached, run stopped (exit 3).")
        print(f"nessie: {e}")
        print(f"nessie: {a.out}/{bayes_manifest.MANIFEST_NAME} holds every completed "
              f"arm. Rerun the SAME command with a higher --max-usd and --resume; "
              f"completed arms are skipped, not repaid.")
        return 3
    except bayesian.PriorRunWouldBeOverwritten as e:
        print("nessie: refused, nothing was billed (exit 4).")
        print(f"nessie: {e}")
        return 4
    except preflight.ForceRouteRejected as e:
        print("nessie: preflight refused the run, nothing was billed (exit 5).")
        print(f"nessie: {e}")
        return 5
    except bayesian.CorpusChanged as e:
        print("nessie: refused the resume, nothing was billed (exit 6).")
        print(f"nessie: {e}")
        return 6
    # The likeliest first-run failure of all is a wrong port, and it is raised by
    # urllib inside the preflight's own POST -- before any harness guard can see
    # it. Uncaught it reached the operator as fifteen lines of urllib frames under
    # exit 1, the code that means "a normal run had real failures". HTTPError is a
    # URLError subclass, so a 500 or a 403 lands here too and prints its own status.
    except urllib.error.URLError as e:
        print("nessie: could not talk to the endpoint, run stopped (exit 7).")
        print(f"nessie: {a.base_url}: {e}")
        print(f"nessie: check the stack is up and --base-url is right. Any arms "
              f"that did complete are in {a.out}/{bayes_manifest.MANIFEST_NAME} "
              f"and --resume will skip them.")
        return 7

    both = sum(1 for p in m.pairs if p.ns and p.cc)
    print(f"nessie: {both}/{len(m.pairs)} complete pairs "
          f"({2 * len(m.pairs)} arms); manifest → {a.out}/{bayes_manifest.MANIFEST_NAME}")
    return 0


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    # Resolved here rather than as an argparse default because it depends on
    # another flag. A paired run gets its own directory: its manifest, its report
    # and a normal run's are three different schemas that must not share a home.
    if a.out is None:
        a.out = Path("nessie_out_bayes" if a.bayesian else "nessie_out")
    auth = http_driver.basic_auth(a.user, a.password)

    if a.bayesian:
        return _run_bayesian(a, auth)

    # The mirror of `_run_bayesian`'s mutual exclusion. `run_suite` has no budget
    # ceiling, no resume and no per-turn deadline parameter, so silently accepting
    # these would leave an operator believing a spending cap is in force on a paid
    # full-tier run while nothing at all is capped.
    paired_only = [name for name, val in (
        ("--max-usd", a.max_usd is not None), ("--resume", a.resume),
        ("--full-timeout", a.full_timeout != FULL_TIMEOUT_DEFAULT_S),
    ) if val]
    if paired_only:
        build_parser().error(
            f"{', '.join(paired_only)} only applies to --bayesian; a normal run has "
            f"no budget ceiling, no resume and no per-turn deadline.")

    bundle_reader = None
    if a.tier == "full":
        from nessie_tests.bundle import summary_for_session
        bundle_reader = summary_for_session
    run_consistency = a.consistency or (a.tier == "full")
    manifest = runner.run_suite(
        base_url=a.base_url, auth_header=auth, tier=a.tier, scope=a.scope,
        family=a.family, variant_id=a.variant, corpus_path=_CORPUS,
        out_dir=a.out, bundle_reader=bundle_reader, pace_s=a.pace,
        run_consistency=run_consistency, sample=a.sample, seed=a.seed)
    summary = runner.classify_entries(manifest)
    fails = runner.gate_failed(manifest)
    # Outages get their own clause rather than vanishing: they are excluded from
    # `real failures` by design, and a run that quietly lost ten cases to a dead
    # provider must not read as a run that tested them.
    outaged = (f", {len(summary['outage'])} lost to a provider outage (not scored)"
               if summary["outage"] else "")
    # ...and so do the cases that asserted nothing. They ARE inside `fails` by
    # design, but "1 real failure" with no qualifier reads as a product
    # regression, and a case that evaluated zero criteria is corpus drift — a
    # different triage entirely.
    vacuous = (f", {summary['counts']['no_assertions']} asserted nothing "
               f"(counted as failures)" if summary["counts"]["no_assertions"] else "")
    # Cost is printed from the single preformatted string so this line, the HTML
    # report and `manage.py nessie` cannot describe the same run differently. It
    # says `unmeasured` when nothing was observed: a route-tier run keeps billing
    # after the poll loop breaks (http_driver.py:96-98 vs cc_assistant.py:352-366),
    # so `$0.00` there is a claim the harness cannot support.
    print(f"nessie: {len(manifest.entries)} cases, {fails} real failures{outaged}{vacuous} "
          f"(tier={a.tier} scope={a.scope}); cost {summary['cost_display']}; "
          f"report → {a.out}/report.html")
    return 1 if fails else 0
