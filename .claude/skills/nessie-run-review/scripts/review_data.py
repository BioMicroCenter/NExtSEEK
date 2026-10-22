"""The review page's data contract: validate, fingerprint, embed, extract, render.

The page is a pure function of (template, data). `build_report.py` builds `data`, `render` makes
the page, and `check_page.py` proves a page came out of `render` by extracting its embedded data,
validating it against schema/review.schema.json and rendering it again. Nothing here reads a run.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ImportError:  # run with `uv run`, which reads the script's own dependency block
    raise SystemExit("jsonschema is missing: run this with `uv run scripts/<name>.py`")

SKILL = Path(__file__).resolve().parent.parent
SCHEMA = SKILL / "schema" / "review.schema.json"
TEMPLATE = SKILL / "templates" / "report.html.tpl"
GENERATOR = "nessie-run-review"
SCHEMA_VERSION = 1
PLACEHOLDERS = {"__TITLE__", "__EYEBROW__", "__HEADLINE__", "__SUBHEAD__", "__RUNROOT__", "__DATA__", "__FINGERPRINT__"}

DATA_RE = re.compile(r'<script type="application/json" id="review-data">(.*?)</script>', re.S)
FP_RE = re.compile(r'<script type="application/json" id="review-fingerprint">(.*?)</script>', re.S)
STANDALONE = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
              '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
              "{head}\n</head>\n<body>\n{body}\n</body>\n</html>\n")


def problems(data) -> list[str]:
    """Every schema violation, one line each, path first. Empty when the data is valid."""
    v = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    return [f"{'.'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message[:200]}"
            for e in sorted(v.iter_errors(data), key=lambda e: list(map(str, e.absolute_path)))]


def digest(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _script_json(obj) -> str:
    # `<` as < keeps a "</script>" in a case's text from ending the block; JSON.parse undoes it.
    return json.dumps(obj, separators=(",", ":")).replace("<", "\\u003c")


def render(data, template: str, *, standalone: bool = False) -> str:
    h = data["header"]
    html = template
    for k, v in {"__TITLE__": h["title"], "__EYEBROW__": h["eyebrow"], "__HEADLINE__": h["headline"],
                 "__SUBHEAD__": h["subhead"], "__RUNROOT__": h["runroot"]}.items():  # PLACEHOLDERS
        html = html.replace(k, v)
    fp = {"generator": GENERATOR, "schema_version": SCHEMA_VERSION, "data_sha256": digest(data)}
    html = html.replace("__FINGERPRINT__", _script_json(fp)).replace("__DATA__", _script_json(data))
    if standalone:
        # <title>/<style> belong in <head>; everything from the first <div> is body.
        split = html.find('<div class="wrap">')
        head, body = (html[:split], html[split:]) if split > 0 else ("", html)
        html = STANDALONE.format(head=head.strip(), body=body.strip())
    return html


def extract(html: str):
    """(data, fingerprint) from a page, or raises ValueError naming what is missing."""
    d, f = DATA_RE.search(html), FP_RE.search(html)
    if not d:
        raise ValueError('no <script type="application/json" id="review-data"> block: not generator output')
    if not f:
        raise ValueError('no id="review-fingerprint" block: not generator output')
    return json.loads(d.group(1)), json.loads(f.group(1))
