"""Choose an nf-core pipeline for a question, from the cohort's own evidence.

The model call here is deliberately separate from the pipeline agent's own
conversation. The evidence payload runs to roughly 84k tokens; putting it in
the agent's message history would carry that cost on every later turn of the
build. So this module makes one stateless call and returns a small verdict.

This module owns the prompt. It used to live in an eval script,
which meant the measured accuracy described a copy of the code rather than the
code that runs. The evals now import from here.

Nothing in this module raises. Every failure becomes an `out_of_scope` verdict,
because selection must never be able to block a build that works today.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

#: The payload sections the live path assembles. `docs` is deliberately absent:
#: measured over two independent 30-question sets it cost ~100k tokens and
#: scored LOWER (24/30 and 25/30 with it, 26/30 and 27/30 without).
SELECTION_SECTIONS: tuple[str, ...] = ("atlas", "digest", "schemas")

#: More than this many pipelines is not a fork, it is a model that has stopped
#: choosing. The atlas sanctions naming "two or three candidate pipelines".
MAX_FORK = 3

SYSTEM_PROMPT = """You are choosing an nf-core pipeline for a working scientist.

You will be given, in order:

  1. PIPELINE ATLAS — the pipelines you may choose from, what each answers,
     what input each requires, and how neighbouring ones differ.
  2. SAMPLE DIGEST — what is actually known about THIS cohort: its metadata
     fields, its lineage, its grouping candidates, and the full text of any
     protocol documents attached to it.
  3. NF-CORE SCHEMAS — the live parameter schemas, when fetched.

Then you will be given the scientist's QUESTION.

Decide which pipeline(s) genuinely answer THAT QUESTION on THESE SAMPLES.
Both halves matter. A pipeline that fits the data but does not answer the
question is wrong, and so is a pipeline that answers the question but cannot
run on this cohort's data.

Judge what the library can support, not only what it is called. A library
that sequences only one end of each transcript cannot answer a question about
isoforms; a library with no size selection cannot answer a question about
small RNAs; a species with no reference bundle cannot be run at all. The
protocol text is often the only place the preparation is described.

Judge, too, how many samples one library holds. Some plate-based 3' methods
(prime-seq, BRB-seq and similar) give each sample its own barcode on the
oligo(dT) primer at reverse transcription and then pool many samples into one
library, so each FASTQ holds many samples, often under different conditions.
The metadata records such a library exactly like ordinary polyA RNA-Seq; only
the protocol text says it was pooled. Treat it as a barcode-aware,
single-cell-style library: never name rnaseq for it, because rnaseq would count
each pool as one sample and mix every condition in it; scrnaseq's barcode-aware
counting (kallisto/bustools) splits the pools into one count table per sample.
Set "sample_pools" to true when the protocol says this. A 3' tag-counting
library made one sample per library (QuantSeq, a UMI 3' RNA-Seq kit) is not a
pool, and neither are the cell barcodes of a single-cell library: for those,
"sample_pools" is false.

Return an empty pipelines list when nothing fits, and say which of two
different things is true in "basis":

  - "data": a pipeline in the atlas does the kind of analysis asked for, but
    THIS cohort's data cannot support it (the library, the molecule, the
    species). This is a refusal, and a correct, expected answer, not a hedge.
  - "atlas": the question asks for an analysis no pipeline in the atlas
    performs (metagenomics or Hi-C, for now), even when
    an atlas pipeline shares a word with the request (rnavar calls variants
    from RNA and sarek from DNA; neither profiles methylation). Another part
    of the system chooses from a wider catalog, so do not refuse on the
    atlas's behalf.

Equally, do not refuse merely because the pipeline's output would still need
downstream analysis; that is true of almost every correct answer.

Respond with ONLY a single JSON object, no markdown fence, no text around it:

{"pipelines": ["<atlas key>", ...], "reason": "<one sentence>", "basis": "data" | "atlas", "sample_pools": true | false}

"basis" is required when "pipelines" is empty and ignored otherwise.
"sample_pools" is true only when the protocol says several samples were
barcoded and pooled into one library."""

USER_TEMPLATE = """{payload}

## QUESTION

{question}

Respond with ONLY the JSON object described in your instructions."""


@dataclass
class Verdict:
    """One of four outcomes. `kind` is the only field callers branch on."""

    kind: str
    pipelines: list[str] = field(default_factory=list)
    reason: str = ""
    raw: str = ""
    #: Atlas pipelines the model named that the build path cannot launch. They are
    #: dropped from `pipelines`, not held against the rest of the answer.
    dropped: list[str] = field(default_factory=list)
    #: True when the choice fell to the full catalog because the request is not in the
    #: curated atlas (RNA + DNA); `asked` holds the pipeline names the model named, if any.
    atlas_miss: bool = False
    asked: list[str] = field(default_factory=list)
    #: True when the model judged each FASTQ a pool of samples barcoded at reverse
    #: transcription (prime-seq, BRB-seq). rnaseq is then never kept: see decide().
    sample_pools: bool = False


def out_of_scope(reason: str, raw: str = "", *, atlas_miss: bool = False,
                 asked: list[str] | None = None) -> Verdict:
    return Verdict(kind="out_of_scope", pipelines=[], reason=reason, raw=raw,
                   atlas_miss=atlas_miss, asked=asked or [])


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def _extract_json_object(text: str) -> dict[str, Any]:
    """Parse the first JSON object in `text`, tolerating a markdown fence.

    Raises ValueError on anything that is not a JSON object.
    """
    stripped = _FENCE.sub("", text or "").strip()
    if not stripped:
        raise ValueError("empty response")
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start == -1 or end <= start:
            raise ValueError(f"no JSON object in response: {stripped[:200]!r}") from None
        parsed = json.loads(stripped[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError(f"response is not a JSON object: {type(parsed).__name__}")
    return parsed


def decide(*, client, model: str, budget: int | None, payload: str, question: str,
           atlas_keys: set[str], launchable_keys: set[str] | None = None) -> Verdict:
    """Ask one model, once, and map its answer onto a verdict. Never raises.

    `atlas_keys` is every pipeline the payload describes; a name outside it was
    invented. `launchable_keys` is the subset the build path can run (defaults to
    all of them); an atlas pipeline outside it is dropped, not fatal.
    """
    if launchable_keys is None:
        launchable_keys = atlas_keys
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(payload=payload, question=question)},
    ]
    try:
        resp = client.chat(model=model, temperature=0, messages=messages,
                           thinking_budget=budget)
    except Exception as exc:  # noqa: BLE001 - any failure degrades, none propagates
        return out_of_scope(f"the selection model call failed: {exc}")

    raw = getattr(resp, "content", None) or ""
    try:
        parsed = _extract_json_object(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        return out_of_scope(f"the selection model did not return usable JSON: {exc}", raw)

    chosen = parsed.get("pipelines")
    if not isinstance(chosen, list) or not all(isinstance(x, str) for x in chosen):
        return out_of_scope(f"'pipelines' is not a list of strings: {chosen!r}", raw)
    reason = str(parsed.get("reason") or "").strip()

    if not chosen:
        # A refusal is a decision — but only when it says why. An unexplained
        # empty list is nothing the agent can relay, so it degrades instead.
        if not reason:
            return out_of_scope("the selection model refused without giving a reason", raw)
        # Only a refusal about THIS cohort's data may block the build. "Nothing in the
        # atlas does this" is a statement about the atlas's coverage, not about the
        # question, so it hands the choice back to the agent's full catalog; so does
        # a missing or unrecognised basis.
        basis = parsed.get("basis")
        if basis == "data":
            return Verdict(kind="refused", pipelines=[], reason=reason, raw=raw)
        if basis == "atlas":
            return out_of_scope(f"no pipeline in the atlas does this: {reason}", raw,
                                atlas_miss=True)
        return out_of_scope(
            f"the selection model declined without saying whether the data or the atlas "
            f"was the reason ({basis!r}): {reason}", raw)

    # "nf-core/rnaseq", "RNAseq " and "rnaseq" name the same pipeline; only a different pipeline is a miss.
    chosen = [k.strip().lower().removeprefix("nf-core/") for k in chosen]
    invented = [k for k in chosen if k not in atlas_keys]
    if invented:
        return out_of_scope(
            f"the selection model named {', '.join(sorted(set(invented)))}, "
            "which is not in the atlas", raw, atlas_miss=True, asked=sorted(set(invented)))

    if len(chosen) > MAX_FORK:
        return out_of_scope(
            f"the selection model named {len(set(chosen))} pipelines, which is not a choice", raw)

    named = list(dict.fromkeys(chosen))
    # A pooled, sample-barcoded library is never rnaseq (owner's ruling, 2026-10-27): rnaseq
    # would count each pool as one sample. When the model judged the library pooled and still
    # named rnaseq, scrnaseq, which splits the pools by their barcodes, takes its place.
    sample_pools = parsed.get("sample_pools") is True
    if sample_pools and "rnaseq" in named:
        swap = ["scrnaseq"] if "scrnaseq" in atlas_keys else []
        named = list(dict.fromkeys(s for k in named for s in (swap if k == "rnaseq" else [k])))
        reason = (reason + " rnaseq is set aside: each FASTQ is a pool of barcoded samples, "
                  "so it would count a pool as one sample.").strip()
        if not named:
            return out_of_scope(f"the library is a pool of barcoded samples, which rnaseq cannot "
                                f"split, and no barcode-aware pipeline is in the atlas: {reason}", raw)
    kept = [k for k in named if k in launchable_keys]
    dropped = [k for k in named if k not in launchable_keys]
    if not kept:
        # `dropped` rides along so the agent can name what would answer the question
        # instead of picking some other pipeline in its place.
        return Verdict(kind="out_of_scope", pipelines=[], raw=raw, dropped=dropped,
                       reason=f"the selection model named {', '.join(dropped)}, which is "
                              "in the atlas but cannot be launched here")

    kind = "chosen" if len(kept) == 1 else "fork"
    return Verdict(kind=kind, pipelines=kept, reason=reason, raw=raw, dropped=dropped,
                   sample_pools=sample_pools)
