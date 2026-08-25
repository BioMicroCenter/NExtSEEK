#!/usr/bin/env python3
"""Build the team questions' selection digests against PRODUCTION NExtSEEK, on
the host, and write them to a file the container can consume.

## Why this runs on the host rather than in the container

Production (https://nextseek.mit.edu) holds every team study — including the
four the fibroblast question needs, which exist on no other reachable
instance. But the container is configured as the `demo` user and gets 401
there, and moving a real user's password into the container is credential
handling worth avoiding when there is an alternative.

The alternative: the host can already reach production and can import
chat_nextseek with PyPDF2 available, so it builds the digests here — the
expensive, credential-bearing part — and the container only needs the
finished digest to make the model call. Same split the SEEK path already
uses, where `pull_team_uids.py` fetches and `run_team_questions.py` reasons.

## What this captures that the SEEK path could not

  - The FULL lineage (D.SEQ, DNA, RNA, TIS, PAT, PAV, MUS ...), not just D.SEQ.
  - Real protocol DOCUMENTS. Most of these cohorts' `Protocol` fields hold a
    protocol *name* like `P.BMC-240301-V1_ZapR-BMC.pdf`, which shipped code
    already resolves: extract_protocol_refs_from_metadata matches
    `^P\\.[A-Za-z0-9._-]+$` as a `protocol_name` ref, and fetch_protocols
    resolves it through the NExtSEEK API. Those documents get downloaded and
    text-extracted exactly as they would in a normal run.
  - Genuine prose (e.g. "rRNA-depleted total RNA library prep (NEB Ultra II
    ...)"), which is NOT a document and so never becomes a protocol. It is
    attached separately, in full, by protocol_prose — metadata_summary
    truncates every value to 120 characters and would cut it mid-sentence.

Credentials come from the curation .env on this machine and are never printed,
passed on a command line, or copied into the container.

Run it from `chat_nextseek/` — that is the uv project whose environment can
both import `chat_nextseek` and extract PDFs; the outer repo's environment
cannot (its `mysqlclient` build fails on this host):

    cd chat_nextseek && uv run python evals/build_prod_digests.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

EVALS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVALS_DIR.parent
sys.path.insert(0, str(EVALS_DIR))

# ---------------------------------------------------------------------------
# ChatConfig always builds an LLM client, and every MODEL_MODE raises at
# construction without its credential (mixed/gcp -> GCP_API_KEY, anth/aws ->
# AWS_BEARER_TOKEN_BEDROCK, oai -> OPENAI_API_KEY). This script MAKES NO LLM
# CALLS AT ALL — build_sample_digest is pure fetch-and-summarise ("Costs zero
# LLM tokens", sample_digest.py) — so the client it builds is never used.
#
# These placeholders exist only to get past that construction check on a host
# that has no model credentials. setdefault, so a real key already in the
# environment always wins. The model call happens later, in the container,
# where the real Bedrock credentials live.
# ---------------------------------------------------------------------------
os.environ.setdefault("NEXTSEEK_MODE", "gcp")
os.environ.setdefault("GCP_API_KEY", "placeholder-this-script-makes-no-llm-calls")
os.environ.setdefault("CATALOG_FILE", str(PROJECT_ROOT / "agent_model_catalog.json"))

from chat_nextseek.config import ChatConfig  # noqa: E402
from chat_nextseek.pipeline.sample_digest import DigestError, build_sample_digest  # noqa: E402
from protocol_prose import attach_protocol_prose, collect_protocol_prose  # noqa: E402
from run_team_questions import RESOLVED_SAMPLE_CAP, stratified_cap  # noqa: E402

PROD_BASE = "https://nextseek.mit.edu"
ENV_PATH = Path(os.environ.get("NEXTSEEK_EVAL_ENV_FILE", ".env"))
QUESTIONS = EVALS_DIR / "team_questions.json"
OUT = EVALS_DIR / "prod_digests.json"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in ENV_PATH.read_text().splitlines():
        line = raw.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main() -> int:
    env = load_env()
    config = ChatConfig(config_map={
        "NEXTSEEK_BASE_URL": PROD_BASE,
        "API_USER": env["NEXTSEEK_USERNAME"],
        "API_PASS": env["NEXTSEEK_PASSWORD"],
    })
    print(f"[build_prod_digests] base={PROD_BASE} user={env['NEXTSEEK_USERNAME']!r}")

    questions = json.loads(QUESTIONS.read_text())
    out: list[dict[str, Any]] = []

    for q in questions:
        qid, uids = q["id"], q["uids"]
        uids_used = stratified_cap(sorted(uids), RESOLVED_SAMPLE_CAP)
        print(f"\n[build_prod_digests] {qid}: {len(uids_used)} of {len(uids)} UIDs -> digest")
        entry: dict[str, Any] = {
            "id": qid, "question": q["question"],
            "n_uids_supplied": len(uids), "uids_used": uids_used,
        }
        try:
            digest = build_sample_digest(config, uids_used)
            prose = collect_protocol_prose(config, uids_used)
            digest = attach_protocol_prose(digest, prose)
        except DigestError as exc:
            entry["error"] = f"DigestError: {exc}"
            out.append(entry)
            print(f"   ERROR: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - live network, record and continue
            entry["error"] = f"{type(exc).__name__}: {exc}"
            out.append(entry)
            print(f"   ERROR: {type(exc).__name__}: {exc}")
            continue

        protocols = digest.get("protocols") or {}
        chars = sum(
            len(att.get("text") or "")
            for p in protocols.values() for att in (p.get("attachments") or [])
        )
        n_ok = sum(
            1 for p in protocols.values()
            if any(a.get("extraction") == "ok" for a in (p.get("attachments") or []))
        )
        types = sorted((digest.get("metadata_summary") or {}).get("by_sample_type") or {})
        entry["digest"] = digest
        out.append(entry)
        print(f"   sample types: {types}")
        print(f"   protocol DOCUMENTS: {n_ok}/{len(protocols)} readable, {chars:,} chars extracted")
        print(f"   protocol PROSE: {prose['n_free_text']} field(s) on "
              f"{sorted(prose['by_sample_type']) or '—'}")

    OUT.write_text(json.dumps(out, indent=2) + "\n")
    print(f"\n[build_prod_digests] wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
