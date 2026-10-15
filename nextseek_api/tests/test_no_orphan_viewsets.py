"""Viewsets nothing routes were deleted; keep them gone."""
import nextseek_api.views as views


def test_unrouted_viewsets_are_gone():
    assert not hasattr(views, "NHPViewSet")
    assert not hasattr(views, "SampleQueryViewSet")
