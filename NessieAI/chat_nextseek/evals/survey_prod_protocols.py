#!/usr/bin/env python3
"""Resolve the team questions' UIDs against production NExtSEEK and survey what
protocol evidence actually exists there.

Production (https://nextseek.mit.edu) turns out to hold every team study,
including the four the fibroblast question needs, which no other reachable
instance had. This script establishes two things before any model call:

  1. How many of each question's UIDs resolve there.
  2. What the `Protocol` field actually contains across the FULL lineage —
     a SOP URL (which the shipped protocol machinery can download and
     text-extract), free-form prose (which is real evidence but arrives as
     metadata, not as a fetched document), or empty.

The distinction matters for the comparison being run: "with protocol data"
means something different depending on which of those three it is.

Runs on the HOST (needs no container): it speaks to the production REST API
directly with basic auth read from the curation .env.
"""
from __future__ import annotations
import os

import base64
import collections
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

import certifi

BASE = "https://nextseek.mit.edu"
ENV_PATH = Path(os.environ.get("NEXTSEEK_EVAL_ENV_FILE", ".env"))
QUESTIONS = Path(__file__).resolve().parent / "team_questions.json"
OUT = Path(__file__).resolve().parent / "demo-output-team-prod"
BATCH = 50
CTX = ssl.create_default_context(cafile=certifi.where())
URLISH = re.compile(r"https?://", re.I)


def load_env() -> dict[str, str]:
    env = {}
    for raw in ENV_PATH.read_text().splitlines():
        line = raw.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = load_env()
AUTH = "Basic " + base64.b64encode(
    f"{ENV['NEXTSEEK_USERNAME']}:{ENV['NEXTSEEK_PASSWORD']}".encode()
).decode()


def retrieve(uids: list[str]) -> dict | None:
    url = f"{BASE}/nextseek_api/admin/samples/retrieve/?page_size=1000"
    req = urllib.request.Request(
        url, data=json.dumps({"identifiers": uids}).encode(), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "Authorization": AUTH},
    )
    try:
        with urllib.request.urlopen(req, timeout=180, context=CTX) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        print(f"    HTTP {e.code}: {e.read()[:120].decode('utf-8','replace')}", file=sys.stderr)
        return None
    except Exception as e:  # noqa: BLE001
        print(f"    {type(e).__name__}: {e}", file=sys.stderr)
        return None


def classify(value) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return "empty"
    if isinstance(value, str) and URLISH.search(value):
        return "sop_url"
    return "free_text"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    questions = json.loads(QUESTIONS.read_text())
    report = []

    for q in questions:
        qid, uids = q["id"], q["uids"]
        resolved: set[str] = set()
        proto_kinds: collections.Counter = collections.Counter()
        by_type: dict[str, collections.Counter] = {}
        samples_texts: dict[str, set] = {}

        for i in range(0, len(uids), BATCH):
            batch = uids[i:i + BATCH]
            body = retrieve(batch)
            if not body:
                continue
            for blk in body.get("data") or []:
                st = blk.get("sample_type") or "unknown"
                for s in blk.get("samples") or []:
                    md = s.get("metadata") or {}
                    uid = md.get("UID") or s.get("uuid")
                    if uid in set(batch):
                        resolved.add(uid)
                    kind = classify(md.get("Protocol"))
                    proto_kinds[kind] += 1
                    by_type.setdefault(st, collections.Counter())[kind] += 1
                    if kind == "free_text":
                        samples_texts.setdefault(st, set()).add(str(md["Protocol"])[:160])

        entry = {
            "id": qid,
            "n_uids": len(uids),
            "n_resolved": len(resolved),
            "protocol_kinds_across_lineage": dict(proto_kinds),
            "protocol_kinds_by_sample_type": {k: dict(v) for k, v in sorted(by_type.items())},
            "free_text_examples_by_type": {k: sorted(v)[:3] for k, v in sorted(samples_texts.items())},
        }
        report.append(entry)
        print(f"{qid:<34} resolved {len(resolved):>4}/{len(uids):<4} "
              f"protocol fields across lineage: {dict(proto_kinds)}")
        for st, c in sorted(by_type.items()):
            if c.get("free_text") or c.get("sop_url"):
                print(f"     {st:<8} {dict(c)}")

    (OUT / "protocol_survey.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nwritten: {OUT / 'protocol_survey.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
