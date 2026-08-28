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
        # No RUN_MAIN guard: gunicorn never sets it, and Django's autoreloader
        # leaves it unset in the parent and "true" in the child — so no test on
        # that variable distinguishes production from the reloader's parent.
        # Warming twice under `runserver` is harmless (twelve pinned documents,
        # on a daemon thread, blocking nothing); skipping it in production
        # would not be.
        if os.environ.get("NEXTSEEK_SKIP_SCHEMA_WARM"):
            return
        try:
            threading.Thread(target=_warm_nfcore_schemas, name="nfcore-schema-warm",
                             daemon=True).start()
        except Exception as exc:  # noqa: BLE001
            logger.warning("nf-core schema warm-up thread could not start: %r", exc)
