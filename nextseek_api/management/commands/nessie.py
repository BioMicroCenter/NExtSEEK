"""``manage.py nessie`` — run the Nessie router-aware assistant test harness.

Runs the ``nessie_tests`` harness (see ``NessieAI/tests/nessie_tests/README.md``) from inside
the trusted Django process, so the full-tier bundle reader (which imports Django
models) works with no separate ``django.setup()`` bootstrap.

    docker exec nextseek uv run manage.py nessie --tier route              # cheap route gate
    docker exec nextseek uv run manage.py nessie --tier full --scope all   # paid full pass (needs seed)

The harness drives cases through the real top-level router at ``--base-url``
(default ``http://localhost:8000`` — gunicorn inside the same container),
writes ``manifest.json`` + ``report.html`` under ``--out``, prints a summary,
and exits non-zero iff a real (non ``known_fail``) case failed.

Forced runs (the graph_search Nessie POC, all PAID at ``--tier full``):

    manage.py nessie --tier full --cases C --force-route ns --force-parser-mode graph
    manage.py nessie --tier full --cases C --force-route ns --arms graph,api
    manage.py nessie --tier full --cases C --force-route ns --arms graph,api --resume --max-turns 60

``--arms`` calls ``runner.run_arms``: a preflight that proves both forces land,
then every question through each arm back to back, with a manifest and a report
per arm, every turn's payload and ``arms.json`` under ``--out``. A wrong answer
in an arm is the measurement, so an arms run never exits non-zero for one.
"""
from __future__ import annotations

import os
import urllib.error
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from NessieAI import paths

# The hand-owned harness corpus, NessieAI/tests/nessie_tests/corpus.json.
_CORPUS = paths.NESSIE_CORPUS

_DEFAULT_PASSWORD = "demopassword"


class Command(BaseCommand):
    help = "Run the Nessie router-aware assistant test harness (route/full tiers, forced arms)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--tier", choices=["route", "full"], default="route")
        parser.add_argument("--scope", choices=["specific", "all"], default="specific")
        parser.add_argument("--base-url", default="http://localhost:8000")
        parser.add_argument("--family", default=None)
        parser.add_argument("--variant", default=None)
        parser.add_argument("--user", default="demo")
        parser.add_argument("--password", default=None,
                            help=f"Default {_DEFAULT_PASSWORD}. Prefer --password-env, which "
                                 "keeps the password off the command line.")
        parser.add_argument("--password-env", default=None, metavar="NAME",
                            help="Read the password from the environment variable NAME. The "
                                 "value is never printed.")
        parser.add_argument("--pace", type=float, default=0.0)
        parser.add_argument(
            "--consistency", action="store_true",
            help="Run the #33 consistency groups (auto-on for --tier full).",
        )
        parser.add_argument("--sample", type=float, default=1.0,
                            help="Fraction of selected variants to run, sampled per family (e.g. 0.1 for a tenth). Default 1.0 = all.")
        parser.add_argument("--seed", type=int, default=0, help="Deterministic sampling seed.")
        parser.add_argument("--cases", default=None,
                            help="Path to a catalog-shaped JSON file listing exactly which cases to "
                                 "run, instead of a seeded sample. Keys: include_ids (existing corpus "
                                 "variants, by id, in file order) and/or families (new ad-hoc variants "
                                 "inline). Overrides --scope/--family/--variant/--sample/--seed; "
                                 "inline variants run exactly as written, with no family floor.")
        parser.add_argument("--out", default="/app/nessie_out")
        parser.add_argument("--force-route", choices=["ns", "cc"], default=None,
                            help="Force every turn to one engine instead of the router. "
                                 "Admin-only server side: a non-superuser's value is dropped "
                                 "silently. The route criteria are stripped, as on any forced arm.")
        parser.add_argument("--force-parser-mode", choices=["graph", "api"], default=None,
                            help="With --force-route ns. Evaluation only: force the NS parser to "
                                 "the graph or the API path. Honoured only for a superuser on a "
                                 "server that sets NEXTSEEK_EVAL_PARSER_FORCE=1, and ignored "
                                 "without a word otherwise.")
        parser.add_argument("--arms", default=None, metavar="NAMES",
                            help="PAID. Comma-separated forced NS arms (graph,api or graph): every "
                                 "--cases question through each arm back to back, after a "
                                 "preflight that proves both forces land. Needs --cases, "
                                 "--force-route ns and --tier full; excludes --force-parser-mode.")
        parser.add_argument("--resume", action="store_true", default=False,
                            help="--arms only. Continue the run in --out: every (question, arm) "
                                 "it holds is skipped, except a provider outage, which is rerun.")
        parser.add_argument("--max-turns", type=int, default=None,
                            help="--arms only. The turns this invocation may drive; it stops "
                                 "before a question that would exceed it. 0 runs only the "
                                 "preflight.")

    def handle(self, *args, **opts) -> None:
        from NessieAI.tests.nessie_tests import http_driver, runner

        # Every flag check comes before the password is read, and before any turn.
        arms = self._check_arms(opts, runner) if opts["arms"] is not None else None
        if arms is None:
            self._check_normal(opts)
        auth_header = http_driver.basic_auth(opts["user"], self._password(opts))
        if arms is not None:
            self._run_arms(opts, arms, auth_header, runner)
            return

        tier = opts["tier"]
        run_consistency = opts["consistency"] or tier == "full"
        bundle_reader = None
        if tier == "full":
            # Safe here: the management command runs inside a configured Django.
            from NessieAI.tests.nessie_tests.bundle import summary_for_session
            bundle_reader = summary_for_session

        try:
            manifest = runner.run_suite(
                base_url=opts["base_url"],
                auth_header=auth_header,
                tier=tier,
                scope=opts["scope"],
                family=opts["family"],
                variant_id=opts["variant"],
                corpus_path=_CORPUS,
                out_dir=Path(opts["out"]),
                bundle_reader=bundle_reader,
                pace_s=opts["pace"],
                run_consistency=run_consistency,
                sample=opts["sample"], seed=opts["seed"],
                cases_path=opts["cases"],
                force_route=opts["force_route"],
                force_parser_mode=opts["force_parser_mode"],
            )
        except runner.BundleReaderUnavailable as e:
            # Raised before the first turn (runner.check_bundle_reader), e.g. when the
            # app database is unreachable from this process.
            raise CommandError(str(e)) from e
        self._summarize(manifest, tier, opts["scope"], opts["out"], runner)

    @staticmethod
    def _check_normal(opts) -> None:
        arms_only = [flag for flag, present in (("--resume", bool(opts["resume"])),
                                                ("--max-turns", opts["max_turns"] is not None))
                     if present]
        if arms_only:
            raise CommandError(
                f"{', '.join(arms_only)} only applies to --arms; a normal run has no resume "
                f"and no turn cap.")
        if opts["force_parser_mode"] and opts["force_route"] != "ns":
            raise CommandError(
                "--force-parser-mode needs --force-route ns: the switch lives in the NS "
                "parser, and an unforced turn may be routed to Container-CC, where the field "
                "is ignored without a word.")

    @staticmethod
    def _check_arms(opts, runner) -> list[str]:
        if not opts["cases"]:
            raise CommandError("--arms needs --cases: the arms run exactly the questions of a "
                               "cases file.")
        if opts["force_route"] != "ns":
            raise CommandError("--arms needs --force-route ns: every arm is an NS arm, and an "
                               "unforced turn may be routed to Container-CC.")
        if opts["force_parser_mode"]:
            raise CommandError("--arms sets the parser force per arm; drop --force-parser-mode.")
        if opts["tier"] != "full":
            raise CommandError("--arms drives full turns; add --tier full.")
        names = [a.strip() for a in opts["arms"].split(",")]
        if (not names or any(a not in runner.ARM_PRESETS for a in names)
                or len(set(names)) != len(names)):
            raise CommandError(f"--arms takes distinct names from {sorted(runner.ARM_PRESETS)}, "
                               f"comma-separated; got {opts['arms']!r}.")
        if opts["max_turns"] is not None and opts["max_turns"] < 0:
            raise CommandError("--max-turns must be 0 or more.")
        return names

    @staticmethod
    def _password(opts) -> str:
        name = opts["password_env"]
        if not name:
            return opts["password"] if opts["password"] is not None else _DEFAULT_PASSWORD
        if opts["password"] is not None:
            raise CommandError("give --password or --password-env, not both.")
        value = os.environ.get(name)
        if not value:
            raise CommandError(f"--password-env {name}: that environment variable is unset "
                               f"or empty.")
        return value

    def _run_arms(self, opts, arms, auth_header, runner) -> None:
        from NessieAI.tests.nessie_tests import preflight
        from NessieAI.tests.nessie_tests.bundle import summary_for_session

        try:
            result = runner.run_arms(
                base_url=opts["base_url"], auth_header=auth_header, corpus_path=_CORPUS,
                cases_path=opts["cases"], out_dir=Path(opts["out"]), arms=arms,
                resume=opts["resume"], max_turns=opts["max_turns"],
                bundle_reader=summary_for_session)
        except preflight.PreflightRefused as e:
            raise CommandError(
                f"the preflight refused the run: its probe turns were sent and billed, no "
                f"question was.\n{e}") from e
        except runner.ArmsRunRefused as e:
            raise CommandError(f"refused, nothing was billed: {e}") from e
        except runner.BundleReaderUnavailable as e:
            # Checked before the preflight, so not even a probe turn was sent.
            raise CommandError(str(e)) from e
        except ValueError as e:
            raise CommandError(str(e)) from e
        except urllib.error.URLError as e:
            raise CommandError(
                f"could not talk to {opts['base_url']}: {e}. Every completed (question, arm) "
                f"is on disk under {opts['out']}, and --resume skips it.") from e
        self._summarize_arms(result, opts["out"], runner)

    def _summarize_arms(self, result, out, runner) -> None:
        w = self.stdout.write
        progress = result.get("progress") or {}
        w("")
        w(f"Nessie arms  state={progress.get('state')}  "
          f"turns driven by this invocation: {progress.get('turns_driven')}")
        for arm, manifest in result["manifests"].items():
            summary = runner.classify_entries(manifest)
            count = summary["counts"]
            w(f"  arm {arm}: {summary['total']} questions  passed {count['passed']}  "
              f"failed {count['failed']}  error {count['error']} "
              f"({len(summary['outage'])} provider outage, rerun with --resume)  "
              f"no-assert {count['no_assertions']}  cost {summary['cost_display']}")
            w(f"    report: {Path(out) / arm / 'report.html'}")
        w(f"  arms file: {result['arms_file']}")
        w("  A wrong answer is the measurement here, not a gate failure: score the arms "
          "against the ground truth.")

    def _summarize(self, manifest, tier, scope, out, runner) -> None:
        # Classification lives in runner.classify_entries so this summary and
        # runner.gate_failed cannot disagree again. They used to: "real failures" here
        # excluded xpass while the gate counted it, so a run printed "GATE: PASS" and
        # then raised SystemExit(1).
        summary = runner.classify_entries(manifest)
        count = summary["counts"]
        real_fails = summary["real_fails"]
        known_failed = summary["known_failed"]

        w = self.stdout.write
        w("")
        w(f"Nessie harness  tier={tier}  scope={scope}  ({summary['total']} cases)")
        w(f"  passed  : {count['passed']}")
        w(f"  failed  : {count['failed']}")
        w(f"  skipped : {count['skipped']}")
        w(f"  error   : {count['error']}")
        w(f"  xpass   : {count['xpass']}  (known_fail that PASSED — stale expectation, counted as a failure)")
        w(f"  no-assert: {count['no_assertions']}  (evaluated ZERO criteria — green here would be vacuous, counted as a failure)")
        w(f"  known-fail that failed as expected (excluded from gate): {len(known_failed)}")
        # `cost_display`, not `total_cost`. The latter is float-or-None by design —
        # a route-tier turn never emits `query_complete`, so its cost is unobservable
        # rather than zero — and interpolating it prints the literal "$None".
        w(f"  cost    : {summary['cost_display']}")
        if summary["outage"]:
            w("")
            w(self.style.WARNING(
                f"  INFRASTRUCTURE: {len(summary['outage'])} case(s) were lost to an LLM provider "
                f"outage — they were not scored and say nothing about the product:"))
            for e in summary["outage"]:
                w(f"    - {e.family}/{e.id}")
            w(self.style.WARNING(
                "  Re-run these before reading the headline. Without this line they would "
                "vanish from the printout entirely: an outaged known_fail is excluded from "
                "`known-fail that failed as expected` with nothing else reporting it."))
        if summary["heuristic_routed"]:
            w("")
            w(self.style.WARNING(
                f"  INFRASTRUCTURE: {len(summary['heuristic_routed'])} case(s) were not routed "
                f"by BAML — their route is not evidence about routing:"))
            for e in summary["heuristic_routed"]:
                w(f"    - {e.family}/{e.id}  (route_source={e.route_source})")
        w("")
        selection = (f"cases={manifest.cases_file}" if manifest.cases_file
                     else f"seed={manifest.seed}  sample={manifest.sample}")
        w(f"  {selection}  "
          f"corpus={(manifest.corpus_fingerprint or '')[:12]}  git={manifest.git_sha}")
        w("")
        if real_fails:
            w(self.style.ERROR(f"GATE: FAIL — {len(real_fails)} real failure(s):"))
            for e in real_fails:
                detail = e.reason or ", ".join(e.failed_criteria)
                w(f"    - {e.family}/{e.id}  [{e.status}]  {detail}")
        else:
            w(self.style.SUCCESS("GATE: PASS (no real failures)"))
        if known_failed:
            w("")
            w("  known-fail cases that failed as expected (until #32/#33 are fixed):")
            for e in known_failed:
                w(f"    - {e.family}/{e.id}")
        w("")
        w(f"  report:   {out}/report.html")
        w(f"  manifest: {out}/manifest.json")

        # Non-zero exit iff a real (non known_fail) case failed, so CI/gates work.
        if runner.gate_failed(manifest):
            raise SystemExit(1)
