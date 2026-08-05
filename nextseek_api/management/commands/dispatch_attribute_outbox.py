"""Discoverability shim: Django's per-app management-command scan only walks
each `INSTALLED_APPS` entry's own filesystem path
(`django.core.management.get_commands`). `nextseek_api.attributes` is a
subpackage of the `nextseek_api` app, not a separate `AppConfig`, so
`manage.py dispatch_attribute_outbox` resolves here; the real implementation
(the Section-7 Modified-Files-named module) lives at
`nextseek_api/attributes/management/commands/dispatch_attribute_outbox.py`.
"""
from nextseek_api.attributes.management.commands.dispatch_attribute_outbox import Command  # noqa: F401
