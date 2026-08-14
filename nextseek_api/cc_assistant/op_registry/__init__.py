"""Plan 005 operation registry package."""

from nextseek_api.cc_assistant.op_registry.install_oracle import (
    InstallDiscovery,
    InstallOracleError,
    discover_install,
)

__all__ = [
    "InstallDiscovery",
    "InstallOracleError",
    "discover_install",
]
