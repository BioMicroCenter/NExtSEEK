"""Alias of NessieAI.nessie_harness.sources (same module object; python -m works too)."""
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("NessieAI.nessie_harness.sources", run_name="__main__")
else:
    from NessieAI.nessie_harness import sources as _m

    sys.modules[__name__] = _m
