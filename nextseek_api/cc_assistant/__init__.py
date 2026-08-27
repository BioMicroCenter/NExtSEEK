"""Additive Container-Claude-Code (CC) assistant for NExtSEEK.

This subpackage exposes dmac_assistant's distinctive capabilities — the BAML
LLM **router** and the sandboxed **Container Claude Code** path — as NEW
nextseek_api endpoints, WITHOUT touching the existing ``nextseek_api.assistant``
module (which wraps chat_nextseek and must stay untouched).

Design (see plans/dmac-nextseek-integration-2026-05-29.md):

* A new ViewSet (``services.cc_assistant.CCAssistantViewSet``) accepts a query,
  asks the router (``router.decide``) whether it is a deterministic NExtSEEK
  lookup (NS route -> in-process ``chat_nextseek.run_query``, identical to the
  existing assistant) or an open-ended agentic task (CC route -> a per-request
  sandboxed ``claude`` container via ``cc_engine``).
* Both routes drive the SAME ``QueryTask`` + ``make_db_event_callback``
  ``{event, data}`` progress contract, so the EXISTING
  ``TaskProgressConsumer`` websocket streams them to the unchanged
  ``chat_frontend``. No new consumer or websocket route is required.
"""
