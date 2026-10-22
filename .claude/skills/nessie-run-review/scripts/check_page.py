#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["jsonschema>=4,<5"]
# ///
"""Fail any review page that did not come out of build_report.py. Run it before publishing.

    uv run scripts/check_page.py report.html [more.html ...]

Exit 0 when every page passes, 1 when any fails (each failure is named). A page passes when it
(1) embeds its data block and fingerprint, (2) the data validates against schema/review.schema.json,
(3) the fingerprint matches the data, and (4) the page is byte-for-byte what the generator renders
from that data with the current template. (4) is what catches a hand edit, an appended section or a
page written from scratch around a copied data block.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import review_data as rd  # noqa: E402


def check(html: str) -> list[str]:
    try:
        data, fp = rd.extract(html)
    except ValueError as e:
        return [str(e)]
    except Exception as e:  # a corrupted block
        return [f"embedded data is not valid JSON: {e}"]
    bad = rd.problems(data)
    if bad:
        return ["data fails review.schema.json:"] + [f"  {b}" for b in bad[:20]]
    if fp.get("generator") != rd.GENERATOR or fp.get("schema_version") != rd.SCHEMA_VERSION:
        return [f"fingerprint is {fp.get('generator')} v{fp.get('schema_version')}, "
                f"this checker is {rd.GENERATOR} v{rd.SCHEMA_VERSION}"]
    if fp.get("data_sha256") != rd.digest(data):
        return ["fingerprint does not match the embedded data: the data was edited after the build"]
    tpl = rd.TEMPLATE.read_text(encoding="utf-8")
    if html in (rd.render(data, tpl), rd.render(data, tpl, standalone=True)):
        return []
    return ["the page differs from what build_report.py renders from its own data "
            "(hand edit, appended markup, or an older template): rebuild it with build_report.py"]


def main(argv) -> int:
    if not argv:
        print(__doc__)
        return 2
    rc = 0
    for p in argv:
        errs = check(Path(p).read_text(encoding="utf-8"))
        print(f"{'FAIL' if errs else 'ok  '} {p}")
        for e in errs:
            print(f"     {e}")
        rc |= bool(errs)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
