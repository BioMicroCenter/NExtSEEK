"""Discoverability shim; see `dispatch_attribute_outbox.py` in this same
directory for why this indirection exists. Real implementation:
`nextseek_api/assay_registration/management/commands/run_assay_registration_jobs.py`.
"""
from nextseek_api.assay_registration.management.commands.run_assay_registration_jobs import Command  # noqa: F401
