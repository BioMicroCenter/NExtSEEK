"""Alias of NessieAI.nessie_harness.catalog (same module object)."""
import sys

from NessieAI.nessie_harness import catalog as _m

sys.modules[__name__] = _m
