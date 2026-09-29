from __future__ import annotations

import os
import uuid

THIN_KEYS = {"id", "uuid", "sample_type", "sample_type_description"}

# What `manage.py` resolves to on this project, and what `derive_truth` already uses.
DEFAULT_SETTINGS_MODULE = "dmac.settings"
# A session id no ChatSession can hold (the nil UUID), so the preflight's probe reads
# the real table and the real column and finds nothing.
_PROBE_SESSION_ID = uuid.UUID(int=0)


class BundleReaderUnavailable(RuntimeError):
    """The full tier's bundle reader cannot read in this process.

    Raised by `preflight`, which the runner calls before it sends any turn, so a run
    that meets this has billed nothing.
    """


def _samples(bundle: dict) -> list[dict]:
    for path in (("memory_payload", "data", "samples"), ("api_result_full", "data", "samples")):
        node = bundle
        for k in path:
            node = node.get(k) if isinstance(node, dict) else None
        if isinstance(node, list):
            return node
    gr = (bundle.get("graph_result") or {}).get("data")
    return gr if isinstance(gr, list) else []


def richness_summary(bundle: dict) -> dict:
    samples = _samples(bundle)
    extra = sorted({k for s in samples if isinstance(s, dict) for k in s} - THIN_KEYS)
    return {
        "row_count": len(samples),
        "has_json_metadata": any(bool(s.get("json_metadata")) for s in samples if isinstance(s, dict)),
        "sample_extra_keys": extra,
        "has_extra_keys": bool(extra),
        "memory_payload_null": bundle.get("memory_payload") is None,
    }


def ensure_django() -> None:
    """Configure Django for the bundle reader, unless this process already has.

    `manage.py nessie` runs inside a configured Django, so this does nothing there.
    The module CLI (`python -m NessieAI.tests.nessie_tests`) configures nothing, and
    until plan task 8.4 every full-tier case it ran died on the model import below,
    after its paid turn had already run. Django stays a lazy import: the host lane
    and the route tier never need it.
    """
    try:
        import django
        from django.apps import apps
    except ImportError as exc:
        raise BundleReaderUnavailable(
            f"the bundle is read from the app database through Django, and Django cannot "
            f"be imported here ({type(exc).__name__}: {exc}). Run the full tier inside the "
            f"app container, where `manage.py nessie --tier full` and "
            f"`python -m NessieAI.tests.nessie_tests --tier full` both work.") from exc
    if apps.ready:
        return
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", DEFAULT_SETTINGS_MODULE)
    try:
        django.setup()
    except Exception as exc:
        raise BundleReaderUnavailable(
            f"Django could not be configured with DJANGO_SETTINGS_MODULE="
            f"{os.environ.get('DJANGO_SETTINGS_MODULE')}: {type(exc).__name__}: {exc}") from exc


def read_results_history(session_id) -> list[dict]:
    ensure_django()
    from nextseek_api.assistant.models_db import ChatSession  # lazy: Django only at call time
    return ChatSession.objects.get(session_id=session_id).results_history or []


def summary_for_session(session_id) -> dict | None:
    hist = read_results_history(session_id)
    return richness_summary(hist[-1]) if hist else None


def preflight() -> None:
    """Prove `summary_for_session` can read, before any turn depends on it.

    Configures Django if nothing has, imports the model and reads the same table and
    column the reader reads, for a session that cannot exist. Raises
    BundleReaderUnavailable naming the cause. Free: one read-only query, no turn.
    """
    ensure_django()
    try:
        from nextseek_api.assistant.models_db import ChatSession
        (ChatSession.objects.filter(session_id=_PROBE_SESSION_ID)
         .values_list("results_history", flat=True).first())
    except Exception as exc:
        raise BundleReaderUnavailable(
            f"ChatSession.results_history cannot be read: {type(exc).__name__}: {exc}"
        ) from exc


# The hook the runner looks for (`runner.check_bundle_reader`). It rides on the reader
# itself so that every entry point wiring this reader gets the check without having
# to remember it; the module CLI and `manage.py nessie` both wire this function.
summary_for_session.preflight = preflight


class BundleReaderOtherInstance(BundleReaderUnavailable):
    """The reader can read, but not the database of the instance the turns run on.

    Raised by `runner.check_bundle_reader` when an empty chat it opened on the turns'
    `--base-url` is not in the database this process reads. Every turn of such a run
    bills on that instance and every bundle read here misses, so it is refused before
    the first one.
    """


def session_exists(session_id) -> bool:
    """Whether the database this process reads holds the chat `session_id`.

    The reader's half of the proof that it reads the instance the turns run on: the
    runner opens a chat on `--base-url` and asks this. `preflight` alone cannot tell,
    because it proves only that some database is readable. Free: one read-only query.
    """
    ensure_django()
    from nextseek_api.assistant.models_db import ChatSession
    return ChatSession.objects.filter(session_id=session_id).exists()


# The second hook `runner.check_bundle_reader` looks for, riding on the reader for the
# reason given above `summary_for_session.preflight`.
summary_for_session.holds_session = session_exists
