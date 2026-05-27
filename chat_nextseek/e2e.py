"""chat_nextseek E2E test runner — entry point.

Usage:
  uv run e2e.py                        # sample + run at ratio 0.33
  uv run e2e.py --ratio 0.5
  uv run e2e.py --ratio full           # alias for 1.0
  uv run e2e.py --seed 42
  uv run e2e.py --family pipeline_nfcore
  uv run e2e.py --variant adv.basic
  uv run e2e.py --rerun outputs/e2e_<ts>/manifest.json [--failed-only]
  uv run e2e.py --list [--family <name>]
  uv run e2e.py --report outputs/e2e_<ts>/   # regenerate HTML from existing run
  uv run e2e.py --playwright              # Front-2 spot tests (Phase E)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

CATALOG_PATH = Path(__file__).parent / "e2e" / "catalog.json"


def _parse_ratio(s: str) -> float:
    if s == "full":
        return 1.0
    return float(s)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="chat_nextseek E2E test runner")
    p.add_argument("--ratio", type=_parse_ratio, default=0.33,
                   help="Per-family sampling ratio (default 0.33). 'full' = 1.0.")
    p.add_argument("--seed", type=int, default=None,
                   help="Deterministic sampling seed. If unset, uses time-based seed (also recorded in manifest).")
    p.add_argument("--family", type=str, default=None,
                   help="Run every variant in this family (overrides --ratio).")
    p.add_argument("--variant", type=str, default=None,
                   help="Run one variant by id (overrides --ratio and --family).")
    p.add_argument("--rerun", type=Path, default=None,
                   help="Re-run the exact variants pinned in a prior manifest.json.")
    p.add_argument("--failed-only", action="store_true",
                   help="With --rerun, only re-run variants whose status==failed.")
    p.add_argument("--list", action="store_true",
                   help="List catalog variants and exit.")
    p.add_argument("--report", type=Path, default=None,
                   help="Regenerate report.html from a prior run dir and exit.")
    p.add_argument("--pace", type=int, default=15,
                   help="Seconds to sleep between turns (default 15). 0 disables pacing.")
    p.add_argument("--profile", type=str, default="mixed",
                   help="Provider profile tag (informational; written to manifest).")
    p.add_argument("--playwright", action="store_true",
                   help="Run Front-2 Playwright spot tests instead of Front-1 CLI E2E.")
    p.add_argument("--spot", type=str, default=None,
                   help="With --playwright, run a single spot test by id.")
    p.add_argument("--headed", action="store_true",
                   help="With --playwright, run in headed (non-headless) mode.")
    p.add_argument("--no-playwright", action="store_true",
                   help="Skip the Phase E browser run (CLI only).")
    p.add_argument("--no-cli", action="store_true",
                   help="Skip the Phase A CLI run (Playwright only). Implied by --playwright.")
    p.add_argument("--video", action="store_true",
                   help="With --playwright, capture video.webm per browser variant.")
    p.add_argument("--init-env", action="store_true",
                   help="Generate chat_nextseek/.env from sibling docker + dmac files (target: docker-local).")
    p.add_argument("--force", action="store_true",
                   help="With --init-env, overwrite an existing .env.")

    args = p.parse_args(argv)

    if args.init_env:
        from e2e.import_env import write_env  # noqa: PLC0415
        try:
            path = write_env(force=args.force)
        except FileExistsError as exc:
            print(f"[e2e] {exc}", file=sys.stderr)
            return 1
        print(f"[e2e] wrote {path} (target: docker-local)")
        print(f"[e2e] API_USER/API_PASS default to demo/demopassword; edit {path} if your docker user differs")
        print(f"[e2e] next: `uv run e2e.py --list` to verify, or `uv run e2e.py --variant advanced.basic_ndma` to smoke-test")
        return 0

    if args.list:
        from e2e.catalog import load_catalog  # noqa: PLC0415
        cat = load_catalog(CATALOG_PATH)
        if args.family and args.family not in cat.families:
            print(f"[e2e] no family named {args.family!r}. Available: {', '.join(sorted(cat.families))}", file=sys.stderr)
            return 1
        families_to_show = (
            {args.family: cat.families[args.family]}
            if args.family
            else cat.families
        )
        for fam_name, fam in families_to_show.items():
            print(f"\n== {fam_name} ({len(fam.variants)} variants) ==")
            for v in fam.variants:
                tags = f" [{','.join(v.tags)}]" if v.tags else ""
                env = f" needs={','.join(v.requires_env)}" if v.requires_env else ""
                print(f"  {v.id:40s}  {v.name}{tags}{env}")
        return 0

    if args.report:
        from e2e.manifest import load_manifest  # noqa: PLC0415
        from e2e.report import generate_html_report  # noqa: PLC0415
        manifest = load_manifest(args.report / "manifest.json")
        path = generate_html_report(manifest, args.report)
        print(f"[e2e] regenerated {path}")
        return 0

    if args.playwright:
        # Playwright-only mode: equivalent to --no-cli
        args.no_cli = True

    from e2e.runner import run_main  # noqa: PLC0415
    return run_main(
        CATALOG_PATH,
        ratio=args.ratio,
        seed=args.seed,
        family=args.family,
        variant_id=args.variant or args.spot,
        rerun_manifest=args.rerun,
        failed_only=args.failed_only,
        pace_seconds=args.pace,
        profile=args.profile,
        skip_cli=getattr(args, "no_cli", False),
        skip_playwright=getattr(args, "no_playwright", False),
        headed=args.headed,
        video=getattr(args, "video", False),
    )


if __name__ == "__main__":
    sys.exit(main())
