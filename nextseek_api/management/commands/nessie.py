"""``manage.py nessie`` — run the Nessie router-aware assistant test harness.

Runs the ``nessie_tests`` harness (see ``nessie_tests/README.md``) from inside
the trusted Django process, so the full-tier bundle reader (which imports Django
models) works with no separate ``django.setup()`` bootstrap.

    docker exec nextseek uv run manage.py nessie --tier route              # cheap route gate
    docker exec nextseek uv run manage.py nessie --tier full --scope all   # paid full pass (needs seed)

The harness drives cases through the real top-level router at ``--base-url``
(default ``http://localhost:8000`` — gunicorn inside the same container),
writes ``manifest.json`` + ``report.html`` under ``--out``, prints a summary,
and exits non-zero iff a real (non ``known_fail``) case failed.
"""
from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand

# This file is nextseek_api/management/commands/nessie.py; the repo root (which
# holds the top-level ``nessie_tests`` package) is four parents up.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_OVERLAY = _REPO_ROOT / "nessie_tests" / "overlay.json"


class Command(BaseCommand):
    help = "Run the Nessie router-aware assistant test harness (route/full tiers)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--tier", choices=["route", "full"], default="route")
        parser.add_argument("--scope", choices=["specific", "all"], default="specific")
        parser.add_argument("--base-url", default="http://localhost:8000")
        parser.add_argument("--family", default=None)
        parser.add_argument("--variant", default=None)
        parser.add_argument("--user", default="demo")
        parser.add_argument("--password", default="demopassword")
        parser.add_argument("--pace", type=float, default=0.0)
        parser.add_argument(
            "--consistency", action="store_true",
            help="Run the #33 consistency groups (auto-on for --tier full).",
        )
        parser.add_argument("--sample", type=float, default=1.0,
                            help="Fraction of selected variants to run, sampled per family (e.g. 0.1 for a tenth). Default 1.0 = all.")
        parser.add_argument("--seed", type=int, default=0, help="Deterministic sampling seed.")
        parser.add_argument("--out", default="/app/nessie_out")

    def handle(self, *args, **opts) -> None:
        from nessie_tests import http_driver, runner

        tier = opts["tier"]
        run_consistency = opts["consistency"] or tier == "full"
        bundle_reader = None
        if tier == "full":
            # Safe here: the management command runs inside a configured Django.
            from nessie_tests.bundle import summary_for_session
            bundle_reader = summary_for_session

        manifest = runner.run_suite(
            base_url=opts["base_url"],
            auth_header=http_driver.basic_auth(opts["user"], opts["password"]),
            tier=tier,
            scope=opts["scope"],
            family=opts["family"],
            variant_id=opts["variant"],
            overlay_path=_OVERLAY,
            out_dir=Path(opts["out"]),
            bundle_reader=bundle_reader,
            pace_s=opts["pace"],
            run_consistency=run_consistency,
            sample=opts["sample"], seed=opts["seed"],
        )
        self._summarize(manifest, tier, opts["scope"], opts["out"], runner)

    def _summarize(self, manifest, tier, scope, out, runner) -> None:
        entries = manifest.entries
        count = lambda s: sum(1 for e in entries if e.status == s)
        real_fails = [e for e in entries if e.status in ("failed", "error") and not e.expected_fail]
        known = [e for e in entries if e.expected_fail]

        w = self.stdout.write
        w("")
        w(f"Nessie harness  tier={tier}  scope={scope}  ({len(entries)} cases)")
        w(f"  passed  : {count('passed')}")
        w(f"  failed  : {count('failed')}")
        w(f"  skipped : {count('skipped')}")
        w(f"  error   : {count('error')}")
        w(f"  known-fail (expected, excluded from gate): {len(known)}")
        w("")
        if real_fails:
            w(self.style.ERROR(f"GATE: FAIL — {len(real_fails)} real failure(s):"))
            for e in real_fails:
                detail = e.reason or ", ".join(e.failed_criteria)
                w(f"    - {e.family}/{e.id}  [{e.status}]  {detail}")
        else:
            w(self.style.SUCCESS("GATE: PASS (no real failures)"))
        if known:
            w("")
            w("  known-fail cases (expected to fail until #32/#33 are fixed):")
            for e in known:
                w(f"    - {e.family}/{e.id}")
        w("")
        w(f"  report:   {out}/report.html")
        w(f"  manifest: {out}/manifest.json")

        # Non-zero exit iff a real (non known_fail) case failed, so CI/gates work.
        if runner.gate_failed(manifest):
            raise SystemExit(1)
