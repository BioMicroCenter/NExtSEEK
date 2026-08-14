"""Plan 005 operation registry package."""

from nextseek_api.cc_assistant.op_registry.install_oracle import (
    InstallDiscovery,
    InstallOracleError,
    discover_install,
)
from nextseek_api.cc_assistant.op_registry.plugin_identity import (
    PluginIdentity,
    PluginIdentityError,
    load_and_validate_manifest,
    validate_plugin_identity,
)
from nextseek_api.cc_assistant.op_registry.models import (
    AllowlistSpec,
    ArgSpec,
    Backend,
    GateClass,
    OpList,
    OpSpec,
    ReadSafeEndpoint,
    RouteSpec,
    SkillRow,
    Transport,
)
from nextseek_api.cc_assistant.op_registry.ops import OPS
from nextseek_api.cc_assistant.op_registry.routes import (
    CONTAINER_CC_ROUTE,
    GENERIC_CC_BUILTINS,
)

__all__ = [
    "AllowlistSpec",
    "ArgSpec",
    "Backend",
    "CONTAINER_CC_ROUTE",
    "GENERIC_CC_BUILTINS",
    "GateClass",
    "InstallDiscovery",
    "InstallOracleError",
    "PluginIdentity",
    "PluginIdentityError",
    "OPS",
    "OpList",
    "OpSpec",
    "ReadSafeEndpoint",
    "RouteSpec",
    "SkillRow",
    "Transport",
    "discover_install",
    "load_and_validate_manifest",
    "validate_plugin_identity",
]
