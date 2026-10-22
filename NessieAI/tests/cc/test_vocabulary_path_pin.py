"""The hook reads the path the engine mounts: /data/turn/vocabulary.json (plan 04, review focus 1)."""
import re

from NessieAI import paths
from NessieAI.cc import cc_engine

HOOK = paths.CC_PLUGIN_DIR / "hooks" / "entity_preamble.sh"


def test_the_hooks_default_path_is_the_engines_mount():
    default = re.search(r"\$\{NEXTSEEK_TURN_VOCABULARY_FILE:-([^}]+)\}", HOOK.read_text()).group(1)
    assert default == f"{cc_engine._CONTAINER_TURN}/{cc_engine.VOCABULARY_FILE}"
