"""Unit tests for suggest_path. Pure string work, so no Django and no resolver."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ci.gate.live_routes import suggest_path


def test_a_named_group_becomes_a_placeholder():
    assert suggest_path(r"^seek/^sample/id=(?P<id>\d+)/$") == "/seek/sample/id={id}/"


def test_a_detail_route_keeps_its_negated_class_out_of_the_path():
    """'[^/.]+' is how DRF spells a pk; that '^' is not an anchor to strip."""
    assert suggest_path(r"^nextseek_api/^^sops/(?P<pk>[^/.]+)/$") == "/nextseek_api/sops/{pk}/"


def test_the_router_root_is_the_api_root_path():
    assert suggest_path(r"^nextseek_api/^$") == "/nextseek_api/"
