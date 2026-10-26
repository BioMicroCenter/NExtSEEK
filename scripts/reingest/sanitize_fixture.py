#!/usr/bin/env python3
"""Rewrite every cluster path in a fixture folder to /RUN/..., then refuse if
any absolute path outside /RUN, any email address or any URL remains. The repo
is public: params, traces, samplesheets and launch records hold launch, work and
home paths, and params can hold a notification address or a webhook.

Usage: sanitize_fixture.py <fixture-folder> <cluster-run-dir> [<other-prefix> ...]
Each <other-prefix> (a launch or work dir) is rewritten to /RUN as well.

Every file under the folder is read, whatever its name. A file that is not
strict UTF-8 text is refused, never skipped or rewritten. A prefix matches whole
path segments only (/a/b does not match /a/bc/x). Nothing is written unless
every file passes, so a refusal leaves the folder as it was. An email address
or a URL (any scheme://, except public nf-core test data) is refused, never rewritten: blank that param by hand.
"""
import pathlib
import re
import sys
import urllib.parse

SEGMENT = r"[\w.@+-]"
# An absolute path: a slash-led run of path characters, at least two segments.
# /RUN is exempt only as a whole segment (/RUN/..., not /RUN-old/... or /RUN.x/...).
ABSOLUTE = re.compile(rf"(?<![\w.:/])/(?!RUN(?!{SEGMENT}))({SEGMENT}+(?:/{SEGMENT}+)+)")
EMAIL = re.compile(r"[\w.%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# Any scheme: http, https, s3, gs, ftp, file, ...
URL = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>]*")
# Public nf-core test data is what a fixture's samplesheet legitimately points at (the spec of
# 2026-10-26 §2); anything else with a scheme is still refused.
ALLOWED_URL_PREFIXES = (
    "https://raw.githubusercontent.com/nf-core/test-datasets/",
    "https://github.com/nf-core/test-datasets/",
)
_ALLOWED_HOSTS = ("raw.githubusercontent.com", "github.com")
_TEST_DATA_PATH = re.compile(r"^/nf-core/test-datasets/[A-Za-z0-9._/+-]*$")
# The URL regex runs through commas, semicolons and (in escaped JSON text) backslashes, so one
# match can hold several URLs; each piece is judged on its own.
_URL_SEPARATORS = re.compile(r"[,;\\]")


def allowed_url(url: str) -> bool:
    """True only for a plain https URL under /nf-core/test-datasets/ on GitHub's two hosts:
    one URL only, no userinfo or port, a path of plain file-name characters, no dot-dot
    segment, no query, no fragment."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    segments = parsed.path.split("/")
    # A second "://" means another URL is glued on (by |, ), }, or nothing at all); the path
    # character class refuses the glue characters themselves and any percent-encoding.
    return (url.count("://") == 1
            and _TEST_DATA_PATH.match(parsed.path) is not None
            and parsed.scheme == "https"
            and parsed.netloc in _ALLOWED_HOSTS
            and parsed.path.startswith("/nf-core/test-datasets/")
            and not parsed.query and not parsed.fragment
            and ".." not in segments
            and not re.search(r"%2e|%2f|%5c", parsed.path, re.IGNORECASE)
            and url.startswith(ALLOWED_URL_PREFIXES))


def disallowed_urls(text: str) -> list[str]:
    """Every URL-like piece of text that is not public nf-core test data."""
    out = []
    for match in URL.findall(text):
        for piece in _URL_SEPARATORS.split(match):
            if "://" in piece and not allowed_url(piece):
                out.append(piece)
    return out


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
    undecodable, leftovers, contacts = [], [], []
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
        contacts += [f"{rel}: {m}" for m in EMAIL.findall(text) + disallowed_urls(text)]
    if undecodable or leftovers or contacts:
        if undecodable:
            print("refusing: not UTF-8 text (a fixture holds text files only):",
                  *undecodable, sep="\n  ", file=sys.stderr)
        if leftovers:
            print("refusing: absolute paths remain (add their prefix as an argument):",
                  *sorted(set(leftovers))[:50], sep="\n  ", file=sys.stderr)
        if contacts:
            print("refusing: email addresses or URLs remain (blank those params by hand):",
                  *sorted(set(contacts))[:50], sep="\n  ", file=sys.stderr)
        return 1
    for path, text in rewritten.items():
        path.write_bytes(text.encode("utf-8"))
    print(f"sanitized {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
