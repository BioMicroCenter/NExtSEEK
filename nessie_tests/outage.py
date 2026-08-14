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
"""
from __future__ import annotations

# The literal emitted by schema_helper.py's "all fallbacks exhausted" LLMFatalError.
# Do NOT copy this string into another PRODUCTION module: the anti-duplication
# check in tests/test_evaluate.py globs `nessie_tests/*.py` (non-recursive, by
# design) and asserts this is the only one that contains it. The fixtures under
# `nessie_tests/tests/` DO carry their own copies, deliberately — they are what
# fails loudly if the product ever rewords the message.
PROVIDER_OUTAGE_MARKER = "All provider fallbacks exhausted"

# Recorded as the manifest entry's `reason`, so a reader of report.html or of the
# printed summary sees why the case was exempted rather than just an `error`.
OUTAGE_REASON = (
    f"provider outage: the reply carries {PROVIDER_OUTAGE_MARKER!r}; every "
    "provider in the fallback chain returned 503, so no product behaviour ran"
)


def is_provider_outage(*texts: object) -> bool:
    """True if ANY of ``texts`` is a reply produced by an exhausted fallback chain.

    Non-strings (None, an int, a dict left over from a partial payload) are simply
    not outages — a resolver that returns something unexpected must not make the
    detector raise inside the run loop.
    """
    return any(PROVIDER_OUTAGE_MARKER in t for t in texts if isinstance(t, str))
