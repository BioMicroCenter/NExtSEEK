"""Discoverability shim; see `dispatch_attribute_outbox.py` in this same
directory for why this indirection exists. Real implementation:
`nextseek_api/attributes/management/commands/check_attribute_outbox_heartbeat.py`.
"""
from nextseek_api.attributes.management.commands.check_attribute_outbox_heartbeat import Command  # noqa: F401
