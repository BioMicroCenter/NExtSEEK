from chat_nextseek.config import (
    detect_pipeline_launch_mode,
    build_luria_env,
    luria_env_complete,
)


def test_detect_mode_defaults_to_luria_when_unset():
    assert detect_pipeline_launch_mode({}) == "luria"


def test_detect_mode_reads_and_lowercases_env():
    assert detect_pipeline_launch_mode({"PIPELINE_LAUNCH_MODE": "LURIA"}) == "luria"


def test_detect_mode_rejects_unknown_and_falls_back():
    assert detect_pipeline_launch_mode({"PIPELINE_LAUNCH_MODE": "slurmish"}) == "luria"


def test_build_luria_env_maps_the_four_fields():
    env = {"LURIA_USER": "cdemu", "LURIAKEY": "/k", "LURIA_WORKING_PATH": "/net/x"}
    le = build_luria_env(env)
    assert le == {"user": "cdemu", "key": "/k", "working_path": "/net/x", "host": "luria.mit.edu"}


def test_luria_env_complete_requires_user_key_working_path():
    assert luria_env_complete({"user": "u", "key": "/k", "working_path": "/p", "host": "luria.mit.edu"}) is True
    assert luria_env_complete({"user": "u", "key": None, "working_path": "/p", "host": "luria.mit.edu"}) is False
