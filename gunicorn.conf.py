bind = '0.0.0.0:8000'
workers = 4
timeout = 1200
keepalive = 2
loglevel = 'debug'
limit_request_line = 8190


def post_worker_init(worker):
    """Read the admin graph vocabulary now, on a thread, so no op waits ~20 s for it (each worker has its own cache)."""
    import threading

    def warm():
        from django.conf import settings
        from chat_nextseek import graph_catalog
        from chat_nextseek.graph_scope import GraphScope, with_scope
        graph_catalog.warm(with_scope(settings.NEXTSEEK_CHAT_CONFIG, GraphScope.admin("worker warm")))

    threading.Thread(target=warm, daemon=True).start()
