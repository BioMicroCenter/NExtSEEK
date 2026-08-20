"""Plan 005 operation registry package.

Submodule imports of ``install_oracle`` and ``plugin_identity`` must remain
stdlib-only: this package ``__init__`` must not eagerly import pydantic models.
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "AllowlistSpec",
    "ArgSpec",
    "Backend",
    "CONTAINER_CC_ROUTE",
    "GENERIC_CC_BUILTINS",
    "NEXTSEEK_QUERY_TOOLS",
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

_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "AllowlistSpec": (".models", "AllowlistSpec"),
    "ArgSpec": (".models", "ArgSpec"),
    "Backend": (".models", "Backend"),
    "CONTAINER_CC_ROUTE": (".routes", "CONTAINER_CC_ROUTE"),
    "GENERIC_CC_BUILTINS": (".routes", "GENERIC_CC_BUILTINS"),
    "NEXTSEEK_QUERY_TOOLS": (".routes", "NEXTSEEK_QUERY_TOOLS"),
    "GateClass": (".models", "GateClass"),
    "InstallDiscovery": (".install_oracle", "InstallDiscovery"),
    "InstallOracleError": (".install_oracle", "InstallOracleError"),
    "PluginIdentity": (".plugin_identity", "PluginIdentity"),
    "PluginIdentityError": (".plugin_identity", "PluginIdentityError"),
    "OPS": (".ops", "OPS"),
    "OpList": (".models", "OpList"),
    "OpSpec": (".models", "OpSpec"),
    "ReadSafeEndpoint": (".models", "ReadSafeEndpoint"),
    "RouteSpec": (".models", "RouteSpec"),
    "SkillRow": (".models", "SkillRow"),
    "Transport": (".models", "Transport"),
    "discover_install": (".install_oracle", "discover_install"),
    "load_and_validate_manifest": (".plugin_identity", "load_and_validate_manifest"),
    "validate_plugin_identity": (".plugin_identity", "validate_plugin_identity"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = target
    value = getattr(importlib.import_module(module_name, __name__), attr)
    globals()[name] = value
    return value
