from chat_nextseek.config import detect_pipeline_launch_mode


def test_default_launch_mode_is_luria():
    assert detect_pipeline_launch_mode({}) == "luria"


def test_invalid_launch_mode_falls_back_to_luria():
    assert detect_pipeline_launch_mode({"PIPELINE_LAUNCH_MODE": "bogus"}) == "luria"


def test_explicit_tower_still_honored():
    assert detect_pipeline_launch_mode({"PIPELINE_LAUNCH_MODE": "tower"}) == "tower"
