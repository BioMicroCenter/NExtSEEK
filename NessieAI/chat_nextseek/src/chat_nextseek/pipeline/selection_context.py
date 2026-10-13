"""Assemble the atlas, the cohort digest, the live nf-core prose docs, and the
live nf-core schemas into one payload for pipeline selection.

Section order is deliberate. The atlas is the reasoning frame and comes first.
The digest is the evidence about these specific samples and comes second. The
pipeline docs come third: they are prose about what each pipeline *does* and
what it *returns* (its README, usage guide, and output description), which is
closer to the selection decision than a parameter list is — a model can judge
"does this pipeline answer the question" from its docs long before it needs
to know its flags. The schemas are bulky reference material — things to
consult once a hypothesis exists, not to reason from — so they come last.
Putting 32k tokens of `skip_dupradar` flags ahead of the evidence and the
docs buries the parts that actually decide the answer.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Sequence
from typing import Any, Callable

from chat_nextseek.pipeline.sample_digest import build_sample_digest
from chat_nextseek.seqera.nfcore_atlas import load_atlas
from chat_nextseek.seqera.nfcore_schema import (
    SchemaFetchError,
    get_pipeline_docs,
    get_schema,
)

#: The payload's sections, in the order they are assembled. The order is the
#: reasoning order documented in this module's docstring, not alphabetical.
SECTION_NAMES: tuple[str, ...] = ("atlas", "digest", "docs", "schemas")

#: The pipelines that get their full nf-core schemas fetched. Every other atlas
#: entry is a stub — prose only, present so the model has an explicit reason to
#: rule it out rather than defaulting to whichever entry carries more text.
RICH_PIPELINES: tuple[str, ...] = (
    "rnaseq", "scrnaseq", "smrnaseq", "hlatyping", "rnafusion", "rnasplice",
)

#: The measured payload baseline (atlas + digest + docs + schemas for all six
#: RICH_PIPELINES) is ~135k tokens. This ceiling exists so a payload that has
#: grown too large fires our own PayloadTooLargeError, with a size breakdown,
#: before the API rejects the request with an opaque error.
#:
#: It was 180_000, chosen against a stated 200k context window. That premise
#: was wrong for the model actually in use: the routed pipeline_agent model,
#: claude-opus-4-7 on Bedrock, has a 1M-token context window and a 128k output
#: cap, and Bedrock serves the full 1M. The old ceiling was not protecting a
#: real API limit — it was rejecting payloads the model would have accepted,
#: which is exactly what happened once real protocol documents were attached
#: (four of the team's five cohorts came in at 182k-203k tokens and were
#: refused before any model call).
#:
#: 850_000 leaves the 128k output cap plus ~20k for the system prompt and the
#: question inside 1M, so our error still fires first. If the routed model
#: ever changes to one with a smaller window, this must come down with it —
#: the number encodes an assumption about the model, not about the payload.
DEFAULT_MAX_TOKENS = 850_000


class PayloadTooLargeError(Exception):
    """The assembled payload exceeds the ceiling. Never truncate silently."""


@dataclass
class SelectionContext:
    atlas: dict[str, Any]
    digest: dict[str, Any]
    docs: dict[str, Any] = field(default_factory=dict)
    docs_fetch_failed: dict[str, str] = field(default_factory=dict)
    schemas: dict[str, Any] = field(default_factory=dict)
    schema_fetch_failed: dict[str, str] = field(default_factory=dict)

    def _atlas_text(self) -> str:
        return "## PIPELINE ATLAS\n" + json.dumps(self.atlas, indent=1)

    def _digest_text(self) -> str:
        header = "## SAMPLE DIGEST\n"
        notice = ""
        status = self.digest.get("protocol_text_status") if isinstance(self.digest, dict) else None
        if status and status.get("n_failed"):
            # Same rationale as the docs/schema sections' notices: a failure
            # recorded only in the per-attachment "extraction" field, deep in
            # the JSON body, is invisible in practice — a model reading empty
            # protocol text without this notice has been observed to infer a
            # library type from protocol titles/filenames anyway (scrnaseq on
            # a bulk cohort, cohort 241219BRY). Put the failure up front, in
            # plain text, and name the fallback evidence explicitly.
            reasons = ", ".join(status.get("failure_reasons") or []) or "unknown reason"
            lines = [
                f"PROTOCOL TEXT UNAVAILABLE for {status['n_failed']} of {status['n_protocols']} "
                f"protocols ({reasons}).",
                "Do not infer the library preparation from protocol titles or filenames.",
                "Judge the library type from the D.SEQ metadata instead — LibraryStrategy, "
                "LibrarySource, LibrarySelection, SequencingType, and the F_bp/R_bp read "
                "geometry — and say in your reasoning that the protocol text was unavailable.",
            ]
            notice = "\n".join(lines) + "\n"
        return header + notice + json.dumps(self.digest, indent=1)

    def _docs_text(self) -> str:
        body = {"fetched": self.docs, "failed": self.docs_fetch_failed}
        header = "## NF-CORE PIPELINE DOCS\n"
        notice = ""
        if self.docs_fetch_failed:
            # Same rationale as the schema section's notice: a failure lands
            # deep in the JSON body otherwise. Put it up front, in plain
            # text, so the model knows which pipeline it is judging without
            # its documentation.
            lines = ["NOT FETCHED — these pipelines were judged without their documentation:"]
            lines += [f"  - {pipeline}: {reason}" for pipeline, reason in self.docs_fetch_failed.items()]
            notice = "\n".join(lines) + "\n"
        return header + notice + json.dumps(body, separators=(",", ":"))

    def _schemas_text(self) -> str:
        body = {"fetched": self.schemas, "failed": self.schema_fetch_failed}
        header = "## NF-CORE SCHEMAS\n"
        notice = ""
        if self.schema_fetch_failed:
            # A failure lands after ~127,000 chars of compact JSON otherwise —
            # the least-read position in the payload. Put it up front, in
            # plain text, so the model knows which pipeline it is judging
            # without parameters.
            lines = ["NOT FETCHED — these pipelines were judged without their parameters:"]
            lines += [f"  - {pipeline}: {reason}" for pipeline, reason in self.schema_fetch_failed.items()]
            notice = "\n".join(lines) + "\n"
        return header + notice + json.dumps(body, separators=(",", ":"))

    def _section_texts(self, sections: Sequence[str] | None = None) -> dict[str, str]:
        """The chosen sections, rendered, in assembly order.

        `sections=None` means all four — the only thing any production caller
        wants. A subset is for measuring what each section is worth: an ablation
        must OMIT a section, not blank it, because an empty `{"fetched":{},
        "failed":{}}` body reads as "the docs were fetched and are empty" rather
        than "you were not given docs".
        """
        renderers = {
            "atlas": self._atlas_text,
            "digest": self._digest_text,
            "docs": self._docs_text,
            "schemas": self._schemas_text,
        }
        if sections is None:
            return {name: render() for name, render in renderers.items()}
        unknown = [s for s in sections if s not in renderers]
        if unknown:
            raise ValueError(
                f"unknown payload section(s): {', '.join(unknown)}; "
                f"expected any of {', '.join(SECTION_NAMES)}"
            )
        wanted = set(sections)
        return {name: render() for name, render in renderers.items() if name in wanted}

    def to_prompt_text(self, sections: Sequence[str] | None = None) -> str:
        return "\n\n".join(self._section_texts(sections).values())

    def size_report(self, sections: Sequence[str] | None = None) -> dict[str, Any]:
        texts = self._section_texts(sections)
        # Two characters per join, and one fewer join than there are sections.
        total = sum(len(t) for t in texts.values()) + max(0, len(texts) - 1) * 2
        return {
            **{name: len(texts.get(name, "")) for name in SECTION_NAMES},
            "sections": list(texts),
            "n_docs_fetched": len(self.docs) if "docs" in texts else 0,
            "n_docs_failed": len(self.docs_fetch_failed) if "docs" in texts else 0,
            "n_schemas_fetched": len(self.schemas) if "schemas" in texts else 0,
            "n_schemas_failed": len(self.schema_fetch_failed) if "schemas" in texts else 0,
            "total_chars": total,
            "est_tokens": total // 4,
        }


def build_selection_context(
    config,
    uids: list[str],
    *,
    base_dir: str | Path | None = None,
    atlas: dict[str, Any] | None = None,
    digest: dict[str, Any] | None = None,
    schema_getter: Callable[[str, str], dict[str, Any]] | None = None,
    docs_getter: Callable[[str, str], dict[str, str]] | None = None,
    sections: Sequence[str] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> SelectionContext:
    """Build the selection payload.

    `sections` names the sections that will actually be rendered. Anything not
    named is not fetched — dropping a section from the payload must drop its
    network cost too, or the saving is only in tokens. `None` means all four,
    which is what the ablation harness wants.

    Raises DigestError when the cohort cannot be profiled — there is no
    selection without evidence. Raises PayloadTooLargeError, with a breakdown,
    rather than handing something downstream to truncate.
    """
    wanted = set(SECTION_NAMES if sections is None else sections)
    unknown = wanted - set(SECTION_NAMES)
    if unknown:
        raise ValueError(
            f"unknown payload section(s): {', '.join(sorted(unknown))}; "
            f"expected any of {', '.join(SECTION_NAMES)}"
        )

    resolved_atlas = atlas if atlas is not None else load_atlas()
    resolved_digest = (
        digest if digest is not None
        else build_sample_digest(config, uids, base_dir=base_dir)
    )
    get = schema_getter or get_schema
    get_docs = docs_getter or get_pipeline_docs

    schemas: dict[str, Any] = {}
    failed: dict[str, str] = {}
    docs: dict[str, Any] = {}
    docs_failed: dict[str, str] = {}
    for key in RICH_PIPELINES:
        entry = resolved_atlas["pipelines"].get(key)
        if not entry:
            continue
        if "schemas" in wanted:
            try:
                schemas[key] = get(key, entry["revision"])
            except SchemaFetchError as exc:
                failed[key] = str(exc)
        if "docs" in wanted:
            try:
                docs[key] = get_docs(key, entry["revision"])
            except SchemaFetchError as exc:
                docs_failed[key] = str(exc)

    ctx = SelectionContext(
        atlas=resolved_atlas,
        digest=resolved_digest,
        docs=docs,
        docs_fetch_failed=docs_failed,
        schemas=schemas,
        schema_fetch_failed=failed,
    )

    report = ctx.size_report(sections)
    if report["est_tokens"] > max_tokens:
        raise PayloadTooLargeError(
            f"selection payload exceeds ceiling of {max_tokens} tokens: {report}"
        )
    return ctx
