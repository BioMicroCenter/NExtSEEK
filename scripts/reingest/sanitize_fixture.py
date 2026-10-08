#!/usr/bin/env python3
"""Rewrite every cluster path in a fixture folder to /RUN/..., then refuse if
any absolute path outside /RUN remains. The repo is public: params, traces,
samplesheets and launch records hold launch, work and home paths.

Usage: sanitize_fixture.py <fixture-folder> <cluster-run-dir> [<other-prefix> ...]
Each <other-prefix> (a launch or work dir) is rewritten to /RUN as well.
"""
import pathlib
import re
import sys

TEXT_SUFFIXES = {".json", ".yml", ".yaml", ".csv", ".tsv", ".txt"}
# An absolute path: a slash-led run of path characters, at least two segments.
ABSOLUTE = re.compile(r"(?<![\w.:/])/(?!RUN\b)[\w.@+-]+(?:/[\w.@+-]+)+")


def main() -> int:
    folder = pathlib.Path(sys.argv[1])
    prefixes = sorted({p.rstrip("/") for p in sys.argv[2:] if p.strip("/")},
                      key=len, reverse=True)
    leftovers = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for prefix in prefixes:
            text = text.replace(prefix, "/RUN")
        path.write_text(text, encoding="utf-8")
        leftovers += [f"{path.relative_to(folder)}: {m}" for m in ABSOLUTE.findall(text)]
    if leftovers:
        print("refusing: absolute paths remain (add their prefix as an argument):",
              *sorted(set(leftovers))[:50], sep="\n  ", file=sys.stderr)
        return 1
    print(f"sanitized {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
