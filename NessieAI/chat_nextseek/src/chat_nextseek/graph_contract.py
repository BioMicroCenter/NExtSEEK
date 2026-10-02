"""chat_nextseek's one door to the NExtSEEK graph contract, ``nextseek_graph/schema.py`` at the checkout root.

Every chat_nextseek module that needs a graph name imports ``schema`` from here, never ``nextseek_graph`` itself:

1. ``from nextseek_graph import schema``, which works wherever the checkout root is on ``sys.path`` (every app
   process, the test lanes, GitHub CI);
2. when ``nextseek_graph`` itself cannot be imported (any other import error propagates), ``schema.py`` is loaded by
   its path from the checkout this file sits in, under a private module name. ``sys.path`` is never edited and
   ``nextseek_graph`` is never registered, so a process holds one copy of the contract. This is how the evaluator
   runs, with its working directory inside ``NessieAI/chat_nextseek``;
3. when neither works, ImportError names both places and the remedy.

A non-editable or git install of chat_nextseek has no checkout around it, so there only step 1 can work.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_PRIVATE_NAME = "_chat_nextseek_graph_contract"


def _schema_file() -> Path | None:
    """``nextseek_graph/schema.py`` in the checkout around this file, or None when the file sits too high for one.

    graph_contract.py -> chat_nextseek -> src -> chat_nextseek (the project) -> NessieAI -> the checkout root.
    """
    parents = Path(__file__).resolve().parents
    return parents[4] / "nextseek_graph" / "schema.py" if len(parents) > 4 else None


def _load() -> ModuleType:
    try:
        from nextseek_graph import schema as contract
        return contract
    except ModuleNotFoundError as exc:
        if exc.name != "nextseek_graph":
            raise
    schema_file = _schema_file()
    if schema_file is not None and schema_file.is_file():
        spec = importlib.util.spec_from_file_location(_PRIVATE_NAME, schema_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    looked = f"{schema_file} does not exist" if schema_file else f"no checkout is around {Path(__file__).resolve()}"
    raise ImportError(
        "chat_nextseek needs the NExtSEEK graph contract, nextseek_graph/schema.py. It is not importable as "
        f"nextseek_graph, and {looked}. Run with the NExtSEEK checkout root on sys.path (the working directory, or "
        "PYTHONPATH), or, in a container, use an app image built from a commit that has nextseek_graph/.")


schema = _load()

__all__ = ("schema",)
