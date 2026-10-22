"""The Container-CC runtime carries its own copy of the bucket suffix (its image has no nextseek_api); this test,
which blocks in GitHub CI, keeps the two equal (tool spec 8.2)."""
import ast
from pathlib import Path

from nextseek_api.studies.buckets import BUCKET_TITLE_SUFFIX

CLIENT = (Path(__file__).resolve().parents[3]
          / "NessieAI/docker/cc-runtime/build_context/plugins/nextseek/bin/_batch_upload_client.py")


def test_the_cc_client_suffix_equals_the_bucket_rule():
    tree = ast.parse(CLIENT.read_text(encoding="utf-8"))
    values = [node.value.value for node in tree.body
              if isinstance(node, ast.Assign) and len(node.targets) == 1
              and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "BUCKET_TITLE_SUFFIX"
              and isinstance(node.value, ast.Constant)]
    assert values == [BUCKET_TITLE_SUFFIX]
