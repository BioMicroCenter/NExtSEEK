"""Provider-outage detection, in a dependency-free module.

**One definition, two callers.** ``evaluate.py`` scores ordinary turns and
``consistency.py`` scores groups; both must reach the same verdict about the
same reply. This lives here rather than in ``evaluate.py`` for the same reason
``limits.py`` does — importing ``evaluate`` drags in ``e2e.criteria`` and
openpyxl, which a consistency group has no use for.

**What it detects.** When every provider in an agent's fallback chain returns
503, ``chat_nextseek/src/chat_nextseek/schemas/schema_helper.py`` raises
``LLMFatalError("All provider fallbacks exhausted — agent '<x>': ...")`` and the
orchestrator hands that string back as the turn's reply. (No line number: that
raise has already moved once, and the string is the stable part.) The turn
carries an error message where an answer should be: no parser ran, no query was
issued, no product behaviour was exercised at all.

**Why it matters.** Scored as an ordinary failure, an outage is indistinguishable
from a regression. Ten of the eighteen reds in the 2026-08-03 seed-6 run were one
Bedrock outage, so more than half that paid run's failure signal was noise that
three reviewers then spent time triaging.

**Why the phrase and not a regex.** The separator between "exhausted" and "agent"
is an em dash at the source and arrives as U+FFFD in the stored evidence, so
anchoring on anything but the phrase is a trap. The phrase itself is stable and
appears nowhere else in the product's vocabulary.

**Since 2026-09-25 (fix 5)** the reply no longer carries that phrase. An NS turn
the AI models could not answer (a 503, an empty body, a 429, a timeout or a
connection error, on the second model too when there was one) replies with the
operator's plain text, and its ``query_error`` event carries
``reason: "model_unavailable"`` with the raw message in ``detail``; the
Container-CC route uses the same reason and its own plain text. All of it is
detected here: either engine's plain text in a reply, and the reason in any event
data handed in. The old phrase still counts,
for stored runs and for the raw message wherever it surfaces.
"""
from __future__ import annotations

# The literal emitted by schema_helper.py's "all fallbacks exhausted" LLMFatalError.
# Do NOT copy this string into another PRODUCTION module: the anti-duplication
# check in tests/test_evaluate.py globs `nessie_tests/*.py` (non-recursive, by
# design) and asserts this is the only one that contains it. The fixtures under
# `nessie_tests/tests/` DO carry their own copies, deliberately — they are what
# fails loudly if the product ever rewords the message.
PROVIDER_OUTAGE_MARKER = "All provider fallbacks exhausted"

# The ``query_error`` data ``reason`` of a turn the AI models could not answer, NS or CC.
# A copy of chat_nextseek.failure_replies.MODEL_UNAVAILABLE_REASON (this module imports
# nothing from the product); tests/test_outage_model_unavailable.py pins the two equal.
MODEL_UNAVAILABLE_REASON = "model_unavailable"

# The stable opening of each engine's reply for that turn: NS (both variants, with and
# without "(we tried a second one as well)") and Container-CC (both of its variants share
# this prefix). Text-only callers, such as export.classify_error and the runner's reply
# checks, classify a turn from these alone. The planner's own failure replies ("The AI
# model that plans the search ...") are an unsupported plan, not an outage, and do not match.
MODEL_UNAVAILABLE_REPLY_MARKERS = (
    "The AI models we use were unavailable",
    "The AI model was unavailable during this turn",
)

# Recorded as the manifest entry's `reason`, so a reader of report.html or of the
# printed summary sees why the case was exempted rather than just an `error`.
OUTAGE_REASON = (
    f"provider outage: the reply carries {PROVIDER_OUTAGE_MARKER!r} or the turn ended "
    f"with reason {MODEL_UNAVAILABLE_REASON!r}; the AI models did not answer, on the "
    "fallback provider either, so no product behaviour ran"
)

# Where event data carries its text.
_TEXT_KEYS = ("error", "detail", "reply")


def _text_is_outage(text: str) -> bool:
    return PROVIDER_OUTAGE_MARKER in text or any(m in text for m in MODEL_UNAVAILABLE_REPLY_MARKERS)


def _data_is_outage(data: dict) -> bool:
    if data.get("reason") == MODEL_UNAVAILABLE_REASON:
        return True
    if any(isinstance(data.get(k), str) and _text_is_outage(data[k]) for k in _TEXT_KEYS):
        return True
    nested = data.get("data")  # a progress event: {"event": "query_error", "data": {...}}
    return isinstance(nested, dict) and nested is not data and _data_is_outage(nested)


def is_provider_outage(*items: object) -> bool:
    """True if ANY of ``items`` shows a turn the AI models could not answer.

    A string is a reply (or an error text) carrying the old exhausted-chain phrase or
    either engine's plain text. A dict is event data (a ``query_error`` payload, or a progress
    event wrapping one) whose ``reason`` is ``model_unavailable`` or whose text carries
    either. Anything else (None, an int) is simply not an outage: a resolver that
    returns something unexpected must not make the detector raise inside the run loop.
    """
    for item in items:
        if isinstance(item, str) and _text_is_outage(item):
            return True
        if isinstance(item, dict) and _data_is_outage(item):
            return True
    return False
