"""Choose an nf-core pipeline for a question, from the cohort's own evidence.

The model call here is deliberately separate from the pipeline agent's own
conversation. The evidence payload runs to roughly 84k tokens; putting it in
the agent's message history would carry that cost on every later turn of the
build. So this module makes one stateless call and returns a small verdict.

This module owns the prompt. It used to live in evals/run_question_cases.py,
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

Return an empty pipelines list when nothing fits — because the data cannot
support the question, or because no pipeline in the atlas does what was asked.
Refusing is a correct, expected answer, not a hedge. Equally, do not refuse
merely because the pipeline's output would still need downstream analysis;
that is true of almost every correct answer.

Respond with ONLY a single JSON object, no markdown fence, no text around it:

{"pipelines": ["<atlas key>", ...], "reason": "<one sentence>"}"""

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


def out_of_scope(reason: str, raw: str = "") -> Verdict:
    return Verdict(kind="out_of_scope", pipelines=[], reason=reason, raw=raw)


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
           atlas_keys: set[str]) -> Verdict:
    """Ask one model, once, and map its answer onto a verdict. Never raises."""
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
        return Verdict(kind="refused", pipelines=[], reason=reason, raw=raw)

    invented = [k for k in chosen if k not in atlas_keys]
    if invented:
        return out_of_scope(
            f"the selection model named {', '.join(sorted(set(invented)))}, "
            "which is not in the atlas", raw)

    if len(chosen) > MAX_FORK:
        return out_of_scope(
            f"the selection model named {len(set(chosen))} pipelines, which is not a choice", raw)

    kind = "chosen" if len(set(chosen)) == 1 else "fork"
    return Verdict(kind=kind, pipelines=list(dict.fromkeys(chosen)), reason=reason, raw=raw)
