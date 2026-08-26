"""Backwards-compatible shim: the sample table now lives in seek/sample/."""
from .sample import DBtable_sample, SAMPLE_ERRORCODE  # noqa: F401
