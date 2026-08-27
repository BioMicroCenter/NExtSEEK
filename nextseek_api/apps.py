import logging
import os
import threading

from django.apps import AppConfig

logger = logging.getLogger(__name__)


def _warm_nfcore_schemas() -> None:
    """Prefetch the pinned nf-core schemas the pipeline selector reads.

    Guarded end to end: a vendoring gap, a missing atlas, or no outbound
    network must degrade selection, never stop Django booting.
    """
    try:
        from chat_nextseek.seqera.nfcore_schema import warm_cache

        failures = warm_cache()
    except Exception as exc:  # noqa: BLE001
        logger.warning("nf-core schema warm-up unavailable: %r", exc)
        return
    if failures:
        logger.warning("nf-core schema warm-up incomplete: %s", failures)
    else:
        logger.info("nf-core schema warm-up complete")


class NextseekApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'nextseek_api'

    def ready(self):
        # RUN_MAIN is set in the autoreloader's child process; without this the
        # dev server warms twice. Skipped entirely under tests and migrations,
        # where the network is neither available nor wanted.
        if os.environ.get("RUN_MAIN") == "false":
            return
        if os.environ.get("NEXTSEEK_SKIP_SCHEMA_WARM"):
            return
        threading.Thread(target=_warm_nfcore_schemas, name="nfcore-schema-warm",
                         daemon=True).start()
