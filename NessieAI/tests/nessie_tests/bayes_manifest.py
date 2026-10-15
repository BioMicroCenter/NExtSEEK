"""Alias of NessieAI.nessie_harness.bayes_manifest (same module object; python -m works too)."""
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("NessieAI.nessie_harness.bayes_manifest", run_name="__main__")
else:
    from NessieAI.nessie_harness import bayes_manifest as _m

    sys.modules[__name__] = _m
