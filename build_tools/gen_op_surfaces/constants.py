"""Shared constants for deterministic surface generation."""
from __future__ import annotations

EXIT_NO_CHANGE = 0
EXIT_ERROR = 1
EXIT_CHANGES_WRITTEN = 2

CANONICAL_CAPABILITIES_REL = (
    "chat_nextseek/src/chat_nextseek/context/capabilities.md"
)
BAKED_CAPABILITIES_REL = (
    "docker/cc-runtime/build_context/plugins/nextseek/context/capabilities.md"
)
ROUTE_CAPABILITIES_REL = "dmac_assistant/build_context/route_capabilities.json"

COMMAND_OPS_BEGIN = "<!-- BEGIN PLAN005-GEN:command-ops -->"
COMMAND_OPS_END = "<!-- END PLAN005-GEN:command-ops -->"

SKILL_OPS_BEGIN = "<!-- BEGIN PLAN005-GEN:skill-ops -->"
SKILL_OPS_END = "<!-- END PLAN005-GEN:skill-ops -->"

DOCKERFILE_REL = "docker/cc-runtime/Dockerfile"
COMPOSE_REL = "docker-compose.yml"

PLUGIN_COPY_BEGIN = "# BEGIN PLAN005-GEN:plugin-copy"
PLUGIN_COPY_END = "# END PLAN005-GEN:plugin-copy"
PLUGIN_PATH_BEGIN = "# BEGIN PLAN005-GEN:plugin-path"
PLUGIN_PATH_END = "# END PLAN005-GEN:plugin-path"
CAPABILITIES_COPY_BEGIN = "# BEGIN PLAN005-GEN:capabilities-copy"
CAPABILITIES_COPY_END = "# END PLAN005-GEN:capabilities-copy"
ADDITIONAL_CONTEXTS_BEGIN = "# BEGIN PLAN005-GEN:additional-contexts"
ADDITIONAL_CONTEXTS_END = "# END PLAN005-GEN:additional-contexts"

CLAUDE_MD_REL = "docker/cc-runtime/container/CLAUDE.md"
CONTENT_HASH_REL = "docker/cc-runtime/docs/nextseek/.content-hash"
NEXTSEEK_DOCS_PIN_REF = "a9d69522"

CLAUDE_PLUGINS_BEGIN = "<!-- BEGIN PLAN005-GEN:plugins -->"
CLAUDE_PLUGINS_END = "<!-- END PLAN005-GEN:plugins -->"
CLAUDE_SKILLS_BEGIN = "<!-- BEGIN PLAN005-GEN:skills -->"
CLAUDE_SKILLS_END = "<!-- END PLAN005-GEN:skills -->"
CLAUDE_OPS_BEGIN = "<!-- BEGIN PLAN005-GEN:operations -->"
CLAUDE_OPS_END = "<!-- END PLAN005-GEN:operations -->"

NEXTSEEK_DOCS_BEGIN = "<!-- BEGIN NEXTSEEK-DOCS (auto-generated) -->"
NEXTSEEK_DOCS_END = "<!-- END NEXTSEEK-DOCS (auto-generated) -->"

NAMED_CAPABILITIES_CONTEXT = "chat_nextseek"
CANONICAL_CAPABILITIES_IN_CONTEXT = (
    "src/chat_nextseek/context/capabilities.md"
)
IMAGE_CAPABILITIES_PATH = "/app/plugins/nextseek/context/capabilities.md"

SKILL_OPS_FIELDS = (
    "op_id",
    "bin_name",
    "purpose",
    "transport",
    "gate_class",
    "availability",
    "per_op_gate_enabled",
)

MARKER_PREFIX = "<!-- BEGIN PLAN005-GEN:"
MARKER_SUFFIX = "-->"
