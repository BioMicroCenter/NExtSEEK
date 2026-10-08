#!/usr/bin/env python3
"""Rewrite every cluster path in a fixture folder to /RUN/..., then refuse if
any absolute path outside /RUN remains. The repo is public: params, traces,
samplesheets and launch records hold launch, work and home paths.

Usage: sanitize_fixture.py <fixture-folder> <cluster-run-dir> [<other-prefix> ...]
Each <other-prefix> (a launch or work dir) is rewritten to /RUN as well.

Every file under the folder is read, whatever its name. A file that is not
strict UTF-8 text is refused, never skipped or rewritten. A prefix matches whole
path segments only (/a/b does not match /a/bc/x). Nothing is written unless
every file passes, so a refusal leaves the folder as it was.
"""
import pathlib
import re
import sys

SEGMENT = r"[\w.@+-]"
# An absolute path: a slash-led run of path characters, at least two segments.
# /RUN is exempt only as a whole segment (/RUN/..., not /RUN-old/... or /RUN.x/...).
ABSOLUTE = re.compile(rf"(?<![\w.:/])/(?!RUN(?!{SEGMENT}))({SEGMENT}+(?:/{SEGMENT}+)+)")


def prefix_pattern(prefixes: list[str]) -> re.Pattern | None:
    """Longest prefix first, matched on whole segments on both sides."""
    if not prefixes:
        return None
    alternatives = "|".join(re.escape(p) for p in prefixes)
    return re.compile(rf"(?<!{SEGMENT})(?<!/)(?:{alternatives})(?!{SEGMENT})")


def main() -> int:
    if len(sys.argv) < 3:
        usage = next(line for line in __doc__.splitlines() if line.startswith("Usage:"))
        print(usage, file=sys.stderr)
        return 2
    folder = pathlib.Path(sys.argv[1])
    prefixes = sorted({p.rstrip("/") for p in sys.argv[2:] if p.strip("/")},
                      key=len, reverse=True)
    pattern = prefix_pattern(prefixes)
    rewritten: dict[pathlib.Path, str] = {}
    undecodable, leftovers = [], []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(folder)
        try:
            original = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            undecodable.append(str(rel))
            continue
        text = pattern.sub("/RUN", original) if pattern else original
        if text != original:
            rewritten[path] = text
        leftovers += [f"{rel}: /{m}" for m in ABSOLUTE.findall(text)]
    if undecodable or leftovers:
        if undecodable:
            print("refusing: not UTF-8 text (a fixture holds text files only):",
                  *undecodable, sep="\n  ", file=sys.stderr)
        if leftovers:
            print("refusing: absolute paths remain (add their prefix as an argument):",
                  *sorted(set(leftovers))[:50], sep="\n  ", file=sys.stderr)
        return 1
    for path, text in rewritten.items():
        path.write_bytes(text.encode("utf-8"))
    print(f"sanitized {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
