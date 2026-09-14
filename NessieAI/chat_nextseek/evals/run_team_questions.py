#!/usr/bin/env python3
"""Ask the five real questions the team supplied (`team_questions.json`,
663 UIDs total, `{id, question, uids, note}`) against the live pipeline
selection payload, and record what the model chooses — with no ground truth
and no scoring. There is nothing to grade here; the deliverable is what was
chosen and why.

## The UID problem this runner exists to handle

As of this writing, NONE of the 663 supplied UIDs resolve against either
reachable NExtSEEK instance. Verified directly:

  - `http://127.0.0.1:8000` (the local docker stack) only holds `SHA`,
    `GRI`, `SHO` studies — none of the team's UIDs are in those studies.
  - `https://nextseek-dev.mit.edu` authenticates fine and returns
    `{"detail":"No samples found for provided UIDs"}` for every one of the
    team's UIDs, while a known-good control (`D.SEQ-221031SHA-60-PUB`)
    returns 6 samples on the same host.

The samples live on some other NExtSEEK instance the user has not yet named.
So this runner:

  1. Never assumes UIDs will resolve. It always attempts resolution first,
     in batches, and reports exactly how many of each question's UIDs came
     back — never silently dropping the ones that didn't.
  2. Chooses its mode PER QUESTION, automatically: sample context is built
     from whichever UIDs resolved (if any); if none resolved, the payload
     carries an explicitly empty digest and the result is marked
     `sample_context: false` so it can never be mistaken for a data-fit
     answer.
  3. Takes `--base-url` so a future run can point at the instance that
     actually holds these samples without editing any config file.

## Two modes, chosen automatically per question

  - WITH sample context: at least one UID resolved. The digest is built
    from the resolved UIDs only, capped at 12 (deterministic: sorted,
    first 12 — same cap philosophy as run_datafit_eval.py's SAMPLE_CAP,
    chosen here to bound payload size the same way).
  - WITHOUT sample context: zero UIDs resolved. The selection payload is
    built with an explicitly empty digest (atlas + docs + schemas are
    still present — only the SAMPLE DIGEST section is empty) so the model
    is judging the question's wording alone. This is a real, useful
    baseline, but it is NOT a data-fit result, and every such record is
    stamped `sample_context: false`.

## No scoring

There is no ground truth for these five questions — they are not drawn from
`groundtruth_cohorts.json`, and three of them are known (per the task brief
that produced this eval) to mix raw sequencing UIDs with already-computed
analysis-product UIDs (A.GEX, A.SCXP, A.SPTX, A.CNV), which nf-core RNA
pipelines cannot consume directly (they all require FASTQ). Two more look
like they may fall outside the RNA atlas entirely (unsupervised clustering
across mixed sample types; cell-typing from an existing matrix). This runner
does not pre-judge any of that — it reports exactly what the model returns,
including a refusal, for each question as asked.

Must run inside the nextseek container, where API + model credentials live:

    docker compose exec -T nextseek uv run python /app/chat_nextseek/evals/run_team_questions.py

Point at a different NExtSEEK instance once one is known:

    docker compose exec -T nextseek uv run python /app/chat_nextseek/evals/run_team_questions.py \\
        --base-url https://<the-real-instance>

Writes demo-output-team/results.json (full per-question records) and
demo-output-team/summary.json (compact per-question table + resolution
totals).
"""
from __future__ import annotations

import argparse
import collections
import itertools
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from chat_nextseek.config import ChatConfig
from chat_nextseek.pipeline.sample_digest import DigestError, build_sample_digest
from chat_nextseek.pipeline.selection_context import (
    DEFAULT_MAX_TOKENS,
    SECTION_NAMES,
    build_selection_context,
)
from chat_nextseek.reports.metadata import build_metadata_summary, fetch_reporter_metadata

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fdh_digest import build_seek_digest, load_question_samples  # noqa: E402
from protocol_prose import attach_protocol_prose, collect_protocol_prose  # noqa: E402

EVALS_DIR = Path(__file__).resolve().parent
QUESTIONS_PATH = EVALS_DIR / "team_questions.json"
DEFAULT_OUTPUT_DIR = EVALS_DIR / "demo-output-team"

#: UIDs are resolved in batches rather than one giant request — the largest
#: question here carries 423 UIDs, and batching keeps any single request
#: (and its timeout/error surface) bounded regardless of question size.
UID_BATCH_SIZE = 100

#: Cap on how many RESOLVED UIDs feed the digest, taken as the first N of the
#: sorted resolved set — deterministic, and bounds payload size the same way
#: run_datafit_eval.py's SAMPLE_CAP does. Named separately because that
#: constant caps a whole cohort's known UIDs; this one caps a subset that
#: survived resolution, which is a different quantity.
RESOLVED_SAMPLE_CAP = 12


def stratified_cap(uids: list[str], cap: int) -> list[str]:
    """Take up to `cap` UIDs spread evenly across their study stems, instead of
    the first `cap` lexicographically.

    A plain `sorted(...)[:cap]` silently collapses a multi-study cohort onto
    whichever study sorts first. Concretely: `retained-introns` resolves 21
    `240122LEV` samples and 27 `260527WHI` ones, and the lexicographic cap
    returns twelve LEV samples and zero WHI — and the two studies do not even
    carry the same fields (the LEV records leave LibraryStrategy/LibrarySource/
    LibrarySelection empty, the WHI records populate them). The model would
    have profiled half the cohort while being asked about all of it.

    Round-robin over stems sorted by name, taking each stem's UIDs in sorted
    order, so the result is deterministic and every study is represented.
    """
    by_stem: dict[str, list[str]] = {}
    for uid in sorted(uids):
        parts = uid.split("-")
        by_stem.setdefault(parts[1] if len(parts) > 1 else uid, []).append(uid)

    picked: list[str] = []
    for i in range(max((len(v) for v in by_stem.values()), default=0)):
        for stem in sorted(by_stem):
            if i < len(by_stem[stem]) and len(picked) < cap:
                picked.append(by_stem[stem][i])
        if len(picked) >= cap:
            break
    return picked

# ---------------------------------------------------------------------------
# Reused verbatim from run_datafit_eval.py: same system prompt, same JSON
# response contract, same tolerant JSON extraction. Do not fork these — if
# the contract changes, it should change in one place.
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the pipeline-selection judgement for NExtSEEK's Nessie assistant.

You will be given four sections of context, in this order:
  1. PIPELINE ATLAS — curated notes on nf-core RNA pipelines: what each one
     answers, what it assumes about the library prep, and how to tell it
     apart from pipelines that accept the same FASTQ files but answer a
     different question.
  2. SAMPLE DIGEST — what is actually known about THIS cohort's samples and
     protocols (metadata fields, grouping candidates, full protocol text).
  3. NF-CORE PIPELINE DOCS — each rich pipeline's own README, usage guide,
     and output guide.
  4. NF-CORE SCHEMAS — the live parameter schemas for the pipelines with a
     full schema fetched. Some pipelines only have atlas prose and no
     schema; that does not rule them out, it just means you have less to
     check.

Then you will be given a scientist's QUESTION. Decide which nf-core
pipeline(s), by their atlas key (e.g. "rnaseq", "rnasplice", "scrnaseq"),
genuinely fit the samples described in the digest.

Some sample sets will not match any pipeline in your atlas at all — the
digest may describe a library strategy, sequencing type, or assay that none
of your RNA pipelines are built for. When that is true, say so and return an
empty pipelines list. Refusing is a correct, expected answer when nothing
fits — it is not a hedge, and it is not something to avoid. Never force a
pipeline onto samples it does not match just to give a non-empty answer.

Respond with ONLY a single JSON object, no markdown code fence, no text
before or after it, in exactly this shape:

{"pipelines": ["<atlas key>", ...], "reason": "<one sentence>"}

`pipelines` must be a list of atlas pipeline keys, and MAY be empty if no
pipeline fits. `reason` must be one sentence explaining the choice (or the
refusal)."""

USER_TEMPLATE = """{payload}

## QUESTION

{question}

Respond with ONLY the JSON object described in your instructions."""


def _extract_json_object(text: str) -> dict[str, Any]:
    """Pull the first balanced JSON object out of a model response, tolerating
    a leading/trailing markdown code fence or stray prose around it."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in model response: {text!r}")
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(cleaned, start)
    if not isinstance(obj, dict):
        raise ValueError(f"Top-level JSON is not an object: {obj!r}")
    return obj


def query_model(client, model: str, budget: int | None, payload: str, question: str) -> dict[str, Any]:
    """Ask the model which pipeline(s), if any, fit `question` given `payload`.
    Returns {"chosen": [...], "reason": str, "raw_content": str, "parse_error": str|None}.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(payload=payload, question=question)},
    ]
    resp = client.chat(model=model, temperature=0, messages=messages, thinking_budget=budget)
    raw_content = resp.content or ""

    try:
        parsed = _extract_json_object(raw_content)
        chosen = parsed.get("pipelines")
        if not isinstance(chosen, list) or not all(isinstance(p, str) for p in chosen):
            raise ValueError(f"'pipelines' is not a list of strings: {chosen!r}")
        reason = parsed.get("reason", "")
        return {"chosen": chosen, "reason": reason, "raw_content": raw_content, "parse_error": None}
    except (ValueError, json.JSONDecodeError) as exc:
        return {"chosen": [], "reason": "", "raw_content": raw_content, "parse_error": str(exc)}


# ---------------------------------------------------------------------------
# UID resolution
# ---------------------------------------------------------------------------


def _empty_digest(n_supplied: int) -> dict[str, Any]:
    """Same shape build_sample_digest returns, describing zero samples. Used
    for the no-sample-context mode so build_selection_context sees an
    explicitly empty SAMPLE DIGEST section rather than skipping it.

    The `data_availability` block leads, for the same reason fdh_digest.py's
    `protocol_availability` does: an empty metadata_summary is a silence, and a
    model reading silence will fill it in. Say plainly that there is no sample
    evidence and no protocol document, so the answer is made — and can be read
    — as a judgement about the question's wording, not about these samples.
    """
    return {
        "data_availability": {
            "n_samples_resolved": 0,
            "n_uids_supplied": n_supplied,
            "n_protocol_documents": 0,
            "note": (
                f"NO SAMPLE DATA IS AVAILABLE for this question. All {n_supplied} supplied UIDs "
                "were looked for and none could be retrieved from any reachable instance, so "
                "there is no sample metadata and no protocol document to read — the sections "
                "below are empty because nothing was found, not because nothing was requested. "
                "Answer from the scientist's question itself and from the pipeline atlas and "
                "docs, choose the pipeline that best fits what they are asking for, and state "
                "explicitly in your reasoning that you had no sample data or protocol to "
                "confirm it against."
            ),
        },
        "n_uids": 0,
        "n_uids_supplied_but_unresolved": n_supplied,
        "metadata_summary": {"by_sample_type": {}, "lineage_edges": []},
        "grouping_candidates": {"by_sample_type": {}, "lineage_edges": []},
        "protocols": {},
        "protocol_text_status": {"n_protocols": 0, "n_ok": 0, "n_failed": 0, "failure_reasons": []},
    }


def protocol_report(digest: dict[str, Any]) -> dict[str, Any]:
    """What the USER should be told about protocol coverage for this question,
    independent of whether the model chose to mention it.

    The first run of these questions showed why this is needed: the digest
    carried an explicit "no protocol document" note asking the model to say so
    in its reasoning, and none of the four answers mentioned it. Protocol
    coverage is a fact about the evidence, so it is reported from the digest
    itself rather than recovered from the model's prose.
    """
    status = digest.get("protocol_text_status") or {}
    n_total = status.get("n_protocols", 0)
    n_ok = status.get("n_ok", 0)
    n_failed = status.get("n_failed", 0)
    chars = sum(
        len(att.get("text") or "")
        for p in (digest.get("protocols") or {}).values()
        for att in (p.get("attachments") or [])
    )
    # Why a SOP yielded no text matters: _classify_extraction can only say
    # "no attachments found", which reads as "the document is empty" when the
    # real cause is an HTTP 403 — the document exists and the account may not
    # view it. That is an access problem someone can fix, not a data gap.
    http = collections.Counter()
    for p in (digest.get("protocols") or {}).values():
        code = ((p.get("payload") or {}).get("status_code"))
        if code and int(code) >= 400:
            http[int(code)] += 1

    prose = digest.get("protocol_prose") or {}
    n_prose = prose.get("n_free_text", 0)
    prose_chars = sum(
        len(t) for vals in (prose.get("by_sample_type") or {}).values() for t in vals
    )
    prose_types = sorted((prose.get("by_sample_type") or {}))

    if n_total == 0 and not n_prose:
        state = "NONE — no SOP document and no free-text protocol anywhere in the lineage"
    elif n_total == 0:
        state = (f"PROSE ONLY — no SOP document, but {n_prose} free-text Protocol field(s) "
                 f"on {', '.join(prose_types)} ({prose_chars:,} chars)")
    elif n_ok == 0:
        why = ""
        if http:
            reason = {403: "403 FORBIDDEN — the account may not view them",
                      404: "404 not found"}
            why = " (" + "; ".join(
                f"{n}x {reason.get(c, f'HTTP {c}')}" for c, n in sorted(http.items())
            ) + ")"
        state = f"UNREADABLE — {n_failed}/{n_total} SOPs referenced but no text extracted{why}"
        if n_prose:
            state += f"; falling back to {n_prose} free-text Protocol field(s) ({prose_chars:,} chars)"
    elif n_failed:
        state = f"PARTIAL — {n_ok}/{n_total} SOPs readable ({chars:,} chars)"
    else:
        state = f"OK — {n_ok}/{n_total} SOPs readable ({chars:,} chars)"
        if n_prose:
            state += f" + {n_prose} free-text field(s)"
    return {
        "state": state,
        "n_protocols": n_total,
        "n_readable": n_ok,
        "n_unreadable": n_failed,
        "extracted_chars": chars,
        "n_free_text_protocol_fields": n_prose,
        "free_text_chars": prose_chars,
        "free_text_sample_types": prose_types,
        "sop_http_errors": dict(http),
        "failure_reasons": status.get("failure_reasons") or [],
        # No protocol evidence AT ALL — neither a readable SOP nor prose.
        "decision_made_without_any_protocol": n_ok == 0 and not n_prose,
        "decision_made_without_sop_text": n_ok == 0,
    }


def resolve_uids(config: ChatConfig, uids: list[str]) -> dict[str, Any]:
    """Fetch metadata for `uids` in batches of UID_BATCH_SIZE and report which
    of the SUPPLIED uids actually came back. Never drops an unresolved UID
    silently: every one not found in any batch's response is recorded.

    Returns:
        {n_supplied, n_resolved, resolved_uids, n_unresolved,
         unresolved_examples, batches}
    """
    resolved: set[str] = set()
    batch_records: list[dict[str, Any]] = []

    for start in range(0, len(uids), UID_BATCH_SIZE):
        batch = uids[start : start + UID_BATCH_SIZE]
        batch_resolved: set[str] = set()
        ok = False
        status_code = None
        error = None
        try:
            metadata = fetch_reporter_metadata(config, batch)
            ok = bool(isinstance(metadata, dict) and metadata.get("ok"))
            status_code = metadata.get("status_code") if isinstance(metadata, dict) else None
            if not ok:
                error = (metadata or {}).get("error") or (metadata.get("data") if isinstance(metadata, dict) else None)
            else:
                summary = build_metadata_summary({"__batch__": metadata})
                uid_index = summary.get("_uid_index") or {}
                batch_resolved = set(batch) & set(uid_index.keys())
                resolved |= batch_resolved
        except Exception as exc:  # noqa: BLE001 - live network call, record and continue
            error = f"{type(exc).__name__}: {exc}"

        batch_records.append({
            "batch_start": start,
            "batch_size": len(batch),
            "ok": ok,
            "status_code": status_code,
            "error": error,
            "n_resolved_in_batch": len(batch_resolved),
        })

    unresolved = sorted(set(uids) - resolved)
    return {
        "n_supplied": len(uids),
        "n_resolved": len(resolved),
        "resolved_uids": sorted(resolved),
        "n_unresolved": len(unresolved),
        "unresolved_examples": unresolved[:10],
        "batches": batch_records,
    }


# ---------------------------------------------------------------------------
# Tiebreak lookup
# ---------------------------------------------------------------------------


def tiebreak_questions(atlas: dict[str, Any], chosen: list[str]) -> list[dict[str, Any]] | None:
    """For an answer naming more than one pipeline, look up each pair's
    ask_user tiebreak question from the atlas's `versus` entries
    (pipelines[a].versus[b].ask_user). Checks both directions — the atlas
    documents a `versus` pair from only one side, never both — and records
    which side actually carried the entry. Returns None when fewer than two
    distinct pipelines were named."""
    distinct = sorted(set(chosen))
    if len(distinct) < 2:
        return None

    pipelines = atlas.get("pipelines") or {}
    pairs: list[dict[str, Any]] = []
    for a, b in itertools.combinations(distinct, 2):
        entry = (pipelines.get(a) or {}).get("versus", {}).get(b)
        found_on = f"{a}.versus.{b}" if entry is not None else None
        if entry is None:
            entry = (pipelines.get(b) or {}).get("versus", {}).get(a)
            found_on = f"{b}.versus.{a}" if entry is not None else None
        pairs.append({
            "pair": [a, b],
            "found_on": found_on,
            "ask_user": (entry or {}).get("ask_user"),
            "differs_by": (entry or {}).get("differs_by"),
        })
    return pairs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _load_questions(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--questions", type=Path, default=QUESTIONS_PATH, help="Path to team_questions.json")
    parser.add_argument(
        "--sections", default=None, metavar="A,B",
        help="Payload sections to send, from: " + ",".join(SECTION_NAMES) + ". Default: all four. "
             "Use atlas,digest,schemas to ask the same questions without the ~100k tokens of "
             "nf-core documentation, which over 30 graded questions bought no accuracy.",
    )
    parser.add_argument(
        "--repeats", type=int, default=1, metavar="N",
        help="Ask each question N times. Temperature 0 is NOT deterministic on this model -- a "
             "byte-identical payload has returned both a pipeline and a refusal -- so N=1 cannot "
             "distinguish a real answer from a lucky draw. These five have no ground truth, so "
             "the spread across repeats IS the result.",
    )
    parser.add_argument(
        "--only", action="append", metavar="ID", default=None,
        help="Run only this question id (repeatable). Each question is one model call over a "
             "~157k-token payload, so re-checking a single question after a change should not "
             "pay for the other four. The summary's totals then cover the selected subset only.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory for results")
    parser.add_argument("--agent", default="pipeline_agent", help="Registered agent name to pull model/client from")
    parser.add_argument(
        "--base-url", default=None,
        help="Override the NExtSEEK API base URL for this run only (e.g. https://nextseek-dev.mit.edu). "
             "Default: the configured NEXTSEEK_BASE_URL (NEXTSEEK_INTERNAL_BASE_URL if set).",
    )
    parser.add_argument(
        "--digests", type=Path, default=None,
        help="Path to prod_digests.json (from build_prod_digests.py). Uses those pre-built "
             "digests instead of fetching. Production NExtSEEK holds every team study but "
             "rejects the container's demo credentials, so the digest is built on the host "
             "and only the finished digest is carried in.",
    )
    parser.add_argument(
        "--seek-samples", type=Path, default=None,
        help="Path to team_question_samples.json (from ns-published-fdh/pull_team_uids.py). "
             "When given, sample context comes from those FAIRDOM SEEK records instead of the "
             "NExtSEEK API — the samples are not on any NExtSEEK instance. Questions with zero "
             "records are skipped rather than run without sample context.",
    )
    args = parser.parse_args()

    sections = None
    if args.sections:
        sections = [x.strip() for x in args.sections.split(",") if x.strip()]
        unknown = [x for x in sections if x not in SECTION_NAMES]
        if unknown:
            parser.error(f"--sections: unknown section(s): {', '.join(unknown)}")

    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # Credentials for a non-default instance come from the environment, never
    # from a CLI flag — an argv password is visible in the container's process
    # list to anything that can read /proc.
    config_map: dict[str, Any] = {}
    if args.base_url:
        config_map["NEXTSEEK_BASE_URL"] = args.base_url
    api_user = os.environ.get("NS_API_USER")
    api_pass = os.environ.get("NS_API_PASS")
    if api_user and api_pass:
        config_map["API_USER"] = api_user
        config_map["API_PASS"] = api_pass
        print("[run_team_questions] using NS_API_USER/NS_API_PASS from the environment "
              f"(user {api_user!r}) instead of the configured credentials")
    config = ChatConfig(config_map=config_map)
    print(f"[run_team_questions] NEXTSEEK_BASE_URL in use: {config.NEXTSEEK_BASE_URL!r}"
          f"{' (overridden via --base-url)' if args.base_url else ' (configured default)'}")

    client, model, budget = config.get_agent_model(args.agent)
    print(f"[run_team_questions] Using agent '{args.agent}' -> provider={getattr(client, 'provider', '?')} model={model}")

    questions = _load_questions(args.questions)
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {q["id"] for q in questions}
        if unknown:
            # A typo'd id would otherwise run zero questions and report a clean
            # exit, which reads exactly like a run that found nothing to say.
            parser.error(f"--only: no such question id: {', '.join(sorted(unknown))}")
        questions = [q for q in questions if q["id"] in wanted]
        print(f"[run_team_questions] --only: {len(questions)} of "
              f"{len(_load_questions(args.questions))} question(s) selected "
              f"({', '.join(q['id'] for q in questions)})")
    print(f"[run_team_questions] {len(questions)} question(s) to run, "
          f"{sum(len(q['uids']) for q in questions)} UIDs total, batch size={UID_BATCH_SIZE}\n")

    digests_by_id: dict[str, dict[str, Any]] = {}
    if args.digests:
        digests_by_id = {e["id"]: e for e in json.loads(args.digests.read_text())}
        n_ready = sum(1 for e in digests_by_id.values() if e.get("digest"))
        print(f"[run_team_questions] pre-built digests: {args.digests}")
        print(f"[run_team_questions] {n_ready}/{len(digests_by_id)} question(s) have a digest "
              f"(built on the host against production NExtSEEK)\n")

    seek_by_id: dict[str, dict[str, Any]] = {}
    if args.seek_samples:
        seek_by_id = {e["key"]: e for e in load_question_samples(args.seek_samples)}
        n_ready = sum(1 for e in seek_by_id.values() if e.get("samples"))
        print(f"[run_team_questions] SEEK sample source: {args.seek_samples}")
        print(f"[run_team_questions] {n_ready}/{len(seek_by_id)} question(s) have records; "
              f"the rest are answered from the question and atlas alone, stamped "
              f"sample_context: false\n")

    results: list[dict[str, Any]] = []

    for q in questions:
        qid = q["id"]
        question_text = q["question"]
        uids = q["uids"]
        note = q.get("note", "")

        if args.digests:
            entry = digests_by_id.get(qid) or {}
            prebuilt = entry.get("digest")
            n_used = len(entry.get("uids_used") or [])
            resolution = {
                "n_supplied": len(uids),
                "n_resolved": entry.get("n_uids_resolved", len(uids) if prebuilt else 0),
                "resolved_uids": entry.get("uids_used") or [],
                "n_unresolved": 0 if prebuilt else len(uids),
                "unresolved_examples": [], "batches": [],
            }
            print(f"[run_team_questions] {qid}: pre-built digest over {n_used} UID(s)"
                  + ("" if prebuilt else " — MISSING, answering context-free"))
        elif args.seek_samples:
            entry = seek_by_id.get(qid) or {}
            records = entry.get("samples") or {}
            if not records:
                # Answered anyway, from the question and the atlas alone, and
                # stamped sample_context: false. A bare skip tells the reader
                # nothing; an answer they can see was made without evidence
                # tells them what the atlas alone supports.
                print(f"[run_team_questions] {qid}: 0 of {len(uids)} UIDs found — answering from "
                      f"the question and atlas alone, NO sample data, NO protocol")
            resolution = {
                "n_supplied": len(uids), "n_resolved": len(records),
                "resolved_uids": sorted(records), "n_unresolved": len(uids) - len(records),
                "unresolved_examples": (entry.get("missing") or [])[:10], "batches": [],
            }
            if records:
                print(f"[run_team_questions] {qid}: {len(records)}/{len(uids)} UID(s) from SEEK records")
        else:
            print(f"[run_team_questions] {qid}: resolving {len(uids)} UID(s)...")
            resolution = resolve_uids(config, uids)
        n_resolved = resolution["n_resolved"]
        if not args.seek_samples:
            print(f"  resolved {n_resolved}/{resolution['n_supplied']}"
                  + (f" (examples not resolved: {resolution['unresolved_examples']})" if resolution["n_unresolved"] else ""))

        record: dict[str, Any] = {
            "id": qid,
            "question": question_text,
            "note": note,
            "n_uids_supplied": resolution["n_supplied"],
            "n_uids_resolved": n_resolved,
            "n_uids_unresolved": resolution["n_unresolved"],
            "unresolved_examples": resolution["unresolved_examples"],
            "resolution_batches": resolution["batches"],
        }

        sample_context = n_resolved > 0
        if args.digests:
            sample_context = bool((digests_by_id.get(qid) or {}).get("digest"))
        record["sample_context"] = sample_context

        if sample_context:
            uids_used = (
                stratified_cap(resolution["resolved_uids"], RESOLVED_SAMPLE_CAP)
                if args.seek_samples
                else sorted(resolution["resolved_uids"])[:RESOLVED_SAMPLE_CAP]
            )
            record["uids_used_for_digest"] = uids_used
            record["n_uids_used_for_digest"] = len(uids_used)
            record["n_uids_available_resolved"] = n_resolved
            try:
                if args.digests:
                    digest = (digests_by_id[qid])["digest"]
                elif args.seek_samples:
                    digest = build_seek_digest(
                        config, {u: records[u] for u in uids_used}
                    )
                else:
                    digest = build_sample_digest(config, uids_used)
                    # Free-text Protocol prose is not a SOP document, so the
                    # digest path never captures it — and metadata_summary
                    # truncates it to 120 chars. On these cohorts that prose IS
                    # the protocol evidence, so attach it in full.
                    digest = attach_protocol_prose(
                        digest, collect_protocol_prose(config, uids_used)
                    )
            except DigestError as exc:
                record["error"] = f"DigestError: {exc}"
                results.append(record)
                print(f"  [ERROR] {qid}: digest build failed — {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - live network call, record and continue
                record["error"] = f"{type(exc).__name__}: {exc}"
                results.append(record)
                print(f"  [ERROR] {qid}: unexpected error building digest — {type(exc).__name__}: {exc}")
                continue
        else:
            digest = _empty_digest(resolution["n_supplied"])
            record["uids_used_for_digest"] = []
            record["n_uids_used_for_digest"] = 0
            record["n_uids_available_resolved"] = 0

        # Protocol coverage is reported from the digest, not from the model's
        # prose — the model may simply not mention it (observed: all four
        # answers in the first SEEK run stayed silent about it).
        record["protocol_report"] = protocol_report(digest)
        print(f"    PROTOCOLS: {record['protocol_report']['state']}")

        # Uncapped first so we always get a size report, even for a payload
        # that would exceed the ceiling; apply the real ceiling ourselves
        # before deciding whether to call the model (same pattern as
        # run_datafit_eval.py).
        ctx = build_selection_context(config=config, uids=[], digest=digest, max_tokens=10**12)
        size_report = ctx.size_report(sections)
        record["payload_size_report"] = size_report

        if size_report["est_tokens"] > DEFAULT_MAX_TOKENS:
            record["error"] = (
                f"PayloadTooLargeError: {size_report['est_tokens']:,} est. tokens "
                f"exceeds ceiling of {DEFAULT_MAX_TOKENS:,}"
            )
            results.append(record)
            print(f"  [ERROR] {qid}: payload too large — {size_report['est_tokens']:,} tokens "
                  f"(ceiling {DEFAULT_MAX_TOKENS:,})")
            continue

        payload = ctx.to_prompt_text(sections)
        runs = []
        for _ in range(args.repeats):
            outcome = query_model(client, model, budget, payload, question_text)
            runs.append(outcome)
        record["runs"] = runs
        record["n_repeats"] = len(runs)
        # With no ground truth, "did it give the same answer twice" is the only
        # quality signal available, so it is recorded per question rather than
        # collapsed into a single chosen list.
        record["answers"] = [", ".join(r["chosen"]) or "refused" for r in runs]
        record["stable"] = len(set(record["answers"])) == 1
        first = runs[0]
        record["chosen"] = first["chosen"]
        record["reason"] = first["reason"]
        record["raw_content"] = first["raw_content"]
        record["parse_error"] = first["parse_error"]

        tiebreak = None
        if not first["parse_error"]:
            tiebreak = tiebreak_questions(ctx.atlas, first["chosen"])
        record["tiebreak"] = tiebreak

        results.append(record)
        # `outcome` is now the last iteration of the repeats loop; use the
        # record's own first run so the printed line matches what was stored.
        chosen_display = " | ".join(sorted(set(record["answers"]))) if record.get("answers") else (
            ", ".join(record.get("chosen") or []) or "(empty/refused)")
        if not record.get("stable", True):
            chosen_display += "  [unstable]"
        context_display = "WITH sample context" if sample_context else "NO sample context (wording only)"
        print(f"  [{context_display}] chosen={chosen_display}")
        if outcome.get("reason"):
            print(f"    reason: {outcome['reason']!r}")
        if tiebreak:
            for pair in tiebreak:
                print(f"    tiebreak {pair['pair']}: {pair['ask_user']!r}")

    # ------------------------------------------------------------------
    # Summary table
    # ------------------------------------------------------------------
    print("\n" + "=" * 140)
    print(f"{'id':<32} {'supplied':<9} {'resolved':<9} {'ctx':<5} {'chosen':<22} {'protocols'}")
    print("-" * 140)
    for r in results:
        chosen_display = ", ".join(r.get("chosen") or []) or ("(error)" if r.get("error") else "(empty)")
        proto = (r.get("protocol_report") or {}).get("state", "-")
        print(f"{r['id']:<32} {r['n_uids_supplied']:<9} {r['n_uids_resolved']:<9} "
              f"{'yes' if r['sample_context'] else 'no':<5} {chosen_display:<22} {proto}")
    print("=" * 140)

    none_at_all = [r for r in results if (r.get("protocol_report") or {}).get("decision_made_without_any_protocol")]
    no_sop = [r for r in results if (r.get("protocol_report") or {}).get("decision_made_without_sop_text")]
    print(f"\nPROTOCOL COVERAGE: {len(no_sop)} of {len(results)} answers were decided without any "
          f"readable SOP document; of those, {len(none_at_all)} had no protocol evidence of any "
          f"kind (not even free text).")
    for r in results:
        pr = r.get("protocol_report") or {}
        if pr:
            print(f"  - {r['id']}: {pr['state']}")
    if none_at_all:
        print("\n  The following answers were made with NO protocol evidence whatsoever — the "
              "library type was judged from metadata fields alone (or, where nothing resolved, "
              "from the question's wording):")
        for r in none_at_all:
            print(f"    * {r['id']}")

    total_supplied = sum(r["n_uids_supplied"] for r in results)
    total_resolved = sum(r["n_uids_resolved"] for r in results)
    n_with_context = sum(1 for r in results if r["sample_context"])

    print(f"\nTOTAL UIDs: {total_resolved}/{total_supplied} resolved across {len(results)} question(s); "
          f"{n_with_context} question(s) got sample context, {len(results) - n_with_context} did not.")
    if total_resolved == 0:
        print("NONE of the supplied UIDs resolved against this instance's NEXTSEEK_BASE_URL. "
              "Every result below reflects the question's wording alone — NOT a data-fit answer. "
              "Point --base-url at the instance holding these samples once it is known.")

    # ------------------------------------------------------------------
    # Write outputs
    # ------------------------------------------------------------------
    results_path = out_dir / "results.json"
    results_path.write_text(json.dumps({
        "agent": args.agent,
        "model": model,
        "base_url": config.NEXTSEEK_BASE_URL,
        "questions_source": str(args.questions),
        "resolved_sample_cap": RESOLVED_SAMPLE_CAP,
        "uid_batch_size": UID_BATCH_SIZE,
        "results": results,
    }, indent=2) + "\n")

    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps({
        "agent": args.agent,
        "model": model,
        "base_url": config.NEXTSEEK_BASE_URL,
        "n_questions": len(results),
        "n_with_sample_context": n_with_context,
        "n_without_sample_context": len(results) - n_with_context,
        "total_uids_supplied": total_supplied,
        "total_uids_resolved": total_resolved,
        "note": (
            "No scoring — there is no ground truth for these five questions. Any record with "
            "sample_context: false reflects the question's wording alone; it is a real baseline "
            "but must not be presented as a data-fit result. Rerun with --base-url pointed at the "
            "instance that actually holds these UIDs to get a genuine data-fit answer."
        ),
        "per_question": [
            {
                "id": r["id"],
                "question": r["question"],
                "n_uids_supplied": r["n_uids_supplied"],
                "n_uids_resolved": r["n_uids_resolved"],
                "sample_context": r["sample_context"],
                "chosen": r.get("chosen"),
                "reason": r.get("reason"),
                "protocol_report": r.get("protocol_report"),
                "tiebreak": r.get("tiebreak"),
                "error": r.get("error"),
            }
            for r in results
        ],
    }, indent=2) + "\n")

    print(f"\nFull results written to: {results_path}")
    print(f"Summary written to: {summary_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
