"""The tool's time check uses the server's numbers and model-op list (plan 04, piece 4)."""
import ast
import re

from NessieAI import paths
from NessieAI.ns import op_limits

BIN = paths.CC_PLUGIN_DIR / "bin"


def test_the_tools_numbers_are_the_servers():
    text = (BIN / "_turn_deadline.py").read_text()
    assert float(re.search(r"^MIN_USABLE_S: float = ([0-9.]+)", text, re.M).group(1)) == op_limits.MIN_USABLE_S
    assert float(re.search(r"^TURN_DEADLINE_HEADROOM_S: float = ([0-9.]+)", text, re.M).group(1)) \
        == op_limits.ANSWER_RESERVE_S


def test_the_no_model_floor_is_the_tools_wait_floor():
    """SC-9: Django gives a no-model op at least what the tool waits for it (MIN_WAIT_S)."""
    text = (BIN / "_turn_deadline.py").read_text()
    assert float(re.search(r"^MIN_WAIT_S: float = ([0-9.]+)", text, re.M).group(1)) == op_limits.NO_MODEL_FLOOR_S


def test_the_tools_model_ops_are_the_servers_plus_the_three_nested_turns():
    tree = ast.parse((BIN / "_nextseek_runner.py").read_text())
    (node,) = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
               and any(getattr(t, "id", None) == "_MODEL_AGENTS" for t in n.targets)]
    agents = set(ast.literal_eval(node.value.args[0]))
    assert agents == set(op_limits.MODEL_OPS) | {"query", "plan", "pipeline"}
