from __future__ import annotations
import argparse
from pathlib import Path
from nessie_tests import runner, http_driver

_OVERLAY = Path(__file__).resolve().parent / "overlay.json"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nessie_tests", description="Router-aware assistant e2e harness")
    p.add_argument("--base-url", required=True, help="e.g. http://localhost:8000")
    p.add_argument("--tier", choices=["route", "full"], default="route")
    p.add_argument("--scope", choices=["specific", "all"], default="specific")
    p.add_argument("--family", default=None)
    p.add_argument("--variant", default=None)
    p.add_argument("--user", default="demo")
    p.add_argument("--password", default="demopassword")
    p.add_argument("--pace", type=float, default=0.0)
    p.add_argument("--out", type=Path, default=Path("nessie_out"))
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    auth = http_driver.basic_auth(a.user, a.password)
    bundle_reader = None
    if a.tier == "full":
        from nessie_tests.bundle import summary_for_session
        bundle_reader = summary_for_session
    manifest = runner.run_suite(
        base_url=a.base_url, auth_header=auth, tier=a.tier, scope=a.scope,
        family=a.family, variant_id=a.variant, overlay_path=_OVERLAY,
        out_dir=a.out, bundle_reader=bundle_reader, pace_s=a.pace)
    fails = runner.gate_failed(manifest)
    print(f"nessie: {len(manifest.entries)} cases, {fails} real failures "
          f"(tier={a.tier} scope={a.scope}); report → {a.out}/report.html")
    return 1 if fails else 0
