"""classify_tool_use is the single tool classifier shared by 1c summary + trace."""
from nextseek_api.cc_assistant.cc_summary import classify_tool_use


def test_classify_bash_write_edit_read_skill_other():
    assert classify_tool_use({"name": "Bash", "input": {"command": "ls"}}) == ("bash", "Bash", "ls")
    assert classify_tool_use({"name": "Write", "input": {"file_path": "/x.md"}}) == ("write", "Write", "/x.md")
    assert classify_tool_use({"name": "MultiEdit", "input": {"file_path": "/y"}}) == ("edit", "MultiEdit", "/y")
    assert classify_tool_use({"name": "Read", "input": {"file_path": "/z"}}) == ("read", "Read", "/z")
    assert classify_tool_use({"name": "Task", "input": {"subagent_type": "Explore"}}) == ("skill", "Task", "Explore")
    assert classify_tool_use({"name": "WebFetch", "input": {}}) == ("tool", "WebFetch", None)
