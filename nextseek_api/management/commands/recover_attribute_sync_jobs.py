"""Discoverability shim; see `dispatch_attribute_outbox.py` in this same
directory for why this indirection exists. Real implementation:
`nextseek_api/attributes/management/commands/recover_attribute_sync_jobs.py`.
"""
from nextseek_api.attributes.management.commands.recover_attribute_sync_jobs import Command  # noqa: F401
